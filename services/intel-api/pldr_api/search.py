from __future__ import annotations

import hashlib
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse, urlsplit

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from .errors import ArchivedIntakeError, IntakeMutationConflictError
from .extraction import assess_extraction, canonicalize_url, extract_page, normalize_text
from .importers import fetch_public_text
from .intake import (
    extracted_material_metadata,
    generate_candidates,
    iso,
    lock_intake_for_mutation,
    submit_web_intake,
)
from .models import (
    IntakeItem,
    Investigation,
    InvestigationLink,
    SearchQueryRun,
    SearchResult,
    SearchSelection,
    SearchSelectionEvent,
)
from .schemas import ExternalSearchRequest, ExternalSearchSelectionRequest


MAX_LOADED_RESULTS = 100


_TOPIC_RELEVANCE_STOP_TERMS = {
    "about",
    "and",
    "from",
    "latest",
    "news",
    "the",
    "with",
    "事件",
    "什么",
    "公开",
    "关于",
    "哪些",
    "当前",
    "是否",
    "材料",
    "相关",
    "资料",
}


def _relevance_chunks(value: str) -> list[str]:
    """Return visible query/topic chunks without pretending to do NLP."""
    normalized = normalize_text(value or "").casefold()
    return [
        chunk
        for chunk in re.split(r"[^\w\u3400-\u9fff]+", normalized)
        if len(chunk) >= 2 and chunk not in _TOPIC_RELEVANCE_STOP_TERMS
    ]


def _relevance_terms(*values: str) -> tuple[list[str], list[str]]:
    anchors: list[str] = []
    concepts: list[str] = []
    for value in values:
        for chunk in _relevance_chunks(value):
            if chunk not in anchors:
                anchors.append(chunk)
            for han_run in re.findall(r"[\u3400-\u9fff]{2,16}", chunk):
                for index in range(len(han_run) - 1):
                    term = han_run[index : index + 2]
                    if term not in _TOPIC_RELEVANCE_STOP_TERMS and term not in concepts:
                        concepts.append(term)
            for term in re.findall(r"[a-z][a-z0-9_-]{2,}", chunk):
                if term not in _TOPIC_RELEVANCE_STOP_TERMS and term not in concepts:
                    concepts.append(term)
    return anchors, concepts


def assess_topic_relevance(
    result: SearchResult,
    investigation: Investigation | None,
) -> dict[str, Any]:
    """Explain a conservative title/snippet relevance pre-screen.

    This is intentionally not a truth or source-quality score.  It only keeps
    broad search hits out of the human pending queue until an analyst selects
    them explicitly.
    """
    if investigation is None:
        return {
            "level": "unknown",
            "label": "尚未初筛",
            "matched_terms": [],
            "reason": "查询没有专题上下文，需人工判断是否相关。",
        }
    anchors, _ = _relevance_terms(result.query_run.keyword, investigation.title)
    _, concepts = _relevance_terms(
        result.query_run.keyword,
        investigation.title,
        getattr(investigation, "question", ""),
    )
    title = normalize_text(result.title or "").casefold()
    context = normalize_text(f"{result.title or ''} {result.snippet or ''}").casefold()
    title_anchor_hits = [term for term in anchors if term in title]
    context_anchor_hits = [term for term in anchors if term in context]
    title_concept_hits = [term for term in concepts if term in title]
    context_concept_hits = [term for term in concepts if term in context]
    single_anchor_query = len(_relevance_chunks(result.query_run.keyword)) == 1
    strong_title_match = (
        any(len(term) >= 4 for term in title_anchor_hits)
        or len(title_concept_hits) >= 3
        or (single_anchor_query and bool(title_anchor_hits))
    )
    matched = list(
        dict.fromkeys(
            title_anchor_hits
            + title_concept_hits
            + context_anchor_hits
            + context_concept_hits
        )
    )[:6]
    publication_time = result.published_at or _explicit_result_publication_time(
        result.title,
        result.snippet,
    )
    event_start = investigation.event_start_at
    if publication_time is not None and event_start is not None:
        if publication_time.tzinfo is None:
            publication_time = publication_time.replace(tzinfo=timezone.utc)
        if event_start.tzinfo is None:
            event_start = event_start.replace(tzinfo=timezone.utc)
        if publication_time.astimezone(timezone.utc) < event_start.astimezone(timezone.utc):
            return {
                "level": "uncertain",
                "label": "时间范围待核对",
                "matched_terms": matched,
                "reason": (
                    f"材料发布于 {publication_time.date().isoformat()}，早于专题事件范围 "
                    f"{event_start.date().isoformat()}；保留在线索列表，不自动进入待处理。"
                ),
                "time_scope": {
                    "status": "published_before_event_start",
                    "published_at": iso(publication_time),
                    "event_start_at": iso(event_start),
                },
            }
    if strong_title_match:
        title_matched = list(dict.fromkeys(title_anchor_hits + title_concept_hits))[:4]
        return {
            "level": "likely",
            "label": "与专题相关",
            "matched_terms": matched,
            "reason": f"标题命中专题词：{'、'.join(title_matched)}。",
        }
    if context_anchor_hits or len(context_concept_hits) >= 2:
        return {
            "level": "uncertain",
            "label": "相关性存疑",
            "matched_terms": matched,
            "reason": "摘要涉及专题，但标题不足以确认；保留在线索列表，由用户决定是否处理。",
        }
    return {
        "level": "unlikely",
        "label": "可能无关",
        "matched_terms": [],
        "reason": "标题和摘要未命中专题词；保留在线索列表，默认不进入待处理。",
    }


ERROR_PRESENTATION: dict[str, dict[str, Any]] = {
    "not_configured": {
        "code": "search.not_configured",
        "summary": "搜索服务尚未配置",
        "why": "当前运行环境没有可用的搜索服务配置。",
        "impact": "本次查询没有产生新资料，已有查询和档案不受影响。",
        "retryable": False,
        "recommended_action": "请管理员检查搜索服务地址和凭据后再试。",
    },
    "authentication_failed": {
        "code": "search.authentication_failed",
        "summary": "搜索服务拒绝了访问凭据",
        "why": "搜索服务认为当前凭据无效或无权调用该接口。",
        "impact": "本次查询没有产生新资料，已有查询和档案不受影响。",
        "retryable": False,
        "recommended_action": "请管理员更新搜索服务凭据。",
    },
    "rate_limited": {
        "code": "search.rate_limited",
        "summary": "搜索服务暂时限流",
        "why": "短时间查询次数超过了上游服务当前允许的额度。",
        "impact": "本次查询没有新增结果，已加载结果仍然保留。",
        "retryable": True,
        "recommended_action": "请稍后重试当前页，不需要重新建立专题。",
    },
    "timeout": {
        "code": "search.timeout",
        "summary": "搜索服务响应超时",
        "why": "上游搜索服务没有在限定时间内返回结果。",
        "impact": "本次查询没有新增结果，已加载结果仍然保留。",
        "retryable": True,
        "recommended_action": "请稍后重试当前页；连续失败时检查搜索服务健康状态。",
    },
    "network_error": {
        "code": "search.unreachable",
        "summary": "暂时无法连接搜索服务",
        "why": "PLDR 与上游搜索服务之间的网络连接失败。",
        "impact": "本次查询没有新增结果，已加载结果仍然保留。",
        "retryable": True,
        "recommended_action": "请重试；连续失败时检查网络或搜索服务状态。",
    },
    "format_disabled": {
        "code": "search.format_disabled",
        "summary": "搜索服务没有开放 JSON 结果",
        "why": "当前 SearXNG 实例未启用 PLDR 所需的 JSON 输出格式。",
        "impact": "PLDR 无法读取本次搜索结果。",
        "retryable": False,
        "recommended_action": "请管理员在 SearXNG 中启用 JSON search format。",
    },
    "invalid_response": {
        "code": "search.invalid_response",
        "summary": "搜索服务返回了无法读取的数据",
        "why": "上游响应不是 PLDR 支持的搜索结果格式。",
        "impact": "本次查询没有新增结果，已加载结果仍然保留。",
        "retryable": True,
        "recommended_action": "请重试；连续失败时检查上游版本和适配器配置。",
    },
    "invalid_query": {
        "code": "search.invalid_query",
        "summary": "无法继续这次查询",
        "why": "查询条件、页码或已保存的查询上下文不一致。",
        "impact": "没有发起新的外部请求，已加载结果不受影响。",
        "retryable": False,
        "recommended_action": "请重新打开原查询并从建议的下一页继续。",
    },
    "backend_error": {
        "code": "search.upstream_error",
        "summary": "搜索服务暂时出错",
        "why": "上游搜索服务返回了错误状态。",
        "impact": "本次查询没有新增结果，已加载结果仍然保留。",
        "retryable": True,
        "recommended_action": "请稍后重试当前页。",
    },
    "concurrent_update": {
        "code": "search.concurrent_update",
        "summary": "同一页正在被另一请求更新",
        "why": "另一个请求同时提交了这次查询的同一页。",
        "impact": "没有覆盖已保存结果；另一请求可能已经完成该页。",
        "retryable": True,
        "recommended_action": "重新打开查询；若该页尚未出现，再重试一次。",
    },
}


