from __future__ import annotations

import asyncio
import hashlib
import html as html_lib
import ipaddress
import os
import json
import socket
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse, urlsplit, urlunsplit
from xml.etree import ElementTree

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .extraction import assess_extraction, canonicalize_url, content_hash, extract_page, normalize_text
from .models import Document, Snapshot, Source
from .security import validate_public_http_url
from .security import UnsafeUrlError


REDIRECT_STATUSES = {301, 302, 303, 307, 308}
DEFAULT_MAX_FETCH_BYTES = 5 * 1024 * 1024


class ResponseTooLargeError(ValueError):
    """Raised before unbounded response content can enter the intake path."""


class UnsupportedContentTypeError(ValueError):
    """Raised when a public URL returns non-text material."""


class UnsupportedContentEncodingError(ValueError):
    """Raised before HTTPX can inflate an unbounded compressed response."""


class RedirectLimitError(ValueError):
    """Raised when the safe direct fetch cannot finish a redirect chain."""


class ReaderFallbackError(RuntimeError):
    """Raised when both the direct fetch and the optional public reader fail."""

    def __init__(self, message: str, *, direct_error: Exception, reader_error: Exception) -> None:
        self.direct_error = direct_error
        self.reader_error = reader_error
        super().__init__(message)


class UnsafeRedirectUrlError(UnsafeUrlError):
    """An unsafe redirect must never be delegated to a remote reader."""


@dataclass(frozen=True)
class FetchedPublicText:
    resolved_url: str
    text: str
    status_code: int
    media_type: str
    size_bytes: int
    fetch_method: str = "direct_http"
    metadata: dict[str, object] = field(default_factory=dict)


def _reader_fallback_enabled() -> bool:
    return os.getenv("PLDR_READER_FALLBACK_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _reader_trusted_dns_enabled() -> bool:
    return bool(os.getenv("PLDR_READER_VALIDATION_DOH_URL", "").strip())


def _reader_fallback_allowed(exc: Exception) -> bool:
    # A poisoned or synthetic local DNS answer must never weaken direct-fetch
    # pinning. An explicitly configured HTTPS DNS resolver may, however,
    # independently validate a public target before a remote Reader fetch.
    if isinstance(exc, UnsafeRedirectUrlError):
        return False
    if isinstance(exc, UnsafeUrlError):
        return _reader_trusted_dns_enabled()
    if isinstance(exc, RedirectLimitError):
        return True
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {403, 408, 425, 429} or exc.response.status_code >= 500
    return False


def _configured_https_url(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    if not value:
        return None
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            f"{name} must be an HTTPS URL without credentials, query, or fragment"
        )
    return value


def _configured_reader_proxy() -> str | None:
    value = os.getenv("PLDR_READER_PROXY_URL", "").strip()
    if not value:
        return None
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "PLDR_READER_PROXY_URL must be an HTTP(S) URL without credentials, query, or fragment"
        )
    return value


def _validated_doh_addresses(payloads: list[object], host: str) -> list[str]:
    addresses: list[str] = []
    for payload in payloads:
        if not isinstance(payload, dict) or payload.get("Status") != 0:
            raise UnsafeUrlError(f"Trusted DNS could not validate host: {host}")
        answers = payload.get("Answer")
        if not isinstance(answers, list):
            continue
        for answer in answers:
            if not isinstance(answer, dict):
                continue
            try:
                address = ipaddress.ip_address(str(answer.get("data", "")).strip())
            except ValueError:
                continue
            if not address.is_global:
                raise UnsafeUrlError(f"Non-public address is blocked: {address}")
            rendered = str(address)
            if rendered not in addresses:
                addresses.append(rendered)
    if not addresses:
        raise UnsafeUrlError(f"Trusted DNS returned no public address for host: {host}")
    return addresses


