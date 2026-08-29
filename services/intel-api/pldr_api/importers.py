from __future__ import annotations

import asyncio
import hashlib
import html as html_lib
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .extraction import canonicalize_url, content_hash, extract_page, normalize_text
from .models import Document, Snapshot, Source
from .security import validate_public_http_url


REDIRECT_STATUSES = {301, 302, 303, 307, 308}
DEFAULT_MAX_FETCH_BYTES = 5 * 1024 * 1024


class ResponseTooLargeError(ValueError):
    """Raised before unbounded response content can enter the intake path."""


class UnsupportedContentTypeError(ValueError):
    """Raised when a public URL returns non-text material."""


class UnsupportedContentEncodingError(ValueError):
    """Raised before HTTPX can inflate an unbounded compressed response."""


@dataclass(frozen=True)
class FetchedPublicText:
    resolved_url: str
    text: str
    status_code: int
    media_type: str
    size_bytes: int


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
            return await _fetch_public_text_response(
                url,
                timeout_seconds=timeout_seconds,
                max_redirects=max_redirects,
                max_bytes=max_bytes,
            )
    except TimeoutError as exc:
        raise httpx.ReadTimeout(
            f"Fetch exceeded {total_timeout_seconds:g} second total deadline"
        ) from exc


async def _fetch_public_text_response(
    url: str,
    *,
    timeout_seconds: int,
    max_redirects: int,
    max_bytes: int,
) -> FetchedPublicText:
    current = canonicalize_url(url)
    async with httpx.AsyncClient(
        timeout=timeout_seconds,
        follow_redirects=False,
        headers={"User-Agent": "PLDR-P0/0.1", "Accept-Encoding": "identity"},
    ) as client:
        for _ in range(max_redirects + 1):
            validate_public_http_url(current)
            # Real httpx clients are streamed so the limit is enforced while bytes arrive.
            # The buffered fallback keeps the small test doubles used by the P0 suite compatible.
            if callable(getattr(client, "stream", None)):
                async with client.stream("GET", current) as response:
                    if response.status_code in REDIRECT_STATUSES:
                        location = response.headers.get("location")
                        if not location:
                            raise ValueError("Redirect response is missing Location header")
                        current = canonicalize_url(urljoin(current, location))
                        validate_public_http_url(current)
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
                response = await client.get(current)
                if response.status_code in REDIRECT_STATUSES:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("Redirect response is missing Location header")
                    current = canonicalize_url(urljoin(current, location))
                    validate_public_http_url(current)
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
    raise ValueError(f"Too many redirects (>{max_redirects})")


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

    page = extract_page(html, fallback_title=title or "")
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
        published_at=now,
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
