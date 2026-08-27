from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup


TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gclid", "fbclid"}


@dataclass(frozen=True)
class ExtractedPage:
    title: str
    body: str


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key.lower() not in TRACKING_PARAMS]
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path.rstrip("/") or "/", urlencode(query), ""))


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def extract_page(html: str, fallback_title: str = "") -> ExtractedPage:
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
            candidates.append(node.get_text(" ", strip=True))
    if not candidates:
        paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all(["p", "h1", "h2", "li"])]
        candidates.append(" ".join(part for part in paragraphs if len(part) >= 25))
    body = max((normalize_text(item) for item in candidates), key=len, default="")
    if len(body) < 40:
        body = normalize_text(soup.get_text(" ", strip=True))
    return ExtractedPage(title=title or "Untitled document", body=body)