async def _validate_reader_target(url: str, *, timeout_seconds: float) -> None:
    validate_public_http_url(url, resolve=False)
    parsed = urlsplit(url)
    assert parsed.hostname is not None
    try:
        literal = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        literal = None
    if literal is not None:
        if not literal.is_global:
            raise UnsafeUrlError(f"Non-public address is blocked: {literal}")
        return

    resolver_url = _configured_https_url("PLDR_READER_VALIDATION_DOH_URL")
    if resolver_url is None:
        validate_public_http_url(url)
        return
    proxy = _configured_reader_proxy()
    async with httpx.AsyncClient(
        timeout=min(max(timeout_seconds, 1.0), 10.0),
        follow_redirects=False,
        trust_env=False,
        proxy=proxy,
    ) as client:
        responses = await asyncio.gather(*(
            client.get(
                resolver_url,
                params={"name": parsed.hostname, "type": record_type},
                headers={"Accept": "application/dns-json"},
            )
            for record_type in ("A", "AAAA")
        ))
    payloads: list[object] = []
    for response in responses:
        response.raise_for_status()
        if len(response.content) > 64 * 1024:
            raise UnsafeUrlError("Trusted DNS response exceeds 65536 byte limit")
        try:
            payloads.append(response.json())
        except json.JSONDecodeError as exc:
            raise UnsafeUrlError("Trusted DNS returned invalid JSON") from exc
    _validated_doh_addresses(payloads, parsed.hostname)


def _direct_fetch_timeout(requested: float) -> float:
    configured = float(os.getenv("PLDR_DIRECT_FETCH_TIMEOUT_SECONDS", "12"))
    if configured <= 0:
        raise ValueError("PLDR_DIRECT_FETCH_TIMEOUT_SECONDS must be positive")
    return min(requested, configured)


def _is_text_media_type(media_type: str) -> bool:
    if media_type.startswith("text/"):
        return True
    if media_type in {
        "application/json",
        "application/ld+json",
        "application/xml",
        "application/xhtml+xml",
        "application/rss+xml",
        "application/atom+xml",
    }:
        return True
    return media_type.endswith("+json") or media_type.endswith("+xml")


def _response_media_type(response: object, max_bytes: int) -> str:
    headers = getattr(response, "headers", {}) or {}
    media_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if not media_type or not _is_text_media_type(media_type):
        raise UnsupportedContentTypeError(
            f"Unsupported or missing text content type: {media_type or 'missing'}"
        )
    raw_length = headers.get("content-length")
    if raw_length:
        try:
            declared_length = int(raw_length)
        except (TypeError, ValueError):
            declared_length = None
        if declared_length is not None and declared_length > max_bytes:
            raise ResponseTooLargeError(
                f"Response exceeds {max_bytes} byte limit (Content-Length {declared_length})"
            )
    return media_type


def _validate_identity_content_encoding(response: object) -> None:
    headers = getattr(response, "headers", {}) or {}
    encoding = headers.get("content-encoding", "").strip().lower()
    if encoding and encoding != "identity":
        raise UnsupportedContentEncodingError(
            f"Compressed response encoding is not supported by the bounded fetcher: {encoding}"
        )


def _decode_text(content: bytes, response: object) -> str:
    encoding = getattr(response, "encoding", None) or "utf-8"
    try:
        return content.decode(encoding, errors="replace")
    except LookupError:
        return content.decode("utf-8", errors="replace")


def _pinned_public_destination(url: str) -> tuple[str, str, str]:
    """Resolve once, validate every answer, and return an IP-pinned request URL."""
    validate_public_http_url(url, resolve=False)
    parsed = urlsplit(url)
    assert parsed.hostname is not None
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        answers = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"Unable to resolve host: {parsed.hostname}") from exc
    addresses: list[str] = []
    for answer in answers:
        address = answer[4][0]
        literal = ipaddress.ip_address(address)
        if not literal.is_global:
            raise UnsafeUrlError(f"Non-public address is blocked: {literal}")
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise UnsafeUrlError(f"Unable to resolve host: {parsed.hostname}")
    address = addresses[0]
    pinned_host = f"[{address}]" if ":" in address else address
    explicit_port = parsed.port is not None
    pinned_netloc = f"{pinned_host}:{port}" if explicit_port else pinned_host
    host_header = parsed.hostname
    if explicit_port:
        host_header = f"{host_header}:{port}"
    pinned_url = urlunsplit((parsed.scheme, pinned_netloc, parsed.path, parsed.query, ""))
    return pinned_url, host_header, parsed.hostname


