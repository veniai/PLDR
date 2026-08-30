from __future__ import annotations

import hashlib
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse, urlsplit

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from .extraction import canonicalize_url, extract_page, normalize_text
from .importers import fetch_public_text
from .intake import generate_candidates, iso, submit_web_intake
from .models import IntakeItem, SearchQueryRun, SearchResult, SearchSelection, SearchSelectionEvent
from .schemas import ExternalSearchRequest, ExternalSearchSelectionRequest


class ExternalSearchError(RuntimeError):
    query_run_id: str | None = None

    def __init__(self, message: str, *, status_code: int = 502, reason: str = "backend_error"):
        super().__init__(message)
        self.status_code = status_code
        self.reason = reason


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


def _normalize_hit(raw: dict[str, Any], *, provider: str, engine: str | None = None) -> SearchHit:
    original_url, canonical_url, fingerprint, host = _parse_public_result_url(raw.get("url"))
    meta_url = raw.get("meta_url") if isinstance(raw.get("meta_url"), dict) else {}
    site_name = _safe_text(
        raw.get("source") or meta_url.get("hostname") or host,
        200,
    ) or host
    return SearchHit(
        original_url=original_url,
        canonical_url=canonical_url,
        fingerprint=fingerprint,
        site_name=site_name,
        title=_safe_text(raw.get("title"), 500) or "Untitled search result",
        snippet=_safe_text(raw.get("description") or raw.get("content"), 2000),
        published_at=_parse_datetime(raw.get("publishedDate") or raw.get("page_age") or raw.get("timestamp")),
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
    payload: dict[str, Any] = {
        "q": request.keyword,
        "count": request.limit,
        "search_lang": request.language,
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
        )
    if response.status_code == 429:
        raise ExternalSearchError("Brave Search API rate limit reached", status_code=429, reason="rate_limited")
    if response.status_code >= 400:
        raise ExternalSearchError(
            f"Brave Search API returned HTTP {response.status_code}",
            status_code=502,
            reason="backend_error",
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise ExternalSearchError("Brave Search API returned invalid JSON", reason="invalid_response") from exc
    if not isinstance(payload, dict):
        raise ExternalSearchError("Brave Search API response must be a JSON object", reason="invalid_response")
    return BackendSearchResponse("brave", channel, [
        _normalize_hit(item, provider="brave") for item in _nested_results(payload)
    ])


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
    params = {
        "q": request.keyword,
        "format": "json",
        "categories": "news" if request.scope == "news" else "general",
        "language": request.language,
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
        )
    if response.status_code == 429:
        raise ExternalSearchError("SearXNG rate limit reached", status_code=429, reason="rate_limited")
    if response.status_code >= 400:
        raise ExternalSearchError(
            f"SearXNG returned HTTP {response.status_code}", status_code=502, reason="backend_error"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise ExternalSearchError("SearXNG returned invalid JSON", reason="invalid_response") from exc
    if not isinstance(payload, dict):
        raise ExternalSearchError("SearXNG response must be a JSON object", reason="invalid_response")
    raw_results = payload.get("results")
    raw_results = raw_results if isinstance(raw_results, list) else []
    return BackendSearchResponse("searxng", channel, [
        _normalize_hit(item, provider="searxng") for item in raw_results if isinstance(item, dict)
    ])


async def request_search(
    config: SearchProviderConfig, request: ExternalSearchRequest
) -> BackendSearchResponse:
    if config.provider == "brave":
        return await request_brave_search(config, request)
    return await request_searxng_search(config, request)


def provider_metadata() -> dict[str, Any]:
    try:
        config = SearchProviderConfig.from_env()
        configured = bool(config.base_url) and (config.provider != "brave" or bool(config.api_key))
        return {
            **PROVIDER_METADATA[config.provider],
            "provider": config.provider,
            "configured": configured,
            "external_request": True,
        }
    except ExternalSearchError as exc:
        return {
            "provider": os.getenv("PLDR_SEARCH_PROVIDER", "brave"),
            "configured": False,
            "error": str(exc),
            "external_request": True,
        }


def _query_run_id() -> str:
    return "srchq_" + uuid.uuid4().hex[:20]


def _result_id(query_run_id: str, fingerprint: str, rank: int) -> str:
    return "srchr_" + hashlib.sha256(f"{query_run_id}:{fingerprint}:{rank}".encode()).hexdigest()[:24]


def serialize_selection(selection: SearchSelection | None) -> dict[str, Any] | None:
    if selection is None:
        return None
    item = selection.intake_item
    return {
        "status": item.status if item is not None else selection.status,
        "outcome": selection.outcome,
        "attempt_count": selection.attempt_count,
        "last_attempt_at": iso(selection.last_attempt_at),
        "last_error": selection.last_error,
        "intake_item_id": selection.intake_item_id,
        "intake_status": item.status if item is not None else None,
        "retryable": selection.status == "failed",
        "selection_event_count": len(selection.events),
        "latest_query_run_id": selection.result.query_run_id,
        "latest_result_id": selection.result_id,
    }


def serialize_search_result(result: SearchResult, selection: SearchSelection | None = None) -> dict[str, Any]:
    run = result.query_run
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
        "published_at": iso(result.published_at),
        "rank": result.rank,
        "engine": result.engine,
        "selection": serialize_selection(selection),
    }


def serialize_query_run(
    run: SearchQueryRun,
    results: list[SearchResult] | None = None,
    selections_by_fingerprint: dict[str, SearchSelection] | None = None,
) -> dict[str, Any]:
    selections = selections_by_fingerprint or {}
    items = results if results is not None else list(run.results)
    return {
        "id": run.id,
        "keyword": run.keyword,
        "scope": run.scope,
        "channel": run.channel,
        "language": run.language,
        "status": run.status,
        "error": run.error,
        "result_count": run.result_count,
        "latency_ms": run.latency_ms,
        "created_at": iso(run.created_at),
        "provider": provider_metadata(),
        "results": [
            serialize_search_result(result, selections.get(result.result_fingerprint)) for result in items
        ],
    }


async def execute_external_search(
    session: Session, request: ExternalSearchRequest
) -> dict[str, Any]:
    normalized_keyword = normalize_text(request.keyword).casefold()
    if not normalized_keyword:
        raise ExternalSearchError("Keyword is empty", status_code=422, reason="invalid_query")
    config = SearchProviderConfig.from_env()
    run = SearchQueryRun(
        id=_query_run_id(),
        keyword=normalize_text(request.keyword),
        normalized_keyword=normalized_keyword,
        scope=request.scope,
        provider=config.provider,
        channel=f"{'brave-search-api' if config.provider == 'brave' else config.provider}:{request.scope}",
        language=request.language,
        status="running",
    )
    session.add(run)
    from .investigations import attach_search_run, record_action

    investigation = attach_search_run(session, request, run)
    session.commit()
    session.refresh(run)
    started = time.monotonic()
    try:
        backend_response = await request_search(config, request)
    except ExternalSearchError as exc:
        run.status = "failed"
        run.error = str(exc)
        run.latency_ms = int((time.monotonic() - started) * 1000)
        record_action(
            session,
            investigation.id,
            "search.query_failed",
            actor="analyst",
            object_type="search_query",
            object_id=run.id,
            detail={"reason": exc.reason, "error": str(exc)},
        )
        session.commit()
        exc.query_run_id = run.id
        raise

    seen: set[str] = set()
    rank = 0
    for hit in backend_response.hits:
        if hit.fingerprint in seen:
            continue
        seen.add(hit.fingerprint)
        rank += 1
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
                engine=hit.engine,
                raw_result=hit.raw_result,
            )
        )
        if rank >= request.limit:
            break
    run.provider = backend_response.provider
    run.channel = backend_response.channel
    run.status = "ok"
    run.result_count = len(seen)
    run.latency_ms = int((time.monotonic() - started) * 1000)
    record_action(
        session,
        investigation.id,
        "search.query_completed",
        actor="analyst",
        object_type="search_query",
        object_id=run.id,
        detail={"result_count": run.result_count, "latency_ms": run.latency_ms},
    )
    session.commit()
    session.refresh(run)
    selections = {
        selection.result_fingerprint: selection
        for selection in session.scalars(
            select(SearchSelection)
            .where(SearchSelection.result_fingerprint.in_(seen))
            .options(
                selectinload(SearchSelection.result).selectinload(SearchResult.query_run),
                selectinload(SearchSelection.events),
            )
        )
    }
    payload = serialize_query_run(run, list(run.results), selections)
    payload["investigation_id"] = investigation.id
    payload["investigation"] = {
        "id": investigation.id,
        "title": investigation.title,
        "status": investigation.status,
    }
    return payload


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
    selection.attempt_count += 1
    selection.last_attempt_at = datetime.now(timezone.utc)
    selection.outcome = "retry"
    if item.status == "generation_failed":
        item = await generate_candidates(session, item)
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
        selection.status = item.status
        event = _record_selection_event(session, selection, result, outcome="retry_not_needed")
        _attach_trace(item, selection, result, event)
        session.commit()
        return item

    fetched_at = datetime.now(timezone.utc)
    try:
        resolved_url, html = await fetch_public_text(result.original_url)
        resolved_url = canonicalize_url(resolved_url)
        page = extract_page(html)
        if len(page.body) < 40:
            raise ValueError("Fetched page body is too short")
        item.status = "parsed"
        item.error = None
        item.canonical_url = resolved_url
        item.title = page.title or None
        item.raw_snapshot = html
        item.raw_hash = hashlib.sha256(html.encode("utf-8")).hexdigest()
        item.extracted_snapshot = page.body
        item.extracted_hash = hashlib.sha256(page.body.encode("utf-8")).hexdigest()
        item.candidate_error = None
        item.candidate_relations = []
        review = dict(item.review or {})
        review["material"] = {"resolved_url": resolved_url, "fetched_at": iso(fetched_at)}
        item.review = review
        session.commit()
        item = await generate_candidates(session, item)
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
    except Exception as exc:
        session.rollback()
        item = session.get(IntakeItem, item.id)
        selection = session.get(SearchSelection, selection.id)
        assert item is not None and selection is not None
        selection.attempt_count += 1
        selection.last_attempt_at = datetime.now(timezone.utc)
        selection.outcome = "retry"
        item.status = "failed"
        item.error = str(exc)
        selection.status = "failed"
        selection.last_error = str(exc)
        result = session.get(SearchResult, result.id)
        assert result is not None
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
            outcome = "already_added"
            if retry and existing.intake_item.status in {"failed", "generation_failed"}:
                existing.result = result
                await _retry_failed_fetch(session, existing)
                outcome = "retried"
            else:
                existing.outcome = "already_added"
                existing.result = result
                event = _record_selection_event(session, existing, result, outcome=outcome)
                _attach_trace(existing.intake_item, existing, result, event)
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
