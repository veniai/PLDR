from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from trafilatura import extract as trafilatura_extract
from trafilatura import extract_metadata


TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gclid", "fbclid"}


@dataclass(frozen=True)
class ExtractedPage:
    title: str
    body: str
    published_at: datetime | None = None
    author: str | None = None
    site_name: str | None = None
    canonical_url: str | None = None
    extraction_method: str = "trafilatura"


@dataclass(frozen=True)
class ParagraphSpan:
    id: str
    text: str
    start_offset: int
    end_offset: int


@dataclass(frozen=True)
class ExtractionQuality:
    status: str
    reasons: tuple[str, ...]
    text_chars: int
    paragraph_count: int
    link_ratio: float


MIN_USABLE_BODY_CHARS = 80
ERROR_PAGE_MARKERS = (
    "access denied",
    "403 forbidden",
    "captcha",
    "verify you are human",
    "页面不存在",
    "访问验证",
    "安全验证",
)


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key.lower() not in TRACKING_PARAMS]
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path.rstrip("/") or "/", urlencode(query), ""))


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def normalize_structured_text(text: str) -> str:
    """Normalize each line without destroying evidence-addressable structure."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = [normalize_text(block) for block in re.split(r"\n\s*\n+", normalized)]
    return "\n\n".join(block for block in blocks if block).strip()


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def paragraph_spans(text: str) -> list[ParagraphSpan]:
    spans: list[ParagraphSpan] = []
    for match in re.finditer(r"(?:^|(?<=\n\n))[^\n]+(?:\n(?!\n)[^\n]+)*", text):
        raw = match.group(0)
        leading = len(raw) - len(raw.lstrip())
        trailing = len(raw) - len(raw.rstrip())
        cleaned = raw.strip()
        if not cleaned:
            continue
        start = match.start() + leading
        end = match.end() - trailing
        spans.append(
            ParagraphSpan(
                id=f"P{len(spans) + 1:03d}",
                text=cleaned,
                start_offset=start,
                end_offset=end,
            )
        )
    return spans


def paragraph_id_for_offset(text: str, start_offset: int, end_offset: int) -> str | None:
    for paragraph in paragraph_spans(text):
        if start_offset >= paragraph.start_offset and end_offset <= paragraph.end_offset:
            return paragraph.id
    return None


def assess_extraction(page: ExtractedPage) -> ExtractionQuality:
    body = page.body or ""
    plain = re.sub(r"!?\[([^\]]*)\]\([^)]+\)", r"\1", body)
    plain = normalize_text(re.sub(r"[#*_>`~]", " ", plain))
    lowered = plain[:1200].lower()
    reasons: list[str] = []
    if len(plain) < MIN_USABLE_BODY_CHARS:
        reasons.append("body_too_short")
    if any(marker in lowered for marker in ERROR_PAGE_MARKERS):
        reasons.append("error_or_challenge_page")
    link_chars = sum(len(match.group(0)) for match in re.finditer(r"\[[^\]]*\]\([^)]+\)", body))
    link_ratio = link_chars / max(len(body), 1)
    if len(body) >= 500 and link_ratio > 0.55:
        reasons.append("navigation_or_link_heavy")
    paragraphs = paragraph_spans(body)
    if len(plain) >= 500 and len(paragraphs) < 2:
        reasons.append("missing_paragraph_structure")
    return ExtractionQuality(
        status="usable" if not reasons else "needs_fallback",
        reasons=tuple(reasons),
        text_chars=len(plain),
        paragraph_count=len(paragraphs),
        link_ratio=round(link_ratio, 4),
    )


def _parse_extracted_date(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    cleaned = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _reasonable_site_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = normalize_text(value)
    if not cleaned or len(cleaned) > 160:
        return None
    lowered = cleaned.lower()
    if "copyright" in lowered or "all rights reserved" in lowered:
        return None
    return cleaned


def _legacy_extract_page(html: str, fallback_title: str = "") -> ExtractedPage:
    """Keep a conservative fallback for small or unusual HTML fixtures/pages."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "svg", "canvas", "nav", "footer", "aside"]):
        tag.decompose()
    title = fallback_title
    if not title and soup.title and soup.title.string:
        title = normalize_text(soup.title.string)
    candidates: list[str] = []
    for selector in ["article", "main", "[role=main]"]:
        node = soup.select_one(selector)
        if node:
            blocks = [
                child.get_text(" ", strip=True)
                for child in node.find_all(["p", "h1", "h2", "li"])
                if child.get_text(" ", strip=True)
            ]
            candidates.append("\n\n".join(blocks) if blocks else node.get_text("\n", strip=True))
    if not candidates:
        paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all(["p", "h1", "h2", "li"])]
        candidates.append("\n".join(part for part in paragraphs if len(part) >= 25))
    body = max((normalize_structured_text(item) for item in candidates), key=len, default="")
    if len(normalize_text(body)) < 40:
        body = normalize_structured_text(soup.get_text("\n", strip=True))
    return ExtractedPage(title=title or "Untitled document", body=body, extraction_method="legacy")