def source_id_for(name: str, host: str) -> str:
    return "src_import_" + hashlib.sha1(f"{name}:{host}".encode("utf-8")).hexdigest()[:12]


def document_id_for(url: str, digest: str) -> str:
    return "doc_import_" + hashlib.sha1(f"{url}:{digest}".encode("utf-8")).hexdigest()[:14]


def get_or_create_source(session: Session, name: str, url: str, language: str) -> Source:
    parsed = urlparse(url)
    host = parsed.hostname or "unknown"
    source_id = source_id_for(name, host)
    source = session.get(Source, source_id)
    if source is None:
        source = Source(
            id=source_id,
            name=name,
            base_url=f"{parsed.scheme}://{host}",
            country="",
            language=language,
            source_type="imported",
            reliability_tier=4,
            independence_group=host,
            status="healthy",
            last_success_at=datetime.now(timezone.utc),
        )
        session.add(source)
        session.flush()
    return source


async def fetch_public_text_response(
    url: str,
    *,
    timeout_seconds: int = 20,
    max_redirects: int = 5,
    max_bytes: int | None = None,
    total_timeout_seconds: float | None = None,
    prefer_readable_html: bool = True,
) -> FetchedPublicText:
    """Fetch bounded public text with redirect checks and one wall-clock deadline."""
    if max_bytes is None:
        max_bytes = int(os.getenv("PLDR_MAX_FETCH_BYTES", str(DEFAULT_MAX_FETCH_BYTES)))
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    if total_timeout_seconds is None:
        total_timeout_seconds = float(os.getenv("PLDR_FETCH_TOTAL_TIMEOUT_SECONDS", "30"))
    if total_timeout_seconds <= 0:
        raise ValueError("total_timeout_seconds must be positive")
    try:
        async with asyncio.timeout(total_timeout_seconds):
            try:
                direct_timeout = min(
                    _direct_fetch_timeout(timeout_seconds),
                    max(0.1, total_timeout_seconds / 2),
                )
                try:
                    async with asyncio.timeout(direct_timeout):
                        direct = await _fetch_public_text_response(
                            url,
                            timeout_seconds=direct_timeout,
                            max_redirects=max_redirects,
                            max_bytes=max_bytes,
                        )
                except TimeoutError as exc:
                    raise httpx.ReadTimeout(
                        f"Direct fetch exceeded {direct_timeout:g} second deadline"
                    ) from exc
                if (
                    prefer_readable_html
                    and "html" in direct.media_type
                    and _reader_fallback_enabled()
                ):
                    quality = assess_extraction(
                        extract_page(direct.text, url=direct.resolved_url)
                    )
                    if quality.status != "usable":
                        try:
                            return await _fetch_reader_html_response(
                                url, timeout_seconds=timeout_seconds, max_bytes=max_bytes
                            )
                        except Exception as reader_error:
                            direct_error = ValueError(
                                "Extracted page body is not usable: "
                                + ", ".join(quality.reasons)
                            )
                            raise ReaderFallbackError(
                                "Direct extraction quality check and reader fallback both failed: "
                                f"{direct_error}; reader: {reader_error}",
                                direct_error=direct_error,
                                reader_error=reader_error,
                            ) from reader_error
                return direct
            except Exception as direct_error:
                if not _reader_fallback_enabled() or not _reader_fallback_allowed(direct_error):
                    raise
                try:
                    return await _fetch_reader_html_response(
                        url,
                        timeout_seconds=timeout_seconds,
                        max_bytes=max_bytes,
                    )
                except Exception as reader_error:
                    if isinstance(direct_error, UnsafeUrlError) and isinstance(
                        reader_error, UnsafeUrlError
                    ):
                        raise direct_error
                    raise ReaderFallbackError(
                        "Direct fetch and reader fallback both failed: "
                        f"{direct_error}; reader: {reader_error}",
                        direct_error=direct_error,
                        reader_error=reader_error,
                    ) from reader_error
    except TimeoutError as exc:
        raise httpx.ReadTimeout(
            f"Fetch exceeded {total_timeout_seconds:g} second total deadline"
        ) from exc