class ExternalSearchError(RuntimeError):
    query_run_id: str | None = None
    attempted_page: int | None = None

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 502,
        reason: str = "backend_error",
        upstream_status: int | None = None,
        retry_after: str | None = None,
        stage: str = "search",
    ):
        super().__init__(message)
        self.status_code = status_code
        self.reason = reason
        self.upstream_status = upstream_status
        self.retry_after = retry_after
        self.stage = stage
        self.trace_id = "search_" + uuid.uuid4().hex[:16]

    def as_dict(self) -> dict[str, Any]:
        presentation = ERROR_PRESENTATION.get(
            self.reason, ERROR_PRESENTATION["backend_error"]
        )
        summary = str(presentation["summary"])
        action = str(presentation["recommended_action"])
        technical = str(self)
        return {
            "code": presentation["code"],
            "stage": self.stage,
            "summary": summary,
            "title": summary,
            "message": summary,
            "why": presentation["why"],
            "impact": presentation["impact"],
            "retryable": bool(presentation["retryable"]),
            "recommended_action": action,
            "next_action": action,
            "technical_message": technical,
            "technical_detail": technical,
            "trace_id": self.trace_id,
            "upstream_status": self.upstream_status,
            "retry_after": self.retry_after,
            # Compatibility fields for pre-workspace clients.
            "reason": self.reason,
            "query_run_id": self.query_run_id,
            "attempted_page": self.attempted_page,
        }


@dataclass(frozen=True)
class SearchProviderConfig:
    provider: str
    base_url: str
    api_key: str
    timeout_seconds: float

    @classmethod
    def from_env(cls) -> "SearchProviderConfig":
        provider = os.getenv("PLDR_SEARCH_PROVIDER", "brave").strip().lower()
        if provider not in {"brave", "searxng"}:
            raise ExternalSearchError(
                f"Unsupported search provider: {provider}", status_code=503, reason="not_configured"
            )
        default_base_url = (
            "https://api.search.brave.com" if provider == "brave" else "http://127.0.0.1:8888"
        )
        return cls(
            provider=provider,
            base_url=os.getenv("PLDR_SEARCH_BASE_URL", default_base_url).strip().rstrip("/"),
            api_key=os.getenv("PLDR_SEARCH_API_KEY", "").strip(),
            timeout_seconds=float(os.getenv("PLDR_SEARCH_TIMEOUT_SECONDS", "12")),
        )


PROVIDER_METADATA: dict[str, dict[str, str]] = {
    "brave": {
        "component": "Brave Search API",
        "version": "REST v1",
        "license": "Brave Search API Terms of Service",
        "deployment_boundary": "External Brave SaaS; operator supplies PLDR_SEARCH_API_KEY; PLDR stores no key",
    },
    "searxng": {
        "component": "SearXNG metasearch",
        "version": "2026.8.22",
        "license": "AGPL-3.0-or-later",
        "deployment_boundary": "Operator-managed JSON-enabled instance; PLDR calls it as a thin search adapter",
    },
}


@dataclass(frozen=True)
class SearchHit:
    original_url: str
    canonical_url: str
    fingerprint: str
    site_name: str
    title: str
    snippet: str
    published_at: datetime | None
    engine: str
    raw_result: dict[str, Any]


@dataclass(frozen=True)
class BackendSearchResponse:
    provider: str
    channel: str
    hits: list[SearchHit]
    has_more: bool | None = None
    total_estimate: int | None = None


def _safe_text(value: Any, limit: int) -> str:
    if value is None:
        return ""
    text = BeautifulSoup(str(value), "lxml").get_text(" ", strip=True)
    return normalize_text(text)[:limit]


def _parse_public_result_url(value: Any) -> tuple[str, str, str, str]:
    if not isinstance(value, str) or not value.strip():
        raise ExternalSearchError("Search backend returned a result without a URL")
    url = value.strip()
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ExternalSearchError(f"Search backend returned a non-HTTP(S) URL: {url[:160]}")
    canonical = canonicalize_url(url)
    if len(url) > 900 or len(canonical) > 900:
        raise ExternalSearchError("Search backend returned an over-length result URL")
    host = (urlparse(canonical).hostname or "").lower()
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return url, canonical, fingerprint, host


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        # Ranking backends commonly return relative ages. These are display-only and must
        # not be converted into an invented publication timestamp.
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


_EXPLICIT_RESULT_DATE_PATTERNS = (
    # Search backends sometimes omit their date field even though the result
    # snippet starts with a publisher dateline, for example
    # ``【某媒体2026年08月12日讯】``.  Only accept an explicit publication
    # marker; a bare date in an article summary may be the event date instead.
    re.compile(
        r"(?P<year>20\d{2})\s*年\s*(?P<month>\d{1,2})\s*月\s*"
        r"(?P<day>\d{1,2})\s*日\s*(?:讯|消息|报道|发布|更新)"
    ),
    re.compile(
        r"(?:发布(?:日期|时间)?|发布于|更新(?:日期|时间)?|更新于)\s*[:：]?\s*"
        r"(?P<year>20\d{2})[-/.年]\s*(?P<month>\d{1,2})[-/.月]\s*"
        r"(?P<day>\d{1,2})(?:日)?"
    ),
    re.compile(
        r"(?:published|updated|posted)\s*(?:on)?\s*[:：]?\s*"
        r"(?P<year>20\d{2})[-/.]\s*(?P<month>\d{1,2})[-/.]\s*"
        r"(?P<day>\d{1,2})",
        re.IGNORECASE,
    ),
)


def _explicit_result_publication_time(*values: str) -> datetime | None:
    """Read only clearly labelled publication datelines from result text."""
    text = normalize_text(" ".join(value for value in values if value))[:600]
    for pattern in _EXPLICIT_RESULT_DATE_PATTERNS:
        match = pattern.search(text)
        if match is None:
            continue
        try:
            return datetime(
                int(match.group("year")),
                int(match.group("month")),
                int(match.group("day")),
                tzinfo=timezone.utc,
            )
        except ValueError:
            continue
    return None