def extract_page(html: str, fallback_title: str = "", url: str | None = None) -> ExtractedPage:
    cleaned_soup = BeautifulSoup(html, "lxml")
    for tag in cleaned_soup(["script", "style", "noscript", "svg", "canvas", "nav", "footer", "aside"]):
        tag.decompose()
    cleaned_html = str(cleaned_soup)
    body = trafilatura_extract(
        cleaned_html,
        url=url,
        output_format="markdown",
        include_comments=False,
        include_links=False,
        include_images=False,
        include_tables=True,
        deduplicate=True,
    )
    metadata = extract_metadata(html, default_url=url) if html.strip() else None
    values = metadata.as_dict() if metadata is not None else {}
    structured = normalize_structured_text(body or "")
    if len(normalize_text(structured)) < 40:
        legacy = _legacy_extract_page(html, fallback_title=fallback_title)
        return ExtractedPage(
            title=legacy.title,
            body=legacy.body,
            published_at=_parse_extracted_date(values.get("date")),
            author=normalize_text(str(values.get("author"))) if values.get("author") else None,
            site_name=_reasonable_site_name(values.get("sitename") or values.get("hostname")),
            canonical_url=str(values.get("url")) if values.get("url") else url,
            extraction_method="legacy",
        )
    legacy = _legacy_extract_page(cleaned_html, fallback_title=fallback_title)
    if (
        len(paragraph_spans(structured)) < 2
        and len(paragraph_spans(legacy.body)) >= 2
        and near_duplicate_similarity(structured, legacy.body) >= 0.9
    ):
        structured = legacy.body
        extraction_method = "trafilatura+dom_structure"
    else:
        extraction_method = "trafilatura"
    extracted_title = normalize_text(str(values.get("title") or ""))
    return ExtractedPage(
        title=extracted_title or fallback_title or "Untitled document",
        body=structured,
        published_at=_parse_extracted_date(values.get("date")),
        author=normalize_text(str(values.get("author"))) if values.get("author") else None,
        site_name=_reasonable_site_name(values.get("sitename") or values.get("hostname")),
        canonical_url=str(values.get("url")) if values.get("url") else url,
        extraction_method=extraction_method,
    )


def near_duplicate_similarity(first: str, second: str) -> float:
    """Return conservative character-shingle similarity for repost grouping."""
    def shingles(value: str) -> set[str]:
        normalized = re.sub(r"[^0-9a-z\u3400-\u4dbf\u4e00-\u9fff]+", "", value.lower())
        if len(normalized) < 5:
            return {normalized} if normalized else set()
        return {normalized[index:index + 5] for index in range(len(normalized) - 4)}

    left, right = shingles(first), shingles(second)
    if not left or not right:
        return 0.0
    intersection = len(left & right)
    jaccard = intersection / len(left | right)
    containment = intersection / min(len(left), len(right))
    first_length = len(normalize_text(first))
    second_length = len(normalize_text(second))
    length_ratio = min(first_length, second_length) / max(first_length, second_length)
    return max(jaccard, containment * length_ratio)