async def _fetch_reader_html_response(
    url: str,
    *,
    timeout_seconds: int,
    max_bytes: int,
) -> FetchedPublicText:
    """Fetch rendered public HTML through an explicitly enabled Jina Reader adapter."""
    target = canonicalize_url(url)
    await _validate_reader_target(target, timeout_seconds=timeout_seconds)
    base_url = os.getenv("PLDR_READER_BASE_URL", "https://r.jina.ai").strip().rstrip("/")
    parsed_base = urlsplit(base_url)
    if (
        parsed_base.scheme != "https"
        or not parsed_base.hostname
        or parsed_base.username
        or parsed_base.password
        or parsed_base.query
        or parsed_base.fragment
    ):
        raise ValueError(
            "PLDR_READER_BASE_URL must be an HTTPS URL without credentials, query, or fragment"
        )
    api_key = os.getenv("PLDR_READER_API_KEY", "").strip()
    proxy = _configured_reader_proxy()
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "X-Respond-With": "html",
        "User-Agent": "PLDR-P0/0.1 reader-fallback",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    async with httpx.AsyncClient(
        timeout=timeout_seconds,
        follow_redirects=False,
        trust_env=False,
        proxy=proxy,
    ) as client:
        async with client.stream("GET", f"{base_url}/{target}", headers=headers) as response:
            response.raise_for_status()
            _validate_identity_content_encoding(response)
            _response_media_type(response, max_bytes)
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes(chunk_size=min(64 * 1024, max_bytes + 1)):
                total += len(chunk)
                if total > max_bytes:
                    raise ResponseTooLargeError(
                        f"Reader response exceeds {max_bytes} byte limit ({total} bytes received)"
                    )
                chunks.append(chunk)
    try:
        payload = json.loads(b"".join(chunks).decode("utf-8"))
        data = payload.get("data") if isinstance(payload, dict) else None
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Reader fallback returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("Reader fallback returned no document data")
    rendered_html = data.get("html")
    if not isinstance(rendered_html, str) or not rendered_html.strip():
        raise ValueError("Reader fallback returned no rendered HTML")
    rendered_size = len(rendered_html.encode("utf-8"))
    if rendered_size > max_bytes:
        raise ResponseTooLargeError(
            f"Rendered page exceeds {max_bytes} byte limit ({rendered_size} bytes received)"
        )
    resolved_url = canonicalize_url(str(data.get("url") or target))
    await _validate_reader_target(resolved_url, timeout_seconds=timeout_seconds)
    upstream_status = data.get("httpStatus")
    if isinstance(upstream_status, int) and upstream_status >= 400:
        raise ValueError(f"Reader upstream returned HTTP {upstream_status}")
    return FetchedPublicText(
        resolved_url=resolved_url,
        text=rendered_html,
        status_code=int(upstream_status) if isinstance(upstream_status, int) else 200,
        media_type="text/html",
        size_bytes=rendered_size,
        fetch_method="jina_reader",
        metadata={
            "title": data.get("title"),
            "published_at": data.get("publishedTime"),
            "description": data.get("description"),
        },
    )