def _normalize_hit(raw: dict[str, Any], *, provider: str, engine: str | None = None) -> SearchHit:
    original_url, canonical_url, fingerprint, host = _parse_public_result_url(raw.get("url"))
    meta_url = raw.get("meta_url") if isinstance(raw.get("meta_url"), dict) else {}
    site_name = _safe_text(
        raw.get("source") or meta_url.get("hostname") or host,
        200,
    ) or host
    title = _safe_text(raw.get("title"), 500) or "Untitled search result"
    snippet = _safe_text(raw.get("description") or raw.get("content"), 2000)
    published_at = _parse_datetime(
        raw.get("publishedDate") or raw.get("page_age") or raw.get("timestamp")
    ) or _explicit_result_publication_time(title, snippet)
    return SearchHit(
        original_url=original_url,
        canonical_url=canonical_url,
        fingerprint=fingerprint,
        site_name=site_name,
        title=title,
        snippet=snippet,
        published_at=published_at,
        engine=_safe_text(engine or raw.get("engine") or provider, 120),
        raw_result=raw,
    )


def _nested_results(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for section in ("web", "news"):
        value = payload.get(section)
        if isinstance(value, dict) and isinstance(value.get("results"), list):
            return [item for item in value["results"] if isinstance(item, dict)]
    value = payload.get("results")
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def effective_search_language(keyword: str, language: str, provider: str) -> str:
    """Resolve the UI's ``auto`` language before making an upstream request.

    This is intentionally small and deterministic. It prevents the former UI
    default from labelling an obviously Chinese query as English while avoiding
    an opaque language-detection dependency in the evidence path.
    """
    cleaned = language.strip() or "auto"
    normalized = cleaned.lower().replace("_", "-")
    if normalized == "auto":
        normalized = (
            "zh" if re.search(r"[\u3400-\u4dbf\u4e00-\u9fff]", keyword) else "en"
        )
    simplified = {"zh", "zh-cn", "zh-sg", "zh-hans"}
    traditional = {"zh-tw", "zh-hk", "zh-mo", "zh-hant"}
    if provider == "brave":
        if normalized in simplified:
            return "zh-hans"
        if normalized in traditional:
            return "zh-hant"
    elif provider == "searxng":
        if normalized in simplified:
            return "zh-CN"
        if normalized in traditional:
            return "zh-TW"
    return cleaned if cleaned.lower() != "auto" else "en"


def _response_retry_after(response: Any) -> str | None:
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    value = headers.get("Retry-After")
    return str(value)[:120] if value else None


async def request_brave_search(
    config: SearchProviderConfig, request: ExternalSearchRequest
) -> BackendSearchResponse:
    if not config.api_key:
        raise ExternalSearchError(
            "Brave Search API is not configured; set PLDR_SEARCH_API_KEY",
            status_code=503,
            reason="not_configured",
        )
    channel = f"brave-search-api:{request.scope}"
    # Brave's API dashboard labels the operation as POST /v1/{scope}/search, but the
    # deployed service root is /res. A request without that resource prefix is rejected
    # by the edge before parameter or credential validation.
    base_url = config.base_url.rstrip("/")
    resource_root = base_url if base_url.endswith("/res") else f"{base_url}/res"
    endpoint = f"{resource_root}/v1/{request.scope}/search"
    page_size = request.effective_page_size
    effective_language = effective_search_language(
        request.keyword, request.language, "brave"
    )
    payload: dict[str, Any] = {
        "q": request.keyword,
        "count": page_size,
        # Brave's offset is a zero-based *page count*, not a row offset.
        "offset": request.page - 1,
        "search_lang": effective_language,
        "country": "ALL",
        "safesearch": "strict",
        "spellcheck": False,
    }
    if request.scope == "web":
        payload["result_filter"] = ["web"]
        payload["text_decorations"] = False
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Subscription-Token": config.api_key,
    }
    try:
        async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
            response = await client.post(endpoint, json=payload, headers=headers)
    except httpx.TimeoutException as exc:
        raise ExternalSearchError(
            f"Brave Search API timed out after {config.timeout_seconds:g}s",
            status_code=504,
            reason="timeout",
        ) from exc
    except httpx.HTTPError as exc:
        raise ExternalSearchError(f"Brave Search API is unreachable: {exc}", reason="network_error") from exc
    if response.status_code in {401, 403}:
        raise ExternalSearchError(
            "Brave Search API rejected the configured credential",
            status_code=response.status_code,
            reason="authentication_failed",
            upstream_status=response.status_code,
        )
    if response.status_code == 429:
        raise ExternalSearchError(
            "Brave Search API rate limit reached",
            status_code=429,
            reason="rate_limited",
            upstream_status=429,
            retry_after=_response_retry_after(response),
        )
    if response.status_code >= 400:
        raise ExternalSearchError(
            f"Brave Search API returned HTTP {response.status_code}",
            status_code=502,
            reason="backend_error",
            upstream_status=response.status_code,
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise ExternalSearchError("Brave Search API returned invalid JSON", reason="invalid_response") from exc
    if not isinstance(payload, dict):
        raise ExternalSearchError("Brave Search API response must be a JSON object", reason="invalid_response")
    hits = [_normalize_hit(item, provider="brave") for item in _nested_results(payload)]
    query_metadata = payload.get("query") if isinstance(payload.get("query"), dict) else {}
    explicit_more = query_metadata.get("more_results_available")
    has_more = explicit_more if isinstance(explicit_more, bool) else len(hits) >= page_size
    return BackendSearchResponse("brave", channel, hits[:page_size], has_more=has_more)


async def request_searxng_search(
    config: SearchProviderConfig, request: ExternalSearchRequest
) -> BackendSearchResponse:
    if not config.base_url:
        raise ExternalSearchError(
            "SearXNG is not configured; set PLDR_SEARCH_BASE_URL",
            status_code=503,
            reason="not_configured",
        )
    channel = f"searxng:{request.scope}"
    page_size = request.effective_page_size
    effective_language = effective_search_language(
        request.keyword, request.language, "searxng"
    )
    params = {
        "q": request.keyword,
        "format": "json",
        "categories": "news" if request.scope == "news" else "general",
        "language": effective_language,
        "pageno": request.page,
        "safesearch": 1,
    }
    try:
        async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
            response = await client.get(f"{config.base_url}/search", params=params)
    except httpx.TimeoutException as exc:
        raise ExternalSearchError(
            f"SearXNG timed out after {config.timeout_seconds:g}s", status_code=504, reason="timeout"
        ) from exc
    except httpx.HTTPError as exc:
        raise ExternalSearchError(f"SearXNG is unreachable: {exc}", reason="network_error") from exc
    if response.status_code == 403:
        raise ExternalSearchError(
            "SearXNG rejected the JSON format; enable json in its search.formats setting",
            status_code=502,
            reason="format_disabled",
            upstream_status=403,
        )
    if response.status_code == 429:
        raise ExternalSearchError(
            "SearXNG rate limit reached",
            status_code=429,
            reason="rate_limited",
            upstream_status=429,
            retry_after=_response_retry_after(response),
        )
    if response.status_code >= 400:
        raise ExternalSearchError(
            f"SearXNG returned HTTP {response.status_code}",
            status_code=502,
            reason="backend_error",
            upstream_status=response.status_code,
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise ExternalSearchError("SearXNG returned invalid JSON", reason="invalid_response") from exc
    if not isinstance(payload, dict):
        raise ExternalSearchError("SearXNG response must be a JSON object", reason="invalid_response")
    raw_results = payload.get("results")
    raw_results = raw_results if isinstance(raw_results, list) else []
    hits = [
        _normalize_hit(item, provider="searxng")
        for item in raw_results
        if isinstance(item, dict)
    ]
    estimate = payload.get("number_of_results")
    total_estimate = (
        estimate
        if isinstance(estimate, int) and not isinstance(estimate, bool) and estimate >= 0
        else None
    )
    # SearXNG controls its own results-per-page setting. PLDR sends the real
    # ``pageno`` and applies its display cap locally; its reported total is only
    # an estimate, never an assertion that PLDR loaded the entire result set.
    # SearXNG owns the actual results-per-page setting. Many operator-managed
    # instances return ten rows even when the PLDR workspace asks to display
    # twenty, so a non-empty page must remain continuable. The executor below
    # also stops when a later page adds no unique rows.
    has_more = bool(hits)
    return BackendSearchResponse(
        "searxng",
        channel,
        hits,
        has_more=has_more,
        total_estimate=total_estimate,
    )


async def request_search(
    config: SearchProviderConfig, request: ExternalSearchRequest
) -> BackendSearchResponse:
    if config.provider == "brave":
        return await request_brave_search(config, request)
    return await request_searxng_search(config, request)


def provider_metadata(historical_provider: str | None = None) -> dict[str, Any]:
    try:
        config = SearchProviderConfig.from_env()
        provider = historical_provider or config.provider
        configured = (
            provider == config.provider
            and bool(config.base_url)
            and (config.provider != "brave" or bool(config.api_key))
        )
        metadata = PROVIDER_METADATA.get(provider, {})
        return {
            **metadata,
            "provider": provider,
            "configured": configured,
            "current_provider": config.provider,
            "external_request": True,
        }
    except ExternalSearchError as exc:
        provider = historical_provider or os.getenv("PLDR_SEARCH_PROVIDER", "brave")
        return {
            **PROVIDER_METADATA.get(provider, {}),
            "provider": provider,
            "configured": False,
            "error": str(exc),
            "external_request": True,
        }


def _query_run_id() -> str:
    return "srchq_" + uuid.uuid4().hex[:20]


def _result_id(query_run_id: str, fingerprint: str, rank: int) -> str:
    return "srchr_" + hashlib.sha256(f"{query_run_id}:{fingerprint}:{rank}".encode()).hexdigest()[:24]


def _selection_error_detail(selection: SearchSelection) -> dict[str, Any] | None:
    if not selection.last_error:
        return None
    message = selection.last_error
    normalized = message.lower()
    error_class = "intake_failed"
    item = selection.intake_item
    if item is not None and item.candidate_mode == "fallback-after-error":
        error_class = "model_fallback"
    elif "non-public address" in normalized or "private address" in normalized:
        error_class = "unsafe_url"
    elif "response too large" in normalized or "size limit" in normalized:
        error_class = "response_too_large"
    elif "unsupported content type" in normalized:
        error_class = "unsupported_content_type"
    elif "unsupported content encoding" in normalized:
        error_class = "unsupported_content_encoding"
    elif "timeout" in normalized or "timed out" in normalized:
        error_class = "timeout"
    elif any(value in normalized for value in ("network", "connection", "dns")):
        error_class = "network"
    elif any(str(status) in normalized for status in (401, 403, 429)):
        error_class = "http_status"
    from .investigations import _structured_task_error

    detail = _structured_task_error(
        error_class,
        message,
        task_status="failed",
    )
    if detail is not None:
        detail["trace_id"] = selection.id
        detail["technical_detail"] = message
    return detail


def serialize_selection(
    selection: SearchSelection | None,
    *,
    current_result: SearchResult | None = None,
) -> dict[str, Any] | None:
    if selection is None:
        return None
    item = selection.intake_item
    error_detail = _selection_error_detail(selection)
    return {
        "status": item.status if item is not None else selection.status,
        "outcome": selection.outcome,
        "attempt_count": selection.attempt_count,
        "last_attempt_at": iso(selection.last_attempt_at),
        "last_error": selection.last_error,
        "error": error_detail,
        "intake_item_id": selection.intake_item_id,
        "intake_status": item.status if item is not None else None,
        "retryable": selection.status in {"failed", "generation_failed"}
        and (
            bool(error_detail["retryable"])
            if error_detail is not None
            else True
        ),
        "selection_event_count": len(selection.events),
        # Describe the result currently being rendered. A canonical URL can be
        # discovered in more than one topic, while the persisted intake object
        # is intentionally reused.
        "latest_query_run_id": (current_result or selection.result).query_run_id,
        "latest_result_id": (current_result or selection.result).id,
    }


def serialize_search_result(
    result: SearchResult,
    selection: SearchSelection | None = None,
    *,
    investigation: Investigation | None = None,
) -> dict[str, Any]:
    run = result.query_run
    publication_time = result.published_at or _explicit_result_publication_time(
        result.title,
        result.snippet,
    )
    return {
        "id": result.id,
        "query_run_id": result.query_run_id,
        "keyword": run.keyword,
        "scope": run.scope,
        "provider": result.provider,
        "channel": result.channel,
        "original_url": result.original_url,
        "canonical_url": result.canonical_url,
        "site": result.site_name,
        "title": result.title,
        "snippet": result.snippet,
        "published_at": iso(publication_time),
        "rank": result.rank,
        "source_page": result.source_page,
        "engine": result.engine,
        "topic_relevance": assess_topic_relevance(result, investigation),
        "selection": serialize_selection(selection, current_result=result),
    }


def serialize_query_run(
    run: SearchQueryRun,
    results: list[SearchResult] | None = None,
    selections_by_fingerprint: dict[str, SearchSelection] | None = None,
    *,
    investigation: Investigation | None = None,
) -> dict[str, Any]:
    selections = selections_by_fingerprint or {}
    items = results if results is not None else list(run.results)
    serialized_results = [
        serialize_search_result(
            result,
            selections.get(result.result_fingerprint),
            investigation=investigation,
        )
        for result in items
    ]
    loaded_count = int(run.result_count or 0)
    page = int(run.current_page or 1)
    page_size = int(run.page_size or 10)
    has_more = bool(run.has_more) and loaded_count < MAX_LOADED_RESULTS
    next_page = page + 1 if has_more else None
    pagination = {
        "page": page,
        "page_size": page_size,
        "returned_count": int(run.returned_count or 0),
        "loaded_count": loaded_count,
        # Search providers do not promise an exact, stable web-wide total.
        "available_count": None,
        "total_estimate": run.total_count,
        "total_known": bool(run.total_known),
        "has_more": has_more,
        "next_page": next_page,
        "next_cursor": str(next_page) if next_page is not None else None,
        "max_loaded_results": MAX_LOADED_RESULTS,
    }
    payload = {
        "id": run.id,
        "query_run_id": run.id,
        "keyword": run.keyword,
        "scope": run.scope,
        "channel": run.channel,
        "language": run.language,
        "effective_language": run.language,
        "status": run.status,
        "error": run.error,
        "error_detail": run.error_detail,
        "structured_error": run.error_detail,
        "result_count": loaded_count,
        **pagination,
        "pagination": pagination,
        "latency_ms": run.latency_ms,
        "created_at": iso(run.created_at),
        "updated_at": iso(run.updated_at),
        "archived": run.archived_at is not None,
        "archived_at": iso(run.archived_at),
        "archived_by": run.archived_by,
        "archive_reason": run.archive_reason,
        "allowed_actions": ["restore"] if run.archived_at is not None else ["archive"],
        "provider": provider_metadata(run.provider),
        "results": serialized_results,
        "items": serialized_results,
    }
    if serialized_results:
        payload["relevance_summary"] = {
            level: sum(
                1
                for item in serialized_results
                if item["topic_relevance"]["level"] == level
            )
            for level in ("likely", "uncertain", "unlikely", "unknown")
        }
    return payload


def _record_query_archive_action(
    session: Session,
    run: SearchQueryRun,
    *,
    action: str,
    analyst: str,
    reason: str,
) -> None:
    from .investigations import record_action

    investigation_ids = list(
        session.scalars(
            select(InvestigationLink.investigation_id).where(
                InvestigationLink.object_type == "search_query",
                InvestigationLink.object_id == run.id,
            )
        )
    )
    for investigation_id in investigation_ids:
        record_action(
            session,
            investigation_id,
            action,
            actor=analyst,
            object_type="search_query",
            object_id=run.id,
            detail={"reason": reason, "status": run.status},
        )


def archive_query_run(
    session: Session,
    run: SearchQueryRun,
    *,
    analyst: str,
    reason: str,
) -> tuple[SearchQueryRun, bool]:
    """Hide a query from history without touching its result or intake graph."""
    if run.archived_at is not None:
        return run, False
    now = datetime.now(timezone.utc)
    run.archived_at = now
    run.archived_by = analyst
    run.archive_reason = reason
    run.updated_at = now
    _record_query_archive_action(
        session,
        run,
        action="search.query_archived",
        analyst=analyst,
        reason=reason,
    )
    session.commit()
    session.refresh(run)
    return run, True


def restore_query_run(
    session: Session,
    run: SearchQueryRun,
    *,
    analyst: str,
    reason: str,
) -> tuple[SearchQueryRun, bool]:
    if run.archived_at is None:
        return run, False
    now = datetime.now(timezone.utc)
    run.archived_at = None
    run.archived_by = None
    run.archive_reason = None
    run.updated_at = now
    _record_query_archive_action(
        session,
        run,
        action="search.query_restored",
        analyst=analyst,
        reason=reason,
    )
    session.commit()
    session.refresh(run)
    return run, True


def _run_investigation(
    session: Session,
    run_id: str,
    *,
    investigation_id: str | None = None,
) -> Investigation | None:
    query = (
        select(Investigation)
        .join(InvestigationLink, InvestigationLink.investigation_id == Investigation.id)
        .where(
            InvestigationLink.object_type == "search_query",
            InvestigationLink.object_id == run_id,
        )
        .order_by(InvestigationLink.created_at.asc())
    )
    if investigation_id is not None:
        query = query.where(Investigation.id == investigation_id)
    return session.scalar(query.limit(1))


def _selections_for_results(
    session: Session,
    results: list[SearchResult],
    *,
    investigation_id: str,
) -> dict[str, SearchSelection]:
    fingerprints = {result.result_fingerprint for result in results}
    if not fingerprints:
        return {}
    return {
        selection.result_fingerprint: selection
        for selection in session.scalars(
            select(SearchSelection)
            .join(
                InvestigationLink,
                (InvestigationLink.object_type == "intake")
                & (InvestigationLink.object_id == SearchSelection.intake_item_id),
            )
            .where(SearchSelection.result_fingerprint.in_(fingerprints))
            .where(InvestigationLink.investigation_id == investigation_id)
            .options(
                selectinload(SearchSelection.result).selectinload(SearchResult.query_run),
                selectinload(SearchSelection.intake_item),
                selectinload(SearchSelection.events),
            )
        )
    }


def get_query_run_payload(
    session: Session,
    run_id: str,
    *,
    investigation_id: str | None = None,
) -> dict[str, Any]:
    run = session.get(SearchQueryRun, run_id)
    if run is None:
        raise ValueError("Search query run not found")
    investigation = _run_investigation(
        session, run.id, investigation_id=investigation_id
    )
    if investigation is None:
        raise ValueError("Search query run is not linked to this investigation")
    results = list(
        session.scalars(
            select(SearchResult)
            .where(SearchResult.query_run_id == run.id)
            .options(selectinload(SearchResult.query_run))
            .order_by(SearchResult.rank.asc())
        )
    )
    payload = serialize_query_run(
        run,
        results,
        _selections_for_results(
            session, results, investigation_id=investigation.id
        ),
        investigation=investigation,
    )
    payload["investigation_id"] = investigation.id
    payload["investigation"] = {
        "id": investigation.id,
        "title": investigation.title,
        "status": investigation.status,
    }
    return payload


def list_query_runs(
    session: Session,
    *,
    investigation_id: str,
    limit: int,
    visibility: str = "active",
) -> dict[str, Any]:
    association = (
        InvestigationLink.object_type == "search_query",
        InvestigationLink.investigation_id == investigation_id,
    )
    base = (
        select(SearchQueryRun)
        .join(InvestigationLink, InvestigationLink.object_id == SearchQueryRun.id)
        .where(*association)
    )
    if visibility == "active":
        base = base.where(SearchQueryRun.archived_at.is_(None))
    elif visibility == "archived":
        base = base.where(SearchQueryRun.archived_at.is_not(None))
    elif visibility != "all":
        raise ValueError("visibility must be active, archived, or all")
    count_query = (
        select(func.count())
        .select_from(SearchQueryRun)
        .join(InvestigationLink, InvestigationLink.object_id == SearchQueryRun.id)
        .where(*association)
    )
    if visibility == "active":
        count_query = count_query.where(SearchQueryRun.archived_at.is_(None))
    elif visibility == "archived":
        count_query = count_query.where(SearchQueryRun.archived_at.is_not(None))
    total = int(
        session.scalar(count_query)
        or 0
    )
    runs = list(
        session.scalars(
            base.order_by(SearchQueryRun.updated_at.desc(), SearchQueryRun.created_at.desc())
            .limit(limit)
        )
    )
    summaries = [serialize_query_run(run, []) for run in runs]
    return {
        "investigation_id": investigation_id,
        "visibility": visibility,
        "count": total,
        "returned_count": len(summaries),
        "runs": summaries,
        "items": summaries,
    }


def _validate_continuation(
    run: SearchQueryRun,
    request: ExternalSearchRequest,
    *,
    provider: str,
    effective_language: str,
    page_size: int,
) -> None:
    if run.normalized_keyword != normalize_text(request.keyword).casefold():
        raise ExternalSearchError(
            "The continuation keyword does not match the saved query",
            status_code=409,
            reason="invalid_query",
        )
    if run.scope != request.scope or run.provider != provider:
        raise ExternalSearchError(
            "The continuation scope or provider does not match the saved query",
            status_code=409,
            reason="invalid_query",
        )
    if run.language != effective_language:
        raise ExternalSearchError(
            "The continuation language does not match the saved query",
            status_code=409,
            reason="invalid_query",
        )
    if int(run.page_size or 10) != page_size:
        raise ExternalSearchError(
            "The page size cannot change while continuing a saved query",
            status_code=409,
            reason="invalid_query",
        )


async def execute_external_search(
    session: Session, request: ExternalSearchRequest
) -> dict[str, Any]:
    normalized_keyword = normalize_text(request.keyword).casefold()
    if not normalized_keyword:
        raise ExternalSearchError("Keyword is empty", status_code=422, reason="invalid_query")
    config = SearchProviderConfig.from_env()
    effective_language = effective_search_language(
        request.keyword, request.language, config.provider
    )
    requested_page_size = request.effective_page_size
    run: SearchQueryRun
    investigation: Investigation
    from .investigations import attach_search_run, record_action

    if request.query_run_id is not None:
        run = session.get(SearchQueryRun, request.query_run_id)
        if run is None:
            raise ValueError("Search query run not found")
        # If a continuation omits both size fields, inherit the frozen page size
        # instead of treating the legacy default of ten as a requested change.
        if not ({"limit", "page_size"} & request.model_fields_set):
            requested_page_size = int(run.page_size or 10)
        if "language" not in request.model_fields_set:
            effective_language = run.language
        try:
            _validate_continuation(
                run,
                request,
                provider=config.provider,
                effective_language=effective_language,
                page_size=requested_page_size,
            )
        except ExternalSearchError as exc:
            exc.query_run_id = run.id
            raise
        investigation = _run_investigation(
            session, run.id, investigation_id=request.investigation_id
        )
        if investigation is None:
            raise ValueError("Search query run is not linked to this investigation")
        retrying_failed_first_page = (
            request.page == 1
            and int(run.current_page or 1) == 1
            and run.status == "failed"
            and int(run.result_count or 0) == 0
        )
        if request.page <= int(run.current_page or 1) and not retrying_failed_first_page:
            return get_query_run_payload(
                session, run.id, investigation_id=investigation.id
            )
        if (
            not retrying_failed_first_page
            and request.page != int(run.current_page or 1) + 1
        ):
            error = ExternalSearchError(
                "Search pages must be loaded in order",
                status_code=409,
                reason="invalid_query",
            )
            error.query_run_id = run.id
            raise error
        if int(run.result_count or 0) >= MAX_LOADED_RESULTS:
            run.has_more = False
            session.commit()
            return get_query_run_payload(
                session, run.id, investigation_id=investigation.id
            )
    else:
        now = datetime.now(timezone.utc)
        run = SearchQueryRun(
            id=_query_run_id(),
            keyword=normalize_text(request.keyword),
            normalized_keyword=normalized_keyword,
            scope=request.scope,
            provider=config.provider,
            channel=f"{'brave-search-api' if config.provider == 'brave' else config.provider}:{request.scope}",
            language=effective_language,
            status="running",
            error_detail=None,
            result_count=0,
            current_page=1,
            page_size=requested_page_size,
            returned_count=0,
            has_more=False,
            total_known=False,
            total_count=None,
            created_at=now,
            updated_at=now,
        )
        session.add(run)
        investigation = attach_search_run(session, request, run)

    # Freeze the effective values sent upstream. ``auto`` must never reach a
    # provider, and a continuation must keep the original page size.
    provider_request = request.model_copy(
        update={
            "language": effective_language,
            "page_size": requested_page_size,
            "limit": requested_page_size,
        }
    )
    run.status = "running"
    run.updated_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(run)
    started = time.monotonic()
    try:
        backend_response = await request_search(config, provider_request)
    except ExternalSearchError as exc:
        exc.query_run_id = run.id
        exc.attempted_page = request.page
        run.status = "partial_failure" if int(run.result_count or 0) else "failed"
        run.error = str(exc)
        run.error_detail = exc.as_dict()
        run.returned_count = 0
        run.latency_ms = int((time.monotonic() - started) * 1000)
        run.updated_at = datetime.now(timezone.utc)
        record_action(
            session,
            investigation.id,
            "search.query_failed",
            actor="analyst",
            object_type="search_query",
            object_id=run.id,
            detail=exc.as_dict(),
        )
        session.commit()
        raise

    existing_fingerprints = set(
        session.scalars(
            select(SearchResult.result_fingerprint).where(
                SearchResult.query_run_id == run.id
            )
        )
    )
    page_seen: set[str] = set()
    rank = int(
        session.scalar(
            select(func.max(SearchResult.rank)).where(SearchResult.query_run_id == run.id)
        )
        or 0
    )
    # ``limit`` is the original one-shot contract: callers that only send that
    # field must still receive at most that many unique rows.  A workspace run
    # opts into provider-page persistence with ``page_size`` (and every saved-run
    # continuation is necessarily a workspace request).  SearXNG controls its
    # own operator page size, which can be larger than PLDR's display page; keep
    # that whole page so later UI paging/select-all cannot silently lose rows.
    preserve_searxng_operator_page = (
        backend_response.provider == "searxng"
        and (request.page_size is not None or request.query_run_id is not None)
    )
    page_result_cap = (
        MAX_LOADED_RESULTS
        if preserve_searxng_operator_page
        else requested_page_size
    )
    added_count = 0
    for hit in backend_response.hits:
        if hit.fingerprint in page_seen:
            continue
        page_seen.add(hit.fingerprint)
        if hit.fingerprint in existing_fingerprints:
            continue
        if added_count >= page_result_cap:
            break
        if rank >= MAX_LOADED_RESULTS:
            break
        rank += 1
        added_count += 1
        existing_fingerprints.add(hit.fingerprint)
        session.add(
            SearchResult(
                id=_result_id(run.id, hit.fingerprint, rank),
                query_run_id=run.id,
                result_fingerprint=hit.fingerprint,
                provider=backend_response.provider,
                channel=backend_response.channel,
                original_url=hit.original_url,
                canonical_url=hit.canonical_url,
                site_name=hit.site_name,
                title=hit.title,
                snippet=hit.snippet,
                published_at=hit.published_at,
                rank=rank,
                source_page=request.page,
                engine=hit.engine,
                raw_result=hit.raw_result,
            )
        )
    try:
        session.flush()
    except IntegrityError as exc:
        # Two workers can pass the preflight page check before either commits.
        # Never leak the uniqueness race as an opaque 500: discard this
        # transaction, then reuse the winner's page when it is already visible.
        session.rollback()
        concurrent_run = session.get(SearchQueryRun, run.id)
        if concurrent_run is not None and int(concurrent_run.current_page or 1) >= request.page:
            return get_query_run_payload(
                session, concurrent_run.id, investigation_id=investigation.id
            )
        error = ExternalSearchError(
            "Concurrent request is updating the same saved query page",
            status_code=409,
            reason="concurrent_update",
        )
        error.query_run_id = run.id
        error.attempted_page = request.page
        raise error from exc
    loaded_count = int(
        session.scalar(
            select(func.count())
            .select_from(SearchResult)
            .where(SearchResult.query_run_id == run.id)
        )
        or 0
    )
    run.provider = backend_response.provider
    run.channel = backend_response.channel
    run.status = "ok"
    run.error = None
    run.error_detail = None
    run.result_count = loaded_count
    run.current_page = request.page
    run.page_size = requested_page_size
    run.returned_count = added_count
    provider_has_more = backend_response.has_more
    if provider_has_more is None:
        provider_has_more = len(backend_response.hits) >= requested_page_size
    run.has_more = (
        bool(provider_has_more)
        and added_count > 0
        and loaded_count < MAX_LOADED_RESULTS
    )
    run.total_known = False
    run.total_count = backend_response.total_estimate
    run.latency_ms = int((time.monotonic() - started) * 1000)
    run.updated_at = datetime.now(timezone.utc)
    record_action(
        session,
        investigation.id,
        "search.query_completed",
        actor="analyst",
        object_type="search_query",
        object_id=run.id,
        detail={
            "page": run.current_page,
            "returned_count": run.returned_count,
            "loaded_count": run.result_count,
            "has_more": run.has_more,
            "latency_ms": run.latency_ms,
        },
    )
    session.commit()
    return get_query_run_payload(session, run.id, investigation_id=investigation.id)


def _trace_for_selection(
    selection: SearchSelection,
    result: SearchResult | None = None,
    *,
    event_id: str | None = None,
    selected_at: datetime | None = None,
    outcome: str | None = None,
) -> dict[str, Any]:
    result = result or selection.result
    run = result.query_run
    return {
        "query_run_id": run.id,
        "keyword": run.keyword,
        "scope": run.scope,
        "provider": result.provider,
        "channel": result.channel,
        "language": run.language,
        "result_id": result.id,
        "original_url": result.original_url,
        "canonical_url": result.canonical_url,
        "search_title": result.title,
        "search_snippet": result.snippet,
        "search_published_at": iso(result.published_at),
        "rank": result.rank,
        "selection_id": selection.id,
        "selection_event_id": event_id,
        "selected_at": iso(selected_at or selection.created_at),
        "outcome": outcome or selection.outcome,
        "attempt_count": selection.attempt_count,
    }


def _attach_trace(
    item: IntakeItem,
    selection: SearchSelection,
    result: SearchResult | None = None,
    event: SearchSelectionEvent | None = None,
) -> None:
    review = dict(item.review or {})
    latest = (
        dict(event.trace_json)
        if event is not None and event.trace_json
        else _trace_for_selection(selection, result)
    )
    history = list(review.get("external_search_history", []))
    legacy_trace = review.get("external_search")
    if not history and legacy_trace:
        history.append(legacy_trace)
    history.append(latest)
    review["external_search"] = latest
    review["external_search_history"] = history
    item.review = review
    item.updated_at = datetime.now(timezone.utc)


def _retry_intake_baseline(item: IntakeItem) -> tuple[str, str | None]:
    return item.status, iso(item.updated_at)


def _require_retry_intake_baseline(
    item: IntakeItem,
    baseline: tuple[str, str | None],
    *,
    action: str,
) -> None:
    if _retry_intake_baseline(item) != baseline:
        raise IntakeMutationConflictError(action)


def _existing_intake_for_result(session: Session, result: SearchResult) -> IntakeItem | None:
    return session.scalar(
        select(IntakeItem)
        .where(
            or_(
                IntakeItem.source_url == result.original_url,
                IntakeItem.canonical_url == result.canonical_url,
            )
        )
        .order_by(IntakeItem.created_at.asc())
        .limit(1)
    )


def _new_selection(
    session: Session,
    result: SearchResult,
    item: IntakeItem,
    *,
    outcome: str,
    attempt_count: int,
) -> SearchSelection:
    selection = SearchSelection(
        id="srchs_" + hashlib.sha256(result.result_fingerprint.encode()).hexdigest()[:24],
        result_id=result.id,
        result_fingerprint=result.result_fingerprint,
        intake_item_id=item.id,
        status=item.status,
        outcome=outcome,
        attempt_count=attempt_count,
        last_error=item.error,
    )
    session.add(selection)
    session.flush()
    return selection


def _record_selection_event(
    session: Session,
    selection: SearchSelection,
    result: SearchResult,
    *,
    outcome: str,
) -> SearchSelectionEvent:
    event_id = "srche_" + uuid.uuid4().hex[:24]
    now = datetime.now(timezone.utc)
    event = SearchSelectionEvent(
        id=event_id,
        selection_id=selection.id,
        query_run_id=result.query_run_id,
        result_id=result.id,
        outcome=outcome,
        trace_json=_trace_for_selection(
            selection,
            result,
            event_id=event_id,
            selected_at=now,
            outcome=outcome,
        ),
        created_at=now,
    )
    session.add(event)
    session.flush()
    return event


async def _retry_failed_fetch(session: Session, selection: SearchSelection) -> IntakeItem:
    item = selection.intake_item
    result = selection.result
    if item.archived_at is not None:
        raise ArchivedIntakeError("retrying this search result")
    item_id = item.id
    selection_id = selection.id
    result_id = result.id
    if item.status == "generation_failed":
        item = await generate_candidates(session, item)
        generated_baseline = _retry_intake_baseline(item)
        session.rollback()
        item = lock_intake_for_mutation(
            session, item_id, action="recording this search retry"
        )
        try:
            _require_retry_intake_baseline(
                item,
                generated_baseline,
                action="recording this search retry",
            )
        except IntakeMutationConflictError:
            session.rollback()
            raise
        selection = session.get(SearchSelection, selection_id)
        result = session.get(SearchResult, result_id)
        assert selection is not None and result is not None
        selection.attempt_count += 1
        selection.last_attempt_at = datetime.now(timezone.utc)
        selection.outcome = "retry"
        selection.status = item.status
        selection.last_error = item.candidate_error
        event = _record_selection_event(
            session,
            selection,
            result,
            outcome="retry_succeeded" if item.status == "candidate_ready" else "retry_failed",
        )
        _attach_trace(item, selection, result, event)
        session.commit()
        return item
    if item.status != "failed":
        # No await is needed in this branch, but the item was loaded in a read
        # transaction that may now be stale. Restart and fence before adding a
        # retry event/trace to its review graph.
        session.rollback()
        item = lock_intake_for_mutation(
            session, item_id, action="recording this search retry"
        )
        selection = session.get(SearchSelection, selection_id)
        result = session.get(SearchResult, result_id)
        assert selection is not None and result is not None
        selection.attempt_count += 1
        selection.last_attempt_at = datetime.now(timezone.utc)
        selection.outcome = "retry"
        selection.status = item.status
        event = _record_selection_event(session, selection, result, outcome="retry_not_needed")
        _attach_trace(item, selection, result, event)
        session.commit()
        return item

    fetched_at = datetime.now(timezone.utc)
    fetch_baseline = _retry_intake_baseline(item)
    try:
        resolved_url, html = await fetch_public_text(result.original_url)
        resolved_url = canonicalize_url(resolved_url)
        page = extract_page(html, fallback_title=item.title or "", url=resolved_url)
        quality = assess_extraction(page)
        if quality.status != "usable":
            raise ValueError(
                "Extracted page body is too short or not usable: "
                + ", ".join(quality.reasons)
            )
        session.rollback()
        item = lock_intake_for_mutation(
            session, item_id, action="retrying this search result"
        )
        _require_retry_intake_baseline(
            item,
            fetch_baseline,
            action="applying this search retry fetch",
        )
        selection = session.get(SearchSelection, selection_id)
        result = session.get(SearchResult, result_id)
        assert selection is not None and result is not None
        selection.attempt_count += 1
        selection.last_attempt_at = datetime.now(timezone.utc)
        selection.outcome = "retry"
        item.status = "parsed"
        item.error = None
        item.canonical_url = resolved_url
        item.title = page.title or item.title or None
        item.published_at = item.published_at or page.published_at
        item.raw_snapshot = html
        item.raw_hash = hashlib.sha256(html.encode("utf-8")).hexdigest()
        item.extracted_snapshot = page.body
        item.extracted_hash = hashlib.sha256(page.body.encode("utf-8")).hexdigest()
        item.candidate_error = None
        item.candidate_relations = []
        item.updated_at = datetime.now(timezone.utc)
        review = dict(item.review or {})
        review["material"] = extracted_material_metadata(
            page,
            resolved_url=resolved_url,
            fetched_at=fetched_at,
            fetch_method="safe_http_or_reader",
            existing=review.get("material") or {},
        )
        item.review = review
        session.commit()
        item = await generate_candidates(session, item)
        generated_baseline = _retry_intake_baseline(item)
        session.rollback()
        item = lock_intake_for_mutation(
            session, item_id, action="recording this search retry"
        )
        _require_retry_intake_baseline(
            item,
            generated_baseline,
            action="recording this search retry",
        )
        selection = session.get(SearchSelection, selection_id)
        result = session.get(SearchResult, result_id)
        assert selection is not None and result is not None
        selection.status = item.status
        selection.last_error = item.candidate_error
        event = _record_selection_event(
            session,
            selection,
            result,
            outcome="retry_succeeded" if item.status == "candidate_ready" else "retry_failed",
        )
        _attach_trace(item, selection, result, event)
        session.commit()
        return item
    except (ArchivedIntakeError, IntakeMutationConflictError):
        session.rollback()
        raise
    except Exception as exc:
        session.rollback()
        item = lock_intake_for_mutation(
            session, item_id, action="recording this search retry failure"
        )
        try:
            _require_retry_intake_baseline(
                item,
                fetch_baseline,
                action="recording this search retry failure",
            )
        except IntakeMutationConflictError:
            session.rollback()
            raise
        selection = session.get(SearchSelection, selection_id)
        result = session.get(SearchResult, result_id)
        assert selection is not None and result is not None
        selection.attempt_count += 1
        selection.last_attempt_at = datetime.now(timezone.utc)
        selection.outcome = "retry"
        item.status = "failed"
        item.error = str(exc)
        item.updated_at = datetime.now(timezone.utc)
        selection.status = "failed"
        selection.last_error = str(exc)
        from .investigations import sync_linked_review_tasks_for_intake

        sync_linked_review_tasks_for_intake(
            session,
            item,
            actor="system:search-retry",
        )
        event = _record_selection_event(session, selection, result, outcome="retry_failed")
        _attach_trace(item, selection, result, event)
        session.commit()
        return item


async def select_search_results(
    session: Session, request: ExternalSearchSelectionRequest, *, retry: bool = False
) -> dict[str, Any]:
    if (
        request.investigation_id is not None
        or request.new_investigation is not None
        or request.request_id is not None
    ):
        # The investigation-aware path is deliberately a short database-only
        # transaction. Requests carrying an idempotency key but no explicit topic
        # are queued into the durable unclassified topic. The collector performs
        # each fetch/model operation later. Only the keyless legacy contract stays
        # synchronous for compatibility with existing API clients.
        from .investigations import enqueue_search_result_tasks

        return enqueue_search_result_tasks(session, request)

    requested_ids = list(dict.fromkeys(request.result_ids))
    results = list(
        session.scalars(
            select(SearchResult)
            .where(SearchResult.id.in_(requested_ids))
            .options(
                selectinload(SearchResult.query_run),
                selectinload(SearchResult.selection).selectinload(SearchSelection.intake_item),
            )
        )
    )
    found_ids = {result.id for result in results}
    missing = [result_id for result_id in requested_ids if result_id not in found_ids]
    if missing:
        raise ValueError(f"Search results not found: {', '.join(missing)}")

    responses: list[dict[str, Any]] = []
    for result in sorted(results, key=lambda item: requested_ids.index(item.id)):
        existing = session.scalar(
            select(SearchSelection)
            .where(SearchSelection.result_fingerprint == result.result_fingerprint)
            .options(
                selectinload(SearchSelection.result).selectinload(SearchResult.query_run),
                selectinload(SearchSelection.intake_item),
                selectinload(SearchSelection.events),
            )
        )
        if existing is not None:
            if existing.intake_item.archived_at is not None:
                raise ArchivedIntakeError("selecting this search result")
            outcome = "already_added"
            if retry and existing.intake_item.status in {"failed", "generation_failed"}:
                existing.result = result
                await _retry_failed_fetch(session, existing)
                outcome = "retried"
            else:
                existing_id = existing.id
                intake_item_id = existing.intake_item_id
                result_id = result.id
                session.rollback()
                locked_item = lock_intake_for_mutation(
                    session,
                    intake_item_id,
                    action="recording this search selection",
                )
                existing = session.get(SearchSelection, existing_id)
                result = session.get(SearchResult, result_id)
                assert existing is not None and result is not None
                existing.outcome = "already_added"
                existing.result = result
                event = _record_selection_event(session, existing, result, outcome=outcome)
                _attach_trace(locked_item, existing, result, event)
                session.commit()
            responses.append(
                {
                    "result_id": result.id,
                    "outcome": outcome,
                    "intake_item_id": existing.intake_item_id,
                    "intake_status": existing.intake_item.status,
                    "error": existing.last_error,
                    "result": serialize_search_result(result, existing),
                }
            )
            from .investigations import link_legacy_search_selection

            link_legacy_search_selection(
                session,
                query_run_id=result.query_run_id,
                intake_item_id=existing.intake_item_id,
            )
            session.commit()
            continue

        existing_item = _existing_intake_for_result(session, result)
        if existing_item is not None:
            if existing_item.archived_at is not None:
                raise ArchivedIntakeError("selecting this search result")
            existing_item_id = existing_item.id
            result_id = result.id
            session.rollback()
            existing_item = lock_intake_for_mutation(
                session,
                existing_item_id,
                action="selecting this search result",
            )
            result = session.get(SearchResult, result_id)
            assert result is not None
            selection = _new_selection(
                session, result, existing_item, outcome="linked_existing_intake", attempt_count=0
            )
            event = _record_selection_event(
                session, selection, result, outcome="linked_existing_intake"
            )
            _attach_trace(existing_item, selection, result, event)
            session.commit()
        else:
            item = await submit_web_intake(
                session,
                result.original_url,
                None,
                None,
                None,
                result.query_run.language,
                input_type="search",
            )
            item = lock_intake_for_mutation(
                session,
                item.id,
                action="recording this search selection",
            )
            selection = _new_selection(session, result, item, outcome="added", attempt_count=1)
            event = _record_selection_event(session, selection, result, outcome="added")
            _attach_trace(item, selection, result, event)
            session.commit()
        responses.append(
            {
                "result_id": result.id,
                "outcome": selection.outcome,
                "intake_item_id": selection.intake_item_id,
                "intake_status": selection.status,
                "error": selection.last_error,
                "result": serialize_search_result(result, selection),
            }
        )
        from .investigations import link_legacy_search_selection

        link_legacy_search_selection(
            session,
            query_run_id=result.query_run_id,
            intake_item_id=selection.intake_item_id,
        )
        session.commit()
    return {
        "status": "ok",
        "requested_count": len(requested_ids),
        "results": responses,
    }


async def retry_search_result(session: Session, result_id: str) -> dict[str, Any]:
    result = session.scalar(
        select(SearchResult)
        .where(SearchResult.id == result_id)
        .options(selectinload(SearchResult.query_run))
    )
    if result is None:
        raise ValueError("Search result not found")
    selection = session.scalar(
        select(SearchSelection)
        .where(SearchSelection.result_fingerprint == result.result_fingerprint)
        .options(
            selectinload(SearchSelection.result).selectinload(SearchResult.query_run),
            selectinload(SearchSelection.intake_item),
            selectinload(SearchSelection.events),
        )
    )
    if selection is None:
        request = ExternalSearchSelectionRequest(result_ids=[result.id])
        return await select_search_results(session, request)
    if selection.intake_item.archived_at is not None:
        raise ArchivedIntakeError("retrying this search result")
    selection.result = result
    item = await _retry_failed_fetch(session, selection)
    return {
        "status": "ok",
        "result_id": result.id,
        "outcome": "retried",
        "intake_item_id": item.id,
        "intake_status": item.status,
        "error": selection.last_error,
        "result": serialize_search_result(result, selection),
    }
