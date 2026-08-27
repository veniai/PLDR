from __future__ import annotations

import hashlib
import html as html_lib
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


async def fetch_public_text(url: str, *, timeout_seconds: int = 20, max_redirects: int = 5) -> tuple[str, str]:
    """Fetch public HTTP(S) text while validating every redirect hop."""
    current = canonicalize_url(url)
    async with httpx.AsyncClient(
        timeout=timeout_seconds,
        follow_redirects=False,
        headers={"User-Agent": "PLDR-P0/0.1"},
    ) as client:
        for _ in range(max_redirects + 1):
            validate_public_http_url(current)
            response = await client.get(current)
            if response.status_code in REDIRECT_STATUSES:
                location = response.headers.get("location")
                if not location:
                    raise ValueError("Redirect response is missing Location header")
                current = canonicalize_url(urljoin(current, location))
                validate_public_http_url(current)
                continue
            response.raise_for_status()
            return current, response.text
    raise ValueError(f"Too many redirects (>{max_redirects})")


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