async def _fetch_public_text_response(
    url: str,
    *,
    timeout_seconds: int,
    max_redirects: int,
    max_bytes: int,
) -> FetchedPublicText:
    initial = canonicalize_url(url)
    current = initial
    async with httpx.AsyncClient(
        timeout=timeout_seconds,
        follow_redirects=False,
        trust_env=False,
        limits=httpx.Limits(max_keepalive_connections=0),
        headers={"User-Agent": "PLDR-P0/0.1", "Accept-Encoding": "identity"},
    ) as client:
        for _ in range(max_redirects + 1):
            try:
                pinned_url, host_header, sni_hostname = await asyncio.to_thread(
                    _pinned_public_destination, current
                )
            except UnsafeUrlError as exc:
                if current != initial:
                    raise UnsafeRedirectUrlError(str(exc)) from exc
                raise
            # Real httpx clients are streamed so the limit is enforced while bytes arrive.
            # The buffered fallback keeps the small test doubles used by the P0 suite compatible.
            if callable(getattr(client, "stream", None)):
                async with client.stream(
                    "GET",
                    pinned_url,
                    headers={"Host": host_header},
                    extensions={"sni_hostname": sni_hostname},
                ) as response:
                    if response.status_code in REDIRECT_STATUSES:
                        location = response.headers.get("location")
                        if not location:
                            raise ValueError("Redirect response is missing Location header")
                        current = canonicalize_url(urljoin(current, location))
                        validate_public_http_url(current, resolve=False)
                        continue
                    response.raise_for_status()
                    _validate_identity_content_encoding(response)
                    media_type = _response_media_type(response, max_bytes)
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes(
                        chunk_size=min(64 * 1024, max_bytes + 1)
                    ):
                        total += len(chunk)
                        if total > max_bytes:
                            raise ResponseTooLargeError(
                                f"Response exceeds {max_bytes} byte limit ({total} bytes received)"
                            )
                        chunks.append(chunk)
                    content = b"".join(chunks)
                    return FetchedPublicText(
                        resolved_url=current,
                        text=_decode_text(content, response),
                        status_code=response.status_code,
                        media_type=media_type,
                        size_bytes=total,
                    )
            else:  # pragma: no cover - exercised by compatibility doubles in test_p0
                response = await client.get(
                    pinned_url,
                    headers={"Host": host_header},
                    extensions={"sni_hostname": sni_hostname},
                )
                if response.status_code in REDIRECT_STATUSES:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("Redirect response is missing Location header")
                    current = canonicalize_url(urljoin(current, location))
                    validate_public_http_url(current, resolve=False)
                    continue
                response.raise_for_status()
                _validate_identity_content_encoding(response)
                media_type = _response_media_type(response, max_bytes)
                content = getattr(response, "content", None)
                if content is None:
                    content = response.text.encode("utf-8")
                if len(content) > max_bytes:
                    raise ResponseTooLargeError(
                        f"Response exceeds {max_bytes} byte limit ({len(content)} bytes)"
                    )
                return FetchedPublicText(
                    resolved_url=current,
                    text=_decode_text(content, response),
                    status_code=response.status_code,
                    media_type=media_type,
                    size_bytes=len(content),
                )
    raise RedirectLimitError(f"Too many redirects (>{max_redirects})")


async def fetch_public_text(
    url: str,
    *,
    timeout_seconds: int = 20,
    max_redirects: int = 5,
    max_bytes: int | None = None,
) -> tuple[str, str]:
    """Compatibility wrapper used by the existing P0.3 intake paths."""
    fetched = await fetch_public_text_response(
        url,
        timeout_seconds=timeout_seconds,
        max_redirects=max_redirects,
        max_bytes=max_bytes,
    )
    return fetched.resolved_url, fetched.text


async def import_url_document(
    session: Session,
    url: str,
    source_name: str | None,
    title: str | None,
    html: str | None,
    language: str,
) -> Document:
    canonical_url = canonicalize_url(url)
    validate_public_http_url(canonical_url, resolve=html is None)

    existing = session.scalar(select(Document).where(Document.canonical_url == canonical_url))
    if existing:
        return existing

    resolved_url = canonical_url
    if html is None:
        resolved_url, html = await fetch_public_text(canonical_url)
        resolved_url = canonicalize_url(resolved_url)
        if resolved_url != canonical_url:
            existing = session.scalar(select(Document).where(Document.canonical_url == resolved_url))
            if existing:
                return existing
            canonical_url = resolved_url

    page = extract_page(html, fallback_title=title or "", url=canonical_url)
    if len(page.body) < 40:
        raise ValueError("Extracted page body is too short")

    digest = content_hash(page.body)
    duplicate = session.scalar(
        select(Document).where(Document.content_hash == digest).order_by(Document.fetched_at.asc())
    )

    parsed = urlparse(canonical_url)
    source = get_or_create_source(
        session,
        source_name or parsed.hostname or "Imported source",
        canonical_url,
        language,
    )
    now = datetime.now(timezone.utc)
    metadata = {
        "imported": True,
        "requested_url": url,
        "resolved_url": resolved_url,
    }
    if duplicate is not None:
        metadata["duplicate_of_document_id"] = duplicate.id

    document = Document(
        id=document_id_for(canonical_url, digest),
        source_id=source.id,
        canonical_url=canonical_url,
        title=page.title,
        body=page.body,
        published_at=page.published_at or now,
        fetched_at=now,
        language=language,
        content_hash=digest,
        upstream_story_id="",
        is_cached=False,
        metadata_json=metadata,
    )
    session.add(document)
    session.flush()
    session.add(
        Snapshot(
            id="snap_" + document.id.removeprefix("doc_"),
            document_id=document.id,
            captured_at=now,
            content_hash=digest,
            excerpt=page.body[:3000],
            storage_path="inline-import",
            metadata_json={
                "confirmation_stage": "direct-import",
                "title": page.title,
                "title_known": bool(page.title),
                "published_at": now.isoformat().replace("+00:00", "Z"),
                "published_at_known": False,
                "language": language,
                "source_description": source_name,
                "source_url": url,
                "canonical_url": canonical_url,
            },
        )
    )
    source.last_success_at = now
    source.status = "healthy"
    source.last_error = None
    session.commit()
    session.refresh(document)
    return document


def element_text(node: ElementTree.Element | None, default: str = "") -> str:
    if node is None:
        return default
    return normalize_text("".join(node.itertext()))


def find_first(node: ElementTree.Element, *paths: str) -> ElementTree.Element | None:
    """Return the first existing Element without relying on Element truthiness."""
    for path in paths:
        found = node.find(path)
        if found is not None:
            return found
    return None


async def import_rss(
    session: Session,
    url: str | None,
    xml: str | None,
    source_name: str,
    language: str,
) -> list[Document]:
    if not xml:
        if not url:
            raise ValueError("RSS url or xml is required")
        _, xml = await fetch_public_text(url)

    root = ElementTree.fromstring(xml)
    items = root.findall(".//item")
    if not items:
        items = root.findall(".//{http://www.w3.org/2005/Atom}entry")

    imported: list[Document] = []
    for item in items[:50]:
        title_node = find_first(item, "title", "{http://www.w3.org/2005/Atom}title")
        link_node = find_first(item, "link", "{http://www.w3.org/2005/Atom}link")
        description_node = find_first(
            item,
            "description",
            "summary",
            "{http://www.w3.org/2005/Atom}summary",
        )

        link = element_text(link_node)
        if link_node is not None and not link and link_node.attrib.get("href"):
            link = link_node.attrib["href"]
        title_text = element_text(title_node, "Untitled RSS item")
        description = element_text(description_node, title_text)

        if not link:
            link = f"https://example.org/.well-known/pldr-rss/{hashlib.sha1(title_text.encode()).hexdigest()[:12]}"

        safe_title = html_lib.escape(title_text)
        safe_description = html_lib.escape(description)
        synthetic_html = (
            f"<html><head><title>{safe_title}</title></head>"
            f"<body><article><p>{safe_description}</p></article></body></html>"
        )
        try:
            imported.append(
                await import_url_document(
                    session,
                    link,
                    source_name,
                    title_text,
                    synthetic_html,
                    language,
                )
            )
        except Exception:
            session.rollback()
    return imported
