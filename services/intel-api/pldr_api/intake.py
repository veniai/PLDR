from __future__ import annotations

import base64
import html as html_lib
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Callable
from xml.etree import ElementTree

from sqlalchemy import DateTime, bindparam, select, text
from sqlalchemy.orm import Session, selectinload

from .errors import ArchivedIntakeError, IntakeMutationConflictError
from .extraction import (
    assess_extraction,
    canonicalize_url,
    content_hash,
    extract_page,
    normalize_text,
    normalize_structured_text,
    near_duplicate_similarity,
    paragraph_id_for_offset,
    paragraph_spans,
)
from .importers import fetch_public_text, fetch_public_text_response
from .llm import run_model_task
from .models import (
    Claim,
    Document,
    Entity,
    Event,
    EventDocument,
    EventEntity,
    Evidence,
    IntakeCandidate,
    IntakeItem,
    Investigation,
    InvestigationLink,
    ReviewTask,
    Snapshot,
    Source,
)
from .schemas import IntakeConfirmationRequest
from .security import validate_public_http_url


MAX_FILE_BYTES = 5 * 1024 * 1024
SUPPORTED_FILE_SUFFIXES = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".html": "text/html",
    ".htm": "text/html",
    ".pdf": "application/pdf",
}
UNKNOWN_TITLE = "[unknown title]"
UNKNOWN_DATETIME = datetime(1970, 1, 1, tzinfo=timezone.utc)
DEFAULT_EVENT_SUMMARY = "Analyst-confirmed intake material."
DEFAULT_EVIDENCE_NOTE = "Human-confirmed from isolated intake candidate; machine candidate retained in intake."
ACTIVE_REVIEW_TASK_STATUSES = ("queued", "fetching", "generating")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Invalid ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def extracted_material_metadata(
    page: Any,
    *,
    resolved_url: str | None = None,
    fetched_at: datetime | None = None,
    fetch_method: str = "provided",
    fetch_metadata: dict[str, Any] | None = None,
    existing: dict[str, Any] | None = None,
    http_status: int | None = None,
) -> dict[str, Any]:
    quality = assess_extraction(page)
    return {
        **(existing or {}),
        "resolved_url": resolved_url,
        "fetched_at": iso(fetched_at),
        "http_status": http_status,
        "fetch_method": fetch_method,
        "fetch_metadata": fetch_metadata or {},
        "extraction_method": page.extraction_method,
        "quality": {
            "status": quality.status,
            "reasons": list(quality.reasons),
            "text_chars": quality.text_chars,
            "paragraph_count": quality.paragraph_count,
            "link_ratio": quality.link_ratio,
        },
        "metadata": {
            "author": page.author,
            "site_name": page.site_name,
            "canonical_url": page.canonical_url,
            "published_at": iso(page.published_at),
        },
        "paragraphs": paragraph_metadata(page.body),
    }


def paragraph_metadata(text_value: str) -> list[dict[str, Any]]:
    return [
        {
            "id": paragraph.id,
            "start_offset": paragraph.start_offset,
            "end_offset": paragraph.end_offset,
            "content_hash": content_hash(paragraph.text),
        }
        for paragraph in paragraph_spans(text_value)
    ]


def _normalized_new_event_fields(item: IntakeItem, request: IntakeConfirmationRequest) -> dict[str, Any]:
    """Return the exact values used when confirmation creates a formal Event."""
    # A document publication time is provenance about the material, not proof of
    # when the described event happened. Keep an intentionally blank event time
    # unknown in the public contract; Event.start_at remains non-null only because
    # the existing persistence model requires a sortable sentinel value.
    requested_start_at = parse_datetime(request.event.start_at)
    start_at = requested_start_at or UNKNOWN_DATETIME
    return {
        "title": request.event.title.strip(),
        "summary": request.event.summary.strip() or DEFAULT_EVENT_SUMMARY,
        "event_type": request.event.event_type,
        "start_at": start_at,
        "start_at_known": requested_start_at is not None,
        "location_name": request.event.location_name if request.event.location_name != "Unknown" else "",
        "importance": request.event.importance,
    }


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def new_intake_id(input_type: str) -> str:
    return f"int_{input_type[:4]}_{uuid.uuid4().hex[:16]}"


def _base_item(input_type: str, **values: Any) -> IntakeItem:
    now = utcnow()
    return IntakeItem(
        id=new_intake_id(input_type),
        input_type=input_type,
        status="parsed",
        created_at=now,
        updated_at=now,
        **values,
    )


def _failed_item(
    session: Session,
    input_type: str,
    error: Exception | str,
    **values: Any,
) -> IntakeItem:
    session.rollback()
    item = _base_item(input_type, **values)
    item.status = "failed"
    item.error = str(error)
    session.add(item)
    session.commit()
    return item


def _clean_known(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value


def lock_intake_for_mutation(
    session: Session,
    item_id: str,
    *,
    action: str,
) -> IntakeItem:
    """Fence archive against an Intake mutation until this transaction commits.

    SQLite ignores ``FOR UPDATE``.  This deliberately uses a raw no-op DML as
    the transaction's first write: it takes SQLite's write lock (and a row lock
    on databases that support them) without changing ``updated_at`` or firing
    ORM on-update behavior.  Callers that crossed an await boundary must roll
    back their old read transaction before calling this function.
    """
    locked = session.execute(
        text(
            "UPDATE intake_items SET updated_at = updated_at "
            "WHERE id = :item_id AND archived_at IS NULL"
        ),
        {"item_id": item_id},
    )
    item = session.get(IntakeItem, item_id, populate_existing=True)
    if not locked.rowcount:
        if item is None:
            raise ValueError("Intake item not found")
        if item.archived_at is not None:
            raise ArchivedIntakeError(action)
        raise RuntimeError("Intake mutation lock could not be acquired")
    if item is None:
        raise ValueError("Intake item not found")
    return item


def lock_intake_for_status_sync(session: Session, item_id: str) -> IntakeItem:
    """Fence an Intake row while propagating its status to dependent rows.

    This includes archived rows, unlike ``lock_intake_for_mutation``.  It does
    not authorize changing Intake content; callers use the no-op write only so
    task/selection convergence is serialized with archive and restore.
    """
    locked = session.execute(
        text(
            "UPDATE intake_items SET updated_at = updated_at "
            "WHERE id = :item_id"
        ),
        {"item_id": item_id},
    )
    item = session.get(IntakeItem, item_id, populate_existing=True)
    if not locked.rowcount or item is None:
        raise ValueError("Intake item not found")
    return item


def _clear_candidate_generation_state(session: Session, item: IntakeItem) -> None:
    for candidate in list(item.candidates):
        session.delete(candidate)
    session.flush()
    item.candidate_error = None
    item.candidate_relations = []


def _generation_baseline(item: IntakeItem) -> tuple[str, str | None]:
    return item.status, iso(item.updated_at)


def _linked_topic_context(session: Session, item: IntakeItem) -> dict[str, Any] | None:
    link = session.scalar(
        select(InvestigationLink)
        .where(
            InvestigationLink.object_type == "intake",
            InvestigationLink.object_id == item.id,
        )
        .order_by(InvestigationLink.created_at.asc())
        .limit(1)
    )
    investigation = session.get(Investigation, link.investigation_id) if link else None
    if investigation is None:
        return None
    settings = investigation.settings_json or {}
    return {
        "title": investigation.title,
        "question": investigation.question,
        "event_start_at": iso(investigation.event_start_at),
        "event_end_at": iso(investigation.event_end_at),
        "tracking_mode": investigation.tracking_mode,
        "output_language": settings.get("report_language") or "zh-CN",
    }


def _model_snapshot(item: IntakeItem) -> str:
    return "\n".join(
        f"[{paragraph.id}] {paragraph.text}" for paragraph in paragraph_spans(item.extracted_snapshot)
    )


def _require_generation_baseline(
    item: IntakeItem,
    baseline: tuple[str, str | None],
    *,
    action: str,
) -> None:
    if _generation_baseline(item) != baseline:
        raise IntakeMutationConflictError(action)


async def generate_candidates(session: Session, item: IntakeItem) -> IntakeItem:
    """Generate and persist candidate objects without touching formal tables."""
    if item.archived_at is not None:
        raise ArchivedIntakeError("regenerating candidates")
    item_id = item.id
    baseline = _generation_baseline(item)
    topic_context = _linked_topic_context(session, item)
    payload = {
        "intake_item_id": item.id,
        "input_type": item.input_type,
        "topic_context": topic_context,
        "known_fields": {
            "title": _clean_known(item.title),
            "source_description": _clean_known(item.source_description),
            "source_url": _clean_known(item.source_url),
            "published_at": iso(item.published_at),
        },
        "snapshot": _model_snapshot(item),
        "output_contract": {
            "relevance": "relevant, uncertain, or not_relevant to topic_context; use relevant when topic_context is null",
            "event": "at most one main event; use null for unknown fields",
            "entities": "at most 8 key entities; use [] when unknown",
            "claims": "at most 5 concise Chinese propositions; each has 1-2 evidence items; claim.text must never copy evidence verbatim",
            "evidence": "snippet is exact source text without the [Pnnn] marker; paragraph_id is the matching marker",
        },
    }
    try:
        response = await run_model_task("extract_intake_candidates", payload)
        # End the pre-await read snapshot, then make the archive predicate the
        # first write of the transaction that stores candidates.  The fence is
        # held through the same commit as every candidate/status mutation.
        session.rollback()
        item = lock_intake_for_mutation(
            session,
            item_id,
            action="regenerating candidates",
        )
        _require_generation_baseline(
            item,
            baseline,
            action="applying the candidate result",
        )
        _clear_candidate_generation_state(session, item)
        if response.get("mode") == "fallback":
            result = deterministic_candidate_result(item)
            mode = "fallback"
            model_name = None
        else:
            result = response.get("result")
            if not isinstance(result, dict):
                raise ValueError("Model response result must be a JSON object")
            mode = "api"
            model_name = response.get("model") or os.getenv("LLM_MODEL_NAME", "configured-model")
        _store_candidates(session, item, result, mode, model_name)
        item.status = "candidate_ready"
        item.updated_at = utcnow()
    except ArchivedIntakeError:
        session.rollback()
        raise
    except IntakeMutationConflictError:
        session.rollback()
        raise
    except Exception as exc:
        # A model or persistence error may also race with archive.  Failure
        # state is an Intake mutation, so it needs a fresh transaction and the
        # same fence rather than overwriting an archive in an error handler.
        session.rollback()
        try:
            item = lock_intake_for_mutation(
                session,
                item_id,
                action="regenerating candidates",
            )
        except ArchivedIntakeError as archived_exc:
            session.rollback()
            raise archived_exc from exc
        try:
            _require_generation_baseline(
                item,
                baseline,
                action="recording the candidate failure",
            )
        except IntakeMutationConflictError as conflict_exc:
            session.rollback()
            raise conflict_exc from exc
        _clear_candidate_generation_state(session, item)
        item.status = "generation_failed"
        item.candidate_mode = "failed"
        item.candidate_error = str(exc)
        item.updated_at = utcnow()
    from .investigations import sync_linked_review_tasks_for_intake

    sync_linked_review_tasks_for_intake(
        session,
        item,
        actor="system:candidate-generation",
    )
    # _store_candidates writes child rows by foreign key, so a relationship that
    # was loaded as empty before generation would otherwise stay stale when
    # expire_on_commit=False. Expire it while the transaction is still open: the
    # response can then lazy-load the committed rows without a fallible refresh in
    # the post-commit durability window.
    session.expire(item, ["candidates"])
    session.commit()
    return item


def deterministic_candidate_result(item: IntakeItem) -> dict[str, Any]:
    """Build a visibly basic draft without pretending that a quote is a claim.

    The claim is a short proposition derived from the known document title;
    the evidence remains an exact snapshot substring.  This keeps the two
    user-facing concepts distinct even when the configured model is absent.
    """
    snapshot = item.extracted_snapshot
    sentences = [part.strip() for part in re.split(r"(?<=[.!?。！？])\s+", snapshot) if part.strip()]
    quote = next((part for part in sentences if len(part) >= 30), snapshot[:240].strip())
    known_title = _clean_known(item.title)
    claim_text = f"资料显示：{known_title}" if known_title else "该资料描述了一项需要进一步核实的事件进展"
    return {
        "event": {
            "title": known_title,
            "summary": snapshot[:500],
            "event_time": None,
            "location_name": None,
        },
        "entities": [],
        "claims": [{
            "text": claim_text,
            "uncertainty": "Basic draft derived from the document title; verify against the exact quote.",
            "evidence": [{"snippet": quote, "stance": "supports", "strength": 0.5}],
        }],
    }


def generate_deterministic_candidates(
    session: Session,
    item: IntakeItem,
    *,
    model_error: str,
) -> IntakeItem:
    """Persist an explicitly-labelled retryable basic draft after a model error."""
    baseline = _generation_baseline(item)
    item_id = item.id
    session.rollback()
    item = lock_intake_for_mutation(session, item_id, action="generating fallback candidates")
    _require_generation_baseline(item, baseline, action="applying fallback candidates")
    for candidate in list(item.candidates):
        session.delete(candidate)
    session.flush()
    _store_candidates(session, item, deterministic_candidate_result(item), "fallback-after-error", None)
    item.status = "candidate_ready"
    item.candidate_mode = "fallback-after-error"
    item.candidate_model = None
    item.candidate_error = model_error
    item.updated_at = utcnow()
    from .investigations import sync_linked_review_tasks_for_intake
    sync_linked_review_tasks_for_intake(session, item, actor="system:candidate-generation")
    session.expire(item, ["candidates"])
    session.commit()
    return item


def _candidate_machine_data(data: dict[str, Any], source_mode: str) -> dict[str, Any]:
    return {"fields": data, "source_mode": source_mode, "status": "machine-candidate"}


def _store_candidates(
    session: Session,
    item: IntakeItem,
    result: dict[str, Any],
    mode: str,
    model_name: str | None,
) -> None:
    item.candidate_mode = mode
    item.candidate_model = model_name
    relations: list[dict[str, str]] = []
    event = result.get("event")
    if not isinstance(event, dict):
        event = {}
    else:
        event = dict(event)
        event_time = event.get("event_time")
        if event_time is None:
            event_time = event.get("occurred_at") or event.get("start_at")
        for alias in ("occurred_at", "start_at", "published_at"):
            event.pop(alias, None)
        if (
            event_time is not None
            and (not isinstance(event_time, str) or event_time not in item.extracted_snapshot)
        ):
            event_time = None
        event["event_time"] = event_time
    event_key = "event"
    session.add(
        IntakeCandidate(
            id=f"{item.id}:event",
            item_id=item.id,
            candidate_key=event_key,
            object_type="event",
            source_mode=mode,
            machine_data=_candidate_machine_data(event, mode),
        )
    )
    relevance = result.get("relevance")
    if relevance == "unclear":
        relevance = "uncertain"
    if relevance not in {"relevant", "uncertain", "not_relevant"}:
        relevance = "relevant"
    review = dict(item.review or {})
    review["analysis"] = {
        "relevance": relevance,
        "model": model_name,
        "candidate_mode": mode,
        "reason": result.get("relevance_reason"),
    }
    item.review = review
    raw_entities = result.get("entities") if isinstance(result.get("entities"), list) else []
    raw_entities = raw_entities[:8] if relevance != "not_relevant" else []
    for idx, entity in enumerate(raw_entities):
        if not isinstance(entity, dict):
            continue
        entity = dict(entity)
        name = entity.get("name")
        if not isinstance(name, str) or not name.strip() or name not in item.extracted_snapshot:
            continue
        key = f"entity:{idx + 1}"
        session.add(
            IntakeCandidate(
                id=f"{item.id}:{key}",
                item_id=item.id,
                candidate_key=key,
                object_type="entity",
                source_mode=mode,
                machine_data=_candidate_machine_data(entity, mode),
            )
        )
        relations.append({"type": "event_entity", "from": key, "to": event_key})

    claims = result.get("claims")
    if not isinstance(claims, list):
        claims = []
    claims = claims[:5] if relevance != "not_relevant" else []
    evidence_index = 0
    for claim_idx, claim in enumerate(claims):
        if not isinstance(claim, dict):
            continue
        claim_key = f"claim:{claim_idx + 1}"
        evidence_items = claim.get("evidence")
        if not isinstance(evidence_items, list):
            evidence_items = []
        evidence_items = evidence_items[:2]
        claim_text = claim.get("text")
        if isinstance(claim_text, str) and any(
            isinstance(evidence.get("snippet"), str)
            and normalize_text(evidence["snippet"]) == normalize_text(claim_text)
            for evidence in evidence_items
            if isinstance(evidence, dict)
        ):
            raise ValueError("Claim text must summarize the information and must not duplicate an evidence quote")
        claim_fields = {
            "text": claim_text,
            "uncertainty": claim.get("uncertainty"),
            "temporal_scope": claim.get("temporal_scope"),
        }
        session.add(
            IntakeCandidate(
                id=f"{item.id}:{claim_key}",
                item_id=item.id,
                candidate_key=claim_key,
                object_type="claim",
                source_mode=mode,
                machine_data=_candidate_machine_data(claim_fields, mode),
            )
        )
        relations.append({"type": "event_claim", "from": claim_key, "to": event_key})
        for evidence in evidence_items:
            if not isinstance(evidence, dict):
                continue
            evidence_index += 1
            key = f"evidence:{evidence_index}"
            snippet = evidence.get("snippet")
            supplied_paragraph_id = evidence.get("paragraph_id")
            validation_error = None
            start_offset = end_offset = -1
            if not isinstance(snippet, str) or not snippet:
                validation_error = "Evidence snippet is missing"
            else:
                occurrences: list[tuple[int, int]] = []
                cursor = 0
                while True:
                    found = item.extracted_snapshot.find(snippet, cursor)
                    if found < 0:
                        break
                    occurrences.append((found, found + len(snippet)))
                    cursor = found + max(1, len(snippet))
                if not occurrences:
                    validation_error = "Evidence snippet is not an exact substring of the complete snapshot"
                else:
                    start_offset, end_offset = occurrences[0]
                    if isinstance(supplied_paragraph_id, str):
                        matching = next(
                            (
                                offsets
                                for offsets in occurrences
                                if paragraph_id_for_offset(
                                    item.extracted_snapshot, offsets[0], offsets[1]
                                )
                                == supplied_paragraph_id
                            ),
                            None,
                        )
                        if matching is not None:
                            start_offset, end_offset = matching
            paragraph_id = (
                paragraph_id_for_offset(item.extracted_snapshot, start_offset, end_offset)
                if start_offset >= 0
                else None
            )
            if (
                validation_error is None
                and supplied_paragraph_id is not None
                and supplied_paragraph_id != paragraph_id
            ):
                validation_error = "Evidence paragraph_id does not match the exact quote location"
            fields = {
                "snippet": snippet if isinstance(snippet, str) else "",
                "start_offset": start_offset,
                "end_offset": end_offset,
                "paragraph_id": paragraph_id,
                "stance": evidence.get("stance") or "context",
                "strength": evidence.get("strength", 0.5),
            }
            session.add(
                IntakeCandidate(
                    id=f"{item.id}:{key}",
                    item_id=item.id,
                    candidate_key=key,
                    object_type="evidence",
                    source_mode=mode,
                    machine_data=_candidate_machine_data(fields, mode),
                    validation_error=validation_error,
                )
            )
            relations.append(
                {
                    "type": "claim_evidence",
                    "from": key,
                    "to": claim_key,
                    "valid": validation_error is None,
                }
            )
    item.candidate_relations = relations
    session.flush()


async def submit_web_intake(
    session: Session,
    url: str,
    source_name: str | None,
    title: str | None,
    html: str | None,
    language: str,
    input_type: str = "web",
    review_extra: dict[str, Any] | None = None,
    *,
    defer_candidates: bool = False,
) -> IntakeItem:
    requested_url = str(url)
    common = {
        "source_url": requested_url,
        "language": language,
        "source_description": (source_name or "").strip(),
    }
    try:
        fetched_remotely = html is None
        canonical_url = canonicalize_url(requested_url)
        validate_public_http_url(canonical_url, resolve=html is None)
        resolved_url = canonical_url
        fetched_at = utcnow()
        fetch_method = "provided_html"
        fetched_metadata: dict[str, object] = {}
        if html is None:
            resolved_url, html = await fetch_public_text(canonical_url)
            resolved_url = canonicalize_url(resolved_url)
            fetch_method = "safe_http_or_reader"
            canonical_url = resolved_url
        if not html or not html.strip():
            raise ValueError("Fetched page is empty")
        page = extract_page(html, fallback_title=title or "", url=resolved_url)
        quality = assess_extraction(page)
        if (fetched_remotely and quality.status != "usable") or len(page.body) < 40:
            raise ValueError(
                "Extracted page body is too short or not usable: " + ", ".join(quality.reasons)
            )
        known_title = (page.title or title or str(fetched_metadata.get("title") or "")).strip() or None
        reader_published_at = fetched_metadata.get("published_at")
        published_at = page.published_at
        if published_at is None and isinstance(reader_published_at, str):
            try:
                published_at = parse_datetime(reader_published_at)
            except ValueError:
                published_at = None
        review: dict[str, Any] = {
            "material": extracted_material_metadata(
                page,
                resolved_url=resolved_url,
                fetched_at=fetched_at,
                fetch_method=fetch_method,
                fetch_metadata=fetched_metadata,
            )
        }
        if review_extra:
            review.update(review_extra)
        item = _base_item(
            input_type,
            source_description=(source_name or canonical_url.split("//", 1)[-1].split("/", 1)[0]).strip(),
            source_url=requested_url,
            canonical_url=canonical_url,
            title=known_title,
            published_at=published_at,
            language=language,
            raw_snapshot=html,
            raw_hash=sha256_text(html),
            extracted_snapshot=page.body,
            extracted_hash=content_hash(page.body),
            review=review,
        )
        session.add(item)
        session.commit()
        if defer_candidates:
            session.refresh(item)
            return item
        return await generate_candidates(session, item)
    except (ArchivedIntakeError, IntakeMutationConflictError):
        # Candidate generation crosses an await boundary.  If another request
        # archives the just-created item while the model is running, preserve
        # that item and let the HTTP layer report the restore-required conflict
        # instead of manufacturing a second, unrelated failed intake row.
        session.rollback()
        raise
    except Exception as exc:
        failure_html = html or ""
        failure_text = normalize_text(failure_html)
        return _failed_item(
            session,
            input_type,
            exc,
            raw_snapshot=failure_html,
            raw_hash=sha256_text(failure_html) if failure_html else "",
            extracted_snapshot=failure_text,
            extracted_hash=content_hash(failure_text) if failure_html else "",
            **common,
        )


async def submit_text_intake(
    session: Session,
    request: Any,
    *,
    defer_candidates: bool = False,
) -> IntakeItem:
    common = {
        "source_description": request.source_description.strip(),
        "title": _clean_known(request.title),
        "language": request.language,
    }
    try:
        text = normalize_structured_text(request.text)
        if len(text) < 10:
            raise ValueError("Pasted text is empty or too short")
        if len(request.source_description.strip()) < 3:
            raise ValueError("A source description of at least 3 characters is required")
        published_at = parse_datetime(request.published_at)
        item = _base_item(
            "text",
            published_at=published_at,
            raw_snapshot=request.text,
            raw_hash=sha256_text(request.text),
            extracted_snapshot=text,
            extracted_hash=content_hash(text),
            review={
                "material": {
                    "input_method": "browser-paste",
                    "paragraphs": paragraph_metadata(text),
                }
            },
            **common,
        )
        session.add(item)
        session.commit()
        session.refresh(item)
        if defer_candidates:
            return item
        return await generate_candidates(session, item)
    except (ArchivedIntakeError, IntakeMutationConflictError):
        session.rollback()
        raise
    except Exception as exc:
        normalized_failure = normalize_text(request.text)
        return _failed_item(
            session,
            "text",
            exc,
            raw_snapshot=request.text,
            raw_hash=sha256_text(request.text),
            extracted_snapshot=normalized_failure,
            extracted_hash=content_hash(normalized_failure),
            **common,
        )


def _extract_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - only reached in a degraded installation
        raise ValueError("PDF extraction dependency is unavailable") from exc
    try:
        reader = PdfReader(BytesIO(data), strict=True)
        if getattr(reader, "is_encrypted", False):
            raise ValueError("Encrypted PDFs are not supported")
        text = normalize_text(" ".join(page.extract_text() or "" for page in reader.pages))
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("PDF file is damaged or cannot be parsed") from exc
    if len(text) < 10:
        raise ValueError("PDF contains no extractable text")
    return text


async def submit_file_intake(
    session: Session,
    upload: Any,
    source_description: str,
    language: str,
    *,
    defer_candidates: bool = False,
) -> IntakeItem:
    filename = Path(upload.filename or "").name
    suffix = Path(filename).suffix.lower()
    common = {"source_description": source_description.strip(), "language": language}
    failure_values: dict[str, Any] = {
        "original_filename": filename or None,
        "media_type": (getattr(upload, "content_type", "") or "application/octet-stream")[:120],
        "size_bytes": None,
        "raw_snapshot": "",
        "raw_hash": "",
        "review": {"material": {"raw_encoding": "none"}},
    }
    try:
        data = await upload.read(MAX_FILE_BYTES + 1)
        failure_values.update(
            size_bytes=len(data),
            raw_snapshot=base64.b64encode(data).decode("ascii"),
            raw_hash=sha256_bytes(data),
            review={"material": {"raw_encoding": "base64"}},
        )
        if not filename or suffix not in SUPPORTED_FILE_SUFFIXES:
            raise ValueError(f"Unsupported file type; allowed: {', '.join(sorted(SUPPORTED_FILE_SUFFIXES))}")
        if not source_description or len(source_description.strip()) < 3:
            raise ValueError("A source description of at least 3 characters is required")
        if len(data) > MAX_FILE_BYTES:
            raise ValueError(f"File exceeds the {MAX_FILE_BYTES // (1024 * 1024)} MiB limit")
        if not data:
            raise ValueError("File is empty")
        media_type = SUPPORTED_FILE_SUFFIXES[suffix]
        failure_values["media_type"] = media_type
        if suffix == ".pdf":
            extracted = _extract_pdf(data)
            raw_snapshot = failure_values["raw_snapshot"]
            raw_hash = failure_values["raw_hash"]
            raw_encoding = "base64"
            material_metadata = {
                "fetch_method": "uploaded_pdf",
                "paragraphs": paragraph_metadata(extracted),
            }
        else:
            try:
                raw_text = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("Text file is not valid UTF-8") from exc
            raw_snapshot = raw_text
            raw_hash = sha256_text(raw_text)
            raw_encoding = "utf-8"
            failure_values.update(
                raw_snapshot=raw_snapshot,
                raw_hash=raw_hash,
                review={"material": {"raw_encoding": raw_encoding}},
            )
            if suffix in {".html", ".htm"}:
                page = extract_page(raw_text)
                extracted = page.body
                material_metadata = extracted_material_metadata(
                    page,
                    fetch_method="uploaded_html",
                )
            else:
                extracted = normalize_structured_text(raw_text)
                material_metadata = {
                    "fetch_method": "uploaded_file",
                    "paragraphs": paragraph_metadata(extracted),
                }
        if len(extracted) < 10:
            raise ValueError("File contains no extractable text")
        item = _base_item(
            "file",
            original_filename=filename,
            media_type=media_type,
            size_bytes=len(data),
            raw_snapshot=raw_snapshot,
            raw_hash=raw_hash,
            extracted_snapshot=extracted,
            extracted_hash=content_hash(extracted),
            review={"material": {**material_metadata, "raw_encoding": raw_encoding}},
            **common,
        )
        session.add(item)
        session.commit()
        session.refresh(item)
        if defer_candidates:
            return item
        return await generate_candidates(session, item)
    except (ArchivedIntakeError, IntakeMutationConflictError):
        session.rollback()
        raise
    except Exception as exc:
        return _failed_item(
            session,
            "file",
            exc,
            **failure_values,
            **common,
        )


def _rss_value(node: ElementTree.Element | None, default: str = "") -> str:
    if node is None:
        return default
    return normalize_text("".join(node.itertext()))


def _rss_find(node: ElementTree.Element, *paths: str) -> ElementTree.Element | None:
    for path in paths:
        found = node.find(path)
        if found is not None:
            return found
    return None


async def submit_rss_intake(
    session: Session,
    url: str | None,
    xml: str | None,
    source_name: str,
    language: str,
    *,
    defer_candidates: bool = False,
) -> list[IntakeItem]:
    try:
        if not xml:
            if not url:
                raise ValueError("RSS url or xml is required")
            _, xml = await fetch_public_text(url)
        try:
            root = ElementTree.fromstring(xml)
        except ElementTree.ParseError as exc:
            raise ValueError("RSS XML is malformed") from exc
        items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
        if not items:
            raise ValueError("RSS feed contains no items")
        results: list[IntakeItem] = []
        for node in items[:50]:
            title_node = _rss_find(node, "title", "{http://www.w3.org/2005/Atom}title")
            link_node = _rss_find(node, "link", "{http://www.w3.org/2005/Atom}link")
            description_node = _rss_find(
                node,
                "description",
                "summary",
                "{http://www.w3.org/2005/Atom}summary",
            )
            title = _rss_value(title_node, "Untitled RSS item")
            link = _rss_value(link_node) or (link_node.attrib.get("href", "") if link_node is not None else "")
            description = _rss_value(description_node, title)
            if not link:
                digest = hashlib.sha1(title.encode("utf-8")).hexdigest()[:12]
                link = f"https://example.org/.well-known/pldr-rss/{digest}"
            synthetic_html = (
                "<html><head><title>"
                + html_lib.escape(title)
                + "</title></head><body><article><p>"
                + html_lib.escape(description)
                + "</p></article></body></html>"
            )
            results.append(
                await submit_web_intake(
                    session,
                    link,
                    source_name,
                    title,
                    synthetic_html,
                    language,
                    input_type="rss",
                    defer_candidates=defer_candidates,
                )
            )
        return results
    except (ArchivedIntakeError, IntakeMutationConflictError):
        session.rollback()
        raise
    except Exception as exc:
        failure_xml = xml or ""
        failure_text = normalize_text(failure_xml)
        return [
            _failed_item(
                session,
                "rss",
                exc,
                source_url=url,
                source_description=source_name,
                language=language,
                raw_snapshot=failure_xml,
                raw_hash=sha256_text(failure_xml) if failure_xml else "",
                extracted_snapshot=failure_text,
                extracted_hash=content_hash(failure_text) if failure_xml else "",
            )
        ]


def get_intake_item(session: Session, item_id: str) -> IntakeItem | None:
    return session.scalar(
        select(IntakeItem)
        .where(IntakeItem.id == item_id)
        .options(selectinload(IntakeItem.candidates))
    )


def serialize_candidate(candidate: IntakeCandidate) -> dict[str, Any]:
    return {
        "id": candidate.id,
        "candidate_key": candidate.candidate_key,
        "object_type": candidate.object_type,
        "source_mode": candidate.source_mode,
        "machine": candidate.machine_data,
        "human": candidate.human_data,
        "validation_error": candidate.validation_error,
        "disposition": candidate.disposition,
        "reviewed_at": iso(candidate.reviewed_at),
        "final_object_id": candidate.final_object_id,
    }


def _intake_has_active_review_task(session: Session, item_id: str) -> bool:
    return session.scalar(
        select(ReviewTask.id)
        .where(
            ReviewTask.intake_item_id == item_id,
            ReviewTask.status.in_(ACTIVE_REVIEW_TASK_STATUSES),
        )
        .limit(1)
    ) is not None


def _intake_allowed_actions(item: IntakeItem, session: Session | None = None) -> list[str]:
    if item.archived_at is not None:
        return ["restore"]
    if item.status == "confirmed":
        return []
    if session is not None and _intake_has_active_review_task(session, item.id):
        return []
    return ["archive"]


def _intake_archive_payload(item: IntakeItem, session: Session | None = None) -> dict[str, Any]:
    return {
        "archived": item.archived_at is not None,
        "archived_at": iso(item.archived_at),
        "archived_by": item.archived_by,
        "archive_reason": item.archive_reason,
        "allowed_actions": _intake_allowed_actions(item, session),
    }


def _record_intake_archive_action(
    session: Session,
    item: IntakeItem,
    *,
    action: str,
    analyst: str,
    reason: str,
) -> None:
    from .investigations import record_action

    investigation_ids = list(
        session.scalars(
            select(InvestigationLink.investigation_id).where(
                InvestigationLink.object_type == "intake",
                InvestigationLink.object_id == item.id,
            )
        )
    )
    for investigation_id in investigation_ids:
        record_action(
            session,
            investigation_id,
            action,
            actor=analyst,
            object_type="intake",
            object_id=item.id,
            detail={"reason": reason, "status": item.status},
        )


def archive_intake(
    session: Session,
    item: IntakeItem,
    *,
    analyst: str,
    reason: str,
) -> tuple[IntakeItem, bool]:
    """Hide an unconfirmed, inactive intake without changing its processing state."""
    item_id = item.id
    now = utcnow()
    # Discard the route's read snapshot.  This conditional write is both the
    # idempotency CAS and SQLite's write fence; task creation/claim and Intake
    # mutations use the inverse unarchived fence and hold it to their commit.
    session.rollback()
    archive_statement = text(
        "UPDATE intake_items "
        "SET archived_at = :archived_at, archived_by = :archived_by, "
        "archive_reason = :archive_reason, updated_at = :updated_at "
        "WHERE id = :item_id AND archived_at IS NULL "
        "AND status <> 'confirmed' "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM investigation_review_tasks "
        "  WHERE intake_item_id = :item_id "
        "  AND status IN ('queued', 'fetching', 'generating')"
        ")"
    ).bindparams(
        bindparam("archived_at", type_=DateTime(timezone=True)),
        bindparam("updated_at", type_=DateTime(timezone=True)),
    )
    archived = session.execute(
        archive_statement,
        {
            "item_id": item_id,
            "archived_at": now,
            "archived_by": analyst,
            "archive_reason": reason,
            "updated_at": now,
        },
    )
    item = session.get(IntakeItem, item_id, populate_existing=True)
    if item is None:
        raise ValueError("Intake item not found")
    if not archived.rowcount:
        if item.archived_at is not None:
            return item, False
        if item.status == "confirmed":
            raise ValueError("A confirmed intake item cannot be archived")
        if _intake_has_active_review_task(session, item.id):
            raise ValueError("An intake item with an active review task cannot be archived")
        raise RuntimeError("Intake archive compare-and-set did not match")
    review = dict(item.review or {})
    history = list(review.get("archive_history", []))
    history.append(
        {
            "action": "archived",
            "analyst": analyst,
            "reason": reason,
            "at": iso(now),
            "status": item.status,
        }
    )
    review["archive_history"] = history
    item.review = review
    _record_intake_archive_action(
        session,
        item,
        action="intake.archived",
        analyst=analyst,
        reason=reason,
    )
    session.commit()
    session.refresh(item)
    return item, True


def restore_intake(
    session: Session,
    item: IntakeItem,
    *,
    analyst: str,
    reason: str,
) -> tuple[IntakeItem, bool]:
    """Restore inbox visibility while preserving the original processing state."""
    item_id = item.id
    session.rollback()
    locked = session.execute(
        text(
            "UPDATE intake_items SET updated_at = updated_at "
            "WHERE id = :item_id AND archived_at IS NOT NULL"
        ),
        {"item_id": item_id},
    )
    item = session.get(IntakeItem, item_id, populate_existing=True)
    if item is None:
        raise ValueError("Intake item not found")
    if not locked.rowcount:
        if item.archived_at is None:
            return item, False
        raise RuntimeError("Intake restore compare-and-set did not match")
    now = utcnow()
    previous_archive = {
        "archived_at": iso(item.archived_at),
        "archived_by": item.archived_by,
        "archive_reason": item.archive_reason,
    }
    item.archived_at = None
    item.archived_by = None
    item.archive_reason = None
    item.updated_at = now
    review = dict(item.review or {})
    history = list(review.get("archive_history", []))
    history.append(
        {
            "action": "restored",
            "analyst": analyst,
            "reason": reason,
            "at": iso(now),
            "status": item.status,
            "previous_archive": previous_archive,
        }
    )
    review["archive_history"] = history
    item.review = review
    _record_intake_archive_action(
        session,
        item,
        action="intake.restored",
        analyst=analyst,
        reason=reason,
    )
    session.commit()
    session.refresh(item)
    return item, True


def serialize_intake_summary(
    item: IntakeItem, *, session: Session | None = None
) -> dict[str, Any]:
    """List-safe material metadata; never embeds snapshots or candidate blobs."""
    return {
        "id": item.id,
        "input_type": item.input_type,
        "status": item.status,
        "error": item.error,
        "source": {
            "description": item.source_description or None,
            "url": item.source_url,
            "canonical_url": item.canonical_url,
            "known": bool(item.source_description or item.canonical_url),
        },
        "title": _clean_known(item.title),
        "published_at": iso(item.published_at),
        "language": item.language,
        "created_at": iso(item.created_at),
        "updated_at": iso(item.updated_at),
        **_intake_archive_payload(item, session),
        "search": item.review.get("external_search") or None,
        "analysis": item.review.get("analysis") or None,
        "candidate_generation": {
            "mode": item.candidate_mode,
            "model": item.candidate_model,
            "error": item.candidate_error,
        },
        "candidate_count": len(item.candidates),
        "final_object_ids": {
            "event": item.final_event_id,
            "document": item.final_document_id,
            "snapshot": item.final_snapshot_id,
        },
    }


def serialize_intake(
    item: IntakeItem, *, session: Session | None = None
) -> dict[str, Any]:
    candidates = [serialize_candidate(candidate) for candidate in item.candidates]
    return {
        "id": item.id,
        "input_type": item.input_type,
        "status": item.status,
        "error": item.error,
        "source": {
            "description": item.source_description or None,
            "url": item.source_url,
            "canonical_url": item.canonical_url,
            "known": bool(item.source_description or item.canonical_url),
        },
        "title": _clean_known(item.title),
        "published_at": iso(item.published_at),
        "language": item.language,
        "file": {
            "name": item.original_filename,
            "media_type": item.media_type,
            "size_bytes": item.size_bytes,
        },
        "created_at": iso(item.created_at),
        "updated_at": iso(item.updated_at),
        **_intake_archive_payload(item, session),
        "material": {
            "raw_hash": item.raw_hash or None,
            "extracted_hash": item.extracted_hash or None,
            "raw_snapshot": item.raw_snapshot,
            "extracted_snapshot": item.extracted_snapshot,
            **item.review.get("material", {}),
        },
        "search": item.review.get("external_search") or None,
        "search_history": item.review.get("external_search_history") or [],
        "analysis": item.review.get("analysis") or None,
        "candidate_generation": {
            "mode": item.candidate_mode,
            "model": item.candidate_model,
            "error": item.candidate_error,
            "relations": item.candidate_relations,
        },
        "candidates": candidates,
        "review": item.review,
        "confirmation_result": item.confirmation_result or None,
        "disposition": item.disposition,
        "reviewed_by": item.reviewed_by,
        "reviewed_at": iso(item.reviewed_at),
        "rejection_reason": item.rejection_reason,
        "final_object_ids": {
            "event": item.final_event_id,
            "document": item.final_document_id,
            "snapshot": item.final_snapshot_id,
        },
    }


def _candidate_map(item: IntakeItem) -> dict[str, IntakeCandidate]:
    return {candidate.candidate_key: candidate for candidate in item.candidates}


def _confirmation_fingerprint(request: IntakeConfirmationRequest) -> str:
    payload = json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _machine_event(item: IntakeItem) -> dict[str, Any]:
    candidate = _candidate_map(item).get("event")
    return candidate.machine_data.get("fields", {}) if candidate else {}


def _difference(machine: dict[str, Any], human: dict[str, Any]) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    for key, value in human.items():
        if machine.get(key) != value:
            changes[key] = {"machine": machine.get(key), "human": value}
    return changes


def validate_confirmation(
    session: Session,
    item: IntakeItem,
    request: IntakeConfirmationRequest,
) -> list[str]:
    errors: list[str] = []
    if item.archived_at is not None:
        errors.append("Archived intake items must be restored before confirmation")
    if item.status != "candidate_ready":
        errors.append(f"Item is not candidate_ready (current status: {item.status})")
    if request.disposition == "merge":
        if not request.merge_event_id:
            errors.append("merge disposition requires merge_event_id")
        elif session.get(Event, request.merge_event_id) is None:
            errors.append("Selected merge event does not exist")
    if request.disposition != "merge" and not request.event.title.strip():
        errors.append("A known event title is required to create or modify a new formal event")
    if request.disposition != "merge" and request.event.start_at:
        try:
            parse_datetime(request.event.start_at)
        except ValueError:
            errors.append("Event start time must be a valid ISO-8601 datetime")
    if request.disposition == "modify":
        machine_event = _machine_event(item)
        if not _difference(machine_event, request.event.model_dump(mode="json")):
            errors.append("Modify disposition must contain an explicit human change")

    candidates = _candidate_map(item)
    selected_claims = {decision.candidate_key for decision in request.claims if decision.action != "exclude"}
    selected_evidence = {decision.candidate_key for decision in request.evidence if decision.action == "include"}
    final_event_id = request.merge_event_id if request.disposition == "merge" else None
    if not selected_claims:
        errors.append("At least one claim candidate must be selected")
    if not selected_evidence:
        errors.append("At least one evidence candidate must be selected")

    for decision in request.entities:
        candidate = candidates.get(decision.candidate_key)
        if candidate is None or candidate.object_type != "entity":
            errors.append(f"Unknown entity candidate: {decision.candidate_key}")
            continue
        if decision.action == "merge":
            if not decision.merge_entity_id or session.get(Entity, decision.merge_entity_id) is None:
                errors.append(f"Invalid entity merge target for {decision.candidate_key}")
        elif decision.action == "create" and not decision.name.strip():
            errors.append(f"Entity name is required for {decision.candidate_key}")

    for decision in request.claims:
        candidate = candidates.get(decision.candidate_key)
        if candidate is None or candidate.object_type != "claim":
            errors.append(f"Unknown claim candidate: {decision.candidate_key}")
            continue
        if decision.action == "exclude":
            continue
        if not decision.text.strip():
            errors.append(f"Claim text is required for {decision.candidate_key}")
        if decision.action == "merge":
            claim_target = session.get(Claim, decision.merge_claim_id) if decision.merge_claim_id else None
            if claim_target is None:
                errors.append(f"Invalid claim merge target for {decision.candidate_key}")
            elif final_event_id is None or claim_target.event_id != final_event_id:
                errors.append(f"Claim merge target for {decision.candidate_key} must belong to the selected final event")

    relation_claim = {
        relation["from"]: relation["to"]
        for relation in item.candidate_relations
        if relation["type"] == "claim_evidence"
    }
    for decision in request.evidence:
        candidate = candidates.get(decision.candidate_key)
        if candidate is None or candidate.object_type != "evidence":
            errors.append(f"Unknown evidence candidate: {decision.candidate_key}")
            continue
        if decision.action == "exclude":
            continue
        if candidate.validation_error:
            errors.append(f"{decision.candidate_key}: {candidate.validation_error}")
            continue
        if relation_claim.get(decision.candidate_key) not in selected_claims:
            errors.append(f"{decision.candidate_key} is selected without its parent claim")
        start = item.extracted_snapshot.find(decision.snippet)
        end = start + len(decision.snippet)
        if start < 0 or item.extracted_snapshot[start:end] != decision.snippet:
            errors.append(f"{decision.candidate_key} cannot be precisely located in the complete snapshot")
    return errors


def build_confirmation_preview(
    session: Session,
    item: IntakeItem,
    request: IntakeConfirmationRequest,
) -> dict[str, Any]:
    errors = validate_confirmation(session, item, request)
    merge_event = session.get(Event, request.merge_event_id) if request.merge_event_id else None
    if merge_event is not None:
        merge_metadata = merge_event.metadata_json or {}
        event_preview = {
            "id": merge_event.id,
            "title": merge_event.title,
            "summary": merge_event.summary,
            "event_type": merge_event.event_type,
            "start_at": None if merge_metadata.get("start_at_known") is False else iso(merge_event.start_at),
            "location_name": merge_event.location_name,
            "importance": merge_event.importance,
            "action": "merge",
        }
    else:
        try:
            normalized_event = _normalized_new_event_fields(item, request)
        except ValueError:
            normalized_event = {
                "title": request.event.title.strip(),
                "summary": request.event.summary.strip() or DEFAULT_EVENT_SUMMARY,
                "event_type": request.event.event_type,
                "start_at": None,
                "start_at_known": False,
                "location_name": request.event.location_name if request.event.location_name != "Unknown" else "",
                "importance": request.event.importance,
            }
        event_preview = {
            "id": None,
            "title": normalized_event["title"],
            "summary": normalized_event["summary"],
            "event_type": normalized_event["event_type"],
            "start_at": iso(normalized_event["start_at"]) if normalized_event["start_at_known"] else None,
            "location_name": normalized_event["location_name"],
            "importance": normalized_event["importance"],
            "action": "create" if request.disposition == "create" else "create-modified",
        }

    entity_previews: list[dict[str, Any]] = []
    for decision in request.entities:
        if decision.action == "exclude":
            continue
        target = session.get(Entity, decision.merge_entity_id) if decision.action == "merge" and decision.merge_entity_id else None
        entity_previews.append({
            "action": decision.action,
            "name": target.name if target is not None else decision.name.strip(),
            "entity_type": target.entity_type if target is not None else decision.entity_type,
            "aliases": list(target.aliases or []) if target is not None else decision.aliases,
            "role": decision.role,
            "merge_entity_id": decision.merge_entity_id,
        })

    claim_previews: list[dict[str, Any]] = []
    for decision in request.claims:
        if decision.action == "exclude":
            continue
        target = session.get(Claim, decision.merge_claim_id) if decision.action == "merge" and decision.merge_claim_id else None
        claim_previews.append({
            "action": decision.action,
            "text": target.text if target is not None else decision.text.strip(),
            "status": target.status if target is not None else decision.status,
            "confidence": target.confidence if target is not None else decision.confidence,
            "temporal_scope": target.temporal_scope if target is not None else decision.temporal_scope,
            "merge_claim_id": decision.merge_claim_id,
        })

    evidence_previews = [
        {
            "snippet": decision.snippet,
            "stance": decision.stance,
            "strength": decision.strength,
            "note": decision.note or DEFAULT_EVIDENCE_NOTE,
            "snapshot_trace": {
                "start_offset": item.extracted_snapshot.find(decision.snippet),
                "end_offset": item.extracted_snapshot.find(decision.snippet) + len(decision.snippet),
            },
        }
        for decision in request.evidence
        if decision.action == "include"
    ]
    preview = {
        "confirmable": not errors,
        "errors": errors,
        "disposition": request.disposition,
        "formal": {
            "source": {
                "name": item.source_description or item.canonical_url or "Unknown source",
                "url": item.canonical_url or item.source_url,
                "known": bool(item.source_description or item.canonical_url),
            },
            "document": {
                "title": item.title or UNKNOWN_TITLE,
                "title_known": bool(item.title),
                "published_at": iso(item.published_at),
                "content_hash": item.extracted_hash,
            },
            "snapshot": {"content_hash": item.extracted_hash, "length": len(item.extracted_snapshot)},
            "event": event_preview,
            "entities": entity_previews,
            "claims": claim_previews,
            "evidence": evidence_previews,
        },
        "trace": {
            "intake_item_id": item.id,
            "machine_candidate_source": item.candidate_mode,
            "human_disposition": request.disposition,
            "analyst": request.analyst,
        },
    }
    formal = preview["formal"]
    degraded = item.candidate_mode in {"fallback", "fallback-after-error"}
    selected = {
        decision.candidate_key
        for decision in [*request.claims, *request.evidence]
        if decision.action not in {"exclude"}
    }
    preview["semantic_preview"] = {
        "source": {**formal["source"], "action": "reuse_or_create"},
        "document": {**formal["document"], "action": "create_or_update"},
        "snapshot": {**formal["snapshot"], "action": "append_immutable"},
        "event": formal["event"],
        "entities": formal["entities"],
        "claims": formal["claims"],
        "evidence": formal["evidence"],
        "relations": [
            {"type": "event_document", "from": "event", "to": "document"},
            *[
                dict(relation)
                for relation in item.candidate_relations
                if relation.get("type") == "claim_evidence"
                and relation.get("from") in selected
                and relation.get("to") in selected
            ],
        ],
        "actions": [
            "保存来源、文档和不可变正文快照",
            "并入已有事件" if formal["event"]["action"] == "merge" else "创建正式事件",
            f"处理 {len(formal['claims'])} 条主张和 {len(formal['evidence'])} 条证据",
            "保留机器候选、人工修改和正式对象之间的审计记录",
        ],
        "candidate_generation": {
            "mode": item.candidate_mode,
            "degraded": degraded,
            "warning": "规则降级候选必须人工核对。" if degraded else None,
        },
    }
    return preview


def _origin(item: IntakeItem) -> tuple[str, str]:
    if item.canonical_url or item.source_url:
        url = item.canonical_url or item.source_url or ""
        host = url.split("//", 1)[-1].split("/", 1)[0] or "unknown"
        return url, host
    identity = item.source_description.strip() or item.raw_hash or item.id
    return f"pldr:unknown-source:{hashlib.sha1(identity.encode('utf-8')).hexdigest()[:20]}", hashlib.sha1(identity.encode("utf-8")).hexdigest()[:20]


def _get_or_create_intake_source(session: Session, item: IntakeItem) -> Source:
    url, group = _origin(item)
    name = item.source_description.strip() or (group if item.canonical_url or item.source_url else "Unknown source")
    source_id = "src_intake_" + hashlib.sha1(f"{name}:{group}".encode("utf-8")).hexdigest()[:16]
    source = session.get(Source, source_id)
    if source is None:
        base_url = url if url.startswith("pldr:") else "/".join(url.split("/", 3)[:3])
        source = Source(
            id=source_id,
            name=name,
            base_url=base_url,
            country="",
            language=item.language,
            source_type=f"intake-{item.input_type}",
            reliability_tier=4,
            independence_group=f"intake:{group}",
            status="healthy",
            last_success_at=utcnow(),
        )
        session.add(source)
        session.flush()
    return source


def _intake_capture_time(item: IntakeItem) -> datetime:
    """Return the time represented by the submitted material, with a safe fallback."""
    material = (item.review or {}).get("material")
    fetched_at = material.get("fetched_at") if isinstance(material, dict) else None
    if isinstance(fetched_at, str):
        try:
            return parse_datetime(fetched_at) or item.created_at
        except ValueError:
            # Review metadata is trace data, not a reason to make an otherwise valid
            # human confirmation impossible.
            pass
    return item.created_at


def _latest_document_snapshot(document: Document) -> Snapshot | None:
    metadata = document.metadata_json or {}
    latest_id = metadata.get("latest_snapshot_id")
    if isinstance(latest_id, str):
        latest = next((snapshot for snapshot in document.snapshots if snapshot.id == latest_id), None)
        if latest is not None:
            return latest
    return max(
        document.snapshots,
        key=lambda snapshot: (iso(snapshot.captured_at) or "", snapshot.id),
        default=None,
    )


def _snapshot_metadata(
    item: IntakeItem,
    *,
    duplicate_of_document_id: str | None = None,
    similar_to_document_id: str | None = None,
    similarity: float | None = None,
) -> dict[str, Any]:
    metadata = {
        "intake_item_id": item.id,
        "input_type": item.input_type,
        "confirmation_stage": "P0.3-human-confirmed",
        "title": item.title,
        "title_known": bool(item.title),
        "published_at": iso(item.published_at),
        "published_at_known": item.published_at is not None,
        "language": item.language,
        "source_description": item.source_description,
        "source_url": item.source_url,
        "canonical_url": item.canonical_url,
        "collection": (item.review or {}).get("collection"),
    }
    if duplicate_of_document_id is not None:
        metadata["duplicate_of_document_id"] = duplicate_of_document_id
        metadata["duplicate_kind"] = "exact"
    elif similar_to_document_id is not None:
        metadata["similar_to_document_id"] = similar_to_document_id
        metadata["similarity"] = round(similarity or 0.0, 4)
        metadata["duplicate_kind"] = "near"
    return metadata


def _near_duplicate_document(
    session: Session, item: IntakeItem, *, exclude_id: str | None = None
) -> tuple[Document | None, float]:
    if len(normalize_text(item.extracted_snapshot)) < 250:
        return None, 0.0
    threshold = float(os.getenv("PLDR_NEAR_DUPLICATE_THRESHOLD", "0.82"))
    query = select(Document).order_by(Document.fetched_at.desc()).limit(200)
    if exclude_id:
        query = query.where(Document.id != exclude_id)
    best: Document | None = None
    best_score = 0.0
    for document in session.scalars(query):
        score = near_duplicate_similarity(item.extracted_snapshot, document.body or "")
        if score > best_score:
            best, best_score = document, score
    return (best, best_score) if best is not None and best_score >= threshold else (None, best_score)


def _duplicate_root(session: Session, document: Document) -> Document:
    current = document
    seen = {current.id}
    while True:
        parent_id = (current.metadata_json or {}).get("duplicate_of_document_id")
        if not isinstance(parent_id, str) or parent_id in seen:
            return current
        parent = session.get(Document, parent_id)
        if parent is None:
            return current
        seen.add(parent.id)
        current = parent


def _exact_duplicate_document(
    session: Session, content_digest: str, *, exclude_id: str | None = None
) -> Document | None:
    candidates = list(
        session.scalars(
            select(Document)
            .where(Document.content_hash == content_digest)
            .order_by(Document.fetched_at.asc(), Document.id.asc())
        )
    )
    returned: set[str] = set()
    for candidate in candidates:
        if candidate.id == exclude_id:
            continue
        root = _duplicate_root(session, candidate)
        if root.id == exclude_id or root.id in returned:
            continue
        returned.add(root.id)
        return root
    return None


def _aware_datetime(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _collection_order(value: Any) -> tuple[str, int] | None:
    if not isinstance(value, dict):
        return None
    target_id = value.get("target_id")
    version_number = value.get("version_number")
    if not isinstance(target_id, str) or not isinstance(version_number, int):
        return None
    return target_id, version_number


def _should_advance_document_head(
    document: Document,
    item: IntakeItem,
    snapshot: Snapshot,
    current: Snapshot | None,
) -> bool:
    if current is None:
        return True
    current_order = _collection_order((document.metadata_json or {}).get("latest_collection"))
    candidate_order = _collection_order((item.review or {}).get("collection"))
    if (
        current_order is not None
        and candidate_order is not None
        and current_order[0] == candidate_order[0]
        and current_order[1] != candidate_order[1]
    ):
        return candidate_order[1] > current_order[1]
    # Manual/imported material has no collector version number. Capture time is the
    # only stable ordering available; equal timestamps keep the existing head rather
    # than letting review order arbitrarily replace it.
    return _aware_datetime(snapshot.captured_at) > _aware_datetime(current.captured_at)


def _updated_document_metadata(
    document: Document,
    item: IntakeItem,
    snapshot: Snapshot,
    *,
    advance_head: bool,
) -> dict[str, Any]:
    metadata = dict(document.metadata_json or {})
    intake_ids = list(metadata.get("intake_item_ids", []))
    original_intake_id = metadata.get("intake_item_id")
    if original_intake_id is not None and original_intake_id not in intake_ids:
        intake_ids.insert(0, original_intake_id)
    if item.id not in intake_ids:
        intake_ids.append(item.id)
    metadata["confirmation_stage"] = "P0.3-human-confirmed"
    metadata["intake_item_ids"] = intake_ids
    if advance_head:
        metadata.update(
            {
                "input_type": item.input_type,
                "title_known": bool(item.title),
                "published_at_known": item.published_at is not None,
                "source_description": item.source_description,
                "raw_hash": item.raw_hash,
                "latest_intake_item_id": item.id,
                "latest_snapshot_id": snapshot.id,
            }
        )
        collection = (item.review or {}).get("collection")
        if collection is not None:
            metadata["latest_collection"] = collection
        else:
            metadata.pop("latest_collection", None)
        duplicate_of = (snapshot.metadata_json or {}).get("duplicate_of_document_id")
        similar_to = (snapshot.metadata_json or {}).get("similar_to_document_id")
        if duplicate_of is not None:
            metadata["duplicate_of_document_id"] = duplicate_of
            metadata["duplicate_kind"] = "exact"
            metadata.pop("similar_to_document_id", None)
            metadata.pop("similarity", None)
        elif similar_to is not None:
            metadata["similar_to_document_id"] = similar_to
            metadata["similarity"] = (snapshot.metadata_json or {}).get("similarity")
            metadata["duplicate_kind"] = "near"
            metadata.pop("duplicate_of_document_id", None)
        else:
            metadata.pop("duplicate_of_document_id", None)
            metadata.pop("similar_to_document_id", None)
            metadata.pop("similarity", None)
            metadata.pop("duplicate_kind", None)
    return metadata


def _create_formal_document(session: Session, item: IntakeItem) -> Document:
    canonical_url = item.canonical_url or item.source_url or f"pldr:intake/{item.id}"
    existing = session.scalar(select(Document).where(Document.canonical_url == canonical_url))
    if existing is not None:
        # One canonical URL identifies one formal Document. Every newly confirmed
        # Intake is its own immutable capture (even if the body is identical), while
        # Evidence points to exactly that capture. Review order must not make an older
        # collector version replace a newer formal head.
        current_head = _latest_document_snapshot(existing)
        duplicate = _exact_duplicate_document(
            session, item.extracted_hash, exclude_id=existing.id
        )
        similar, similarity = (None, 0.0) if duplicate else _near_duplicate_document(
            session, item, exclude_id=existing.id
        )
        snapshot_id = "snap_intake_" + hashlib.sha1(
            f"{existing.id}:{item.id}:{item.extracted_hash}".encode("utf-8")
        ).hexdigest()[:20]
        snapshot = Snapshot(
            id=snapshot_id,
            document_id=existing.id,
            captured_at=_intake_capture_time(item),
            content_hash=item.extracted_hash,
            excerpt=item.extracted_snapshot,
            storage_path="inline-intake-version",
            metadata_json=_snapshot_metadata(
                item,
                duplicate_of_document_id=duplicate.id if duplicate else None,
                similar_to_document_id=similar.id if similar else None,
                similarity=similarity,
            ),
        )
        session.add(snapshot)
        session.flush()
        advance_head = _should_advance_document_head(existing, item, snapshot, current_head)
        if advance_head:
            existing.title = item.title or UNKNOWN_TITLE
            existing.body = item.extracted_snapshot
            existing.published_at = item.published_at or UNKNOWN_DATETIME
            existing.fetched_at = _intake_capture_time(item)
            existing.language = item.language
            existing.content_hash = item.extracted_hash
            existing.is_cached = True
        existing.metadata_json = _updated_document_metadata(
            existing,
            item,
            snapshot,
            advance_head=advance_head,
        )
        item.final_document_id = existing.id
        item.final_snapshot_id = snapshot.id
        return existing
    source = _get_or_create_intake_source(session, item)
    document_id = "doc_intake_" + hashlib.sha1(f"{item.id}:{item.extracted_hash}".encode("utf-8")).hexdigest()[:14]
    duplicate = _exact_duplicate_document(session, item.extracted_hash)
    similar, similarity = (None, 0.0) if duplicate else _near_duplicate_document(session, item)
    snapshot_id = "snap_intake_" + hashlib.sha1(document_id.encode("utf-8")).hexdigest()[:16]
    metadata = {
        "intake_item_id": item.id,
        "input_type": item.input_type,
        "title_known": bool(item.title),
        "published_at_known": item.published_at is not None,
        "source_description": item.source_description,
        "raw_hash": item.raw_hash,
        "confirmation_stage": "P0.3-human-confirmed",
        "intake_item_ids": [item.id],
        "latest_intake_item_id": item.id,
        "latest_snapshot_id": snapshot_id,
    }
    if (item.review or {}).get("collection") is not None:
        metadata["latest_collection"] = item.review["collection"]
    if duplicate is not None:
        metadata["duplicate_of_document_id"] = duplicate.id
        metadata["duplicate_kind"] = "exact"
    elif similar is not None:
        metadata["similar_to_document_id"] = similar.id
        metadata["similarity"] = round(similarity, 4)
        metadata["duplicate_kind"] = "near"
    document = Document(
        id=document_id,
        source_id=source.id,
        canonical_url=canonical_url,
        title=item.title or UNKNOWN_TITLE,
        body=item.extracted_snapshot,
        published_at=item.published_at or UNKNOWN_DATETIME,
        fetched_at=_intake_capture_time(item),
        language=item.language,
        content_hash=item.extracted_hash,
        upstream_story_id="",
        is_cached=True,
        metadata_json=metadata,
    )
    session.add(document)
    session.flush()
    session.add(
        Snapshot(
            id=snapshot_id,
            document_id=document.id,
            captured_at=_intake_capture_time(item),
            content_hash=item.extracted_hash,
            excerpt=item.extracted_snapshot,
            storage_path="inline-intake",
            metadata_json=_snapshot_metadata(
                item,
                duplicate_of_document_id=duplicate.id if duplicate else None,
                similar_to_document_id=similar.id if similar else None,
                similarity=similarity,
            ),
        )
    )
    session.flush()
    item.final_document_id = document.id
    item.final_snapshot_id = snapshot_id
    return document


def _set_candidate_result(
    item: IntakeItem,
    candidate_key: str,
    disposition: str,
    human_data: dict[str, Any],
    final_object_id: str | None,
) -> None:
    candidate = _candidate_map(item).get(candidate_key)
    if candidate is None:
        return
    candidate.disposition = disposition
    candidate.human_data = human_data
    candidate.reviewed_at = utcnow()
    candidate.final_object_id = final_object_id


def confirm_intake(
    session: Session,
    item: IntakeItem,
    request: IntakeConfirmationRequest,
    *,
    failure_hook: Callable[[], None] | None = None,
    locked_validation_hook: Callable[[Session, IntakeItem], None] | None = None,
) -> tuple[IntakeItem, dict[str, Any], bool]:
    """Atomically promote a reviewed submission. The bool says whether this call created objects."""
    fingerprint = _confirmation_fingerprint(request)
    if item.archived_at is not None:
        raise ArchivedIntakeError("confirming it")
    if item.status == "confirmed":
        if item.confirmation_fingerprint != fingerprint:
            raise ValueError("Item is already confirmed with a different review decision")
        return item, item.confirmation_result, False
    errors = validate_confirmation(session, item, request)
    if errors:
        raise ValueError("; ".join(errors))

    # Validation is read-only and may overlap an archive request.  Restart the
    # transaction, acquire the Intake fence before any formal-table write, then
    # reload and validate again under that fence.
    item_id = item.id
    session.rollback()
    item = lock_intake_for_mutation(session, item_id, action="confirming it")
    if item.status == "confirmed":
        if item.confirmation_fingerprint != fingerprint:
            raise ValueError("Item is already confirmed with a different review decision")
        if locked_validation_hook is not None:
            # A concurrent same-fingerprint confirmation may have committed
            # while this scoped request waited for the fence. Its topic link
            # still has to be revalidated before reporting scoped success.
            locked_validation_hook(session, item)
        session.commit()
        return item, item.confirmation_result, False
    errors = validate_confirmation(session, item, request)
    if errors:
        session.rollback()
        raise ValueError("; ".join(errors))
    if locked_validation_hook is not None:
        # Scoped membership checks and their approval audit must run after the
        # Intake fence and inside the exact formal-write transaction.
        locked_validation_hook(session, item)

    try:
        return _confirm_validated_intake(
            session,
            item,
            request,
            fingerprint=fingerprint,
            failure_hook=failure_hook,
        )
    except Exception:
        # Confirmation is one promotion transaction. In particular, advancing a
        # Document and appending its Snapshot must not leak through when any later
        # Event/Claim/Evidence operation fails.
        session.rollback()
        raise


def _confirm_validated_intake(
    session: Session,
    item: IntakeItem,
    request: IntakeConfirmationRequest,
    *,
    fingerprint: str,
    failure_hook: Callable[[], None] | None,
) -> tuple[IntakeItem, dict[str, Any], bool]:

    document = _create_formal_document(session, item)
    if request.merge_event_id:
        event = session.get(Event, request.merge_event_id)
        if event is None:
            raise ValueError("Selected merge event no longer exists")
    else:
        event_fields = _normalized_new_event_fields(item, request)
        event = Event(
            id="evt_intake_" + uuid.uuid4().hex[:16],
            title=event_fields["title"],
            summary=event_fields["summary"],
            event_type=event_fields["event_type"],
            start_at=event_fields["start_at"],
            end_at=None,
            latitude=None,
            longitude=None,
            location_name=event_fields["location_name"],
            importance=event_fields["importance"],
            status="confirmed",
            confidence=0.5,
            metadata_json={
                "intake_item_id": item.id,
                "start_at_known": event_fields["start_at_known"],
                "confirmation_stage": "P0.3-human-confirmed",
            },
        )
        session.add(event)
        session.flush()
    event.updated_at = utcnow()
    if not any(link.document_id == document.id for link in event.document_links):
        session.add(EventDocument(event_id=event.id, document_id=document.id, relevance=1.0))

    candidates = _candidate_map(item)
    entity_ids: dict[str, str] = {}
    for decision in request.entities:
        if decision.action == "exclude":
            _set_candidate_result(item, decision.candidate_key, "excluded", decision.model_dump(mode="json"), None)
            continue
        if decision.action == "merge":
            entity = session.get(Entity, decision.merge_entity_id)
            if entity is None:
                raise ValueError(f"Entity merge target missing: {decision.merge_entity_id}")
        else:
            entity = Entity(
                id="ent_intake_" + uuid.uuid4().hex[:16],
                name=decision.name.strip(),
                entity_type=decision.entity_type,
                aliases=decision.aliases,
            )
            session.add(entity)
            session.flush()
        if not any(link.entity_id == entity.id for link in event.entity_links):
            session.add(EventEntity(event_id=event.id, entity_id=entity.id, role=decision.role))
        entity_ids[decision.candidate_key] = entity.id
        _set_candidate_result(item, decision.candidate_key, decision.action, decision.model_dump(mode="json"), entity.id)

    claim_ids: dict[str, str] = {}
    for decision in request.claims:
        if decision.action == "exclude":
            _set_candidate_result(item, decision.candidate_key, "excluded", decision.model_dump(mode="json"), None)
            continue
        if decision.action == "merge":
            claim = session.get(Claim, decision.merge_claim_id)
            if claim is None:
                raise ValueError(f"Claim merge target missing: {decision.merge_claim_id}")
            if claim.event_id != event.id:
                raise ValueError("Claim merge target must belong to the selected final event")
        else:
            claim = Claim(
                id="clm_intake_" + uuid.uuid4().hex[:16],
                event_id=event.id,
                text=decision.text.strip(),
                status=decision.status,
                confidence=decision.confidence,
                origin="human-confirmed",
                temporal_scope=decision.temporal_scope,
            )
            session.add(claim)
            session.flush()
        claim_ids[decision.candidate_key] = claim.id
        _set_candidate_result(item, decision.candidate_key, decision.action, decision.model_dump(mode="json"), claim.id)

    relation_claim = {
        relation["from"]: relation["to"]
        for relation in item.candidate_relations
        if relation["type"] == "claim_evidence"
    }
    evidence_ids: dict[str, str] = {}
    for decision in request.evidence:
        if decision.action == "exclude":
            _set_candidate_result(item, decision.candidate_key, "excluded", decision.model_dump(mode="json"), None)
            continue
        start = item.extracted_snapshot.find(decision.snippet)
        end = start + len(decision.snippet)
        if start < 0 or item.extracted_snapshot[start:end] != decision.snippet:
            raise ValueError(f"Evidence {decision.candidate_key} failed complete-snapshot validation")
        claim_id = claim_ids.get(relation_claim.get(decision.candidate_key, ""))
        if claim_id is None:
            raise ValueError(f"Evidence {decision.candidate_key} has no selected parent claim")
        evidence_id = "evd_intake_" + uuid.uuid4().hex[:16]
        session.add(
            Evidence(
                id=evidence_id,
                claim_id=claim_id,
                document_id=document.id,
                snapshot_id=item.final_snapshot_id,
                snippet=decision.snippet,
                start_offset=start,
                end_offset=end,
                stance=decision.stance,
                strength=decision.strength,
                note=decision.note or DEFAULT_EVIDENCE_NOTE,
            )
        )
        evidence_ids[decision.candidate_key] = evidence_id
        _set_candidate_result(item, decision.candidate_key, "included", decision.model_dump(mode="json"), evidence_id)

    _set_candidate_result(
        item,
        "event",
        request.disposition,
        request.event.model_dump(mode="json"),
        event.id,
    )
    if failure_hook is not None:
        failure_hook()

    machine_event = _machine_event(item)
    differences = {
        "event": _difference(machine_event, request.event.model_dump(mode="json")),
    }
    trace = {
        "intake_item_id": item.id,
        "input_type": item.input_type,
        "external_search": item.review.get("external_search"),
        "machine_candidate_source": item.candidate_mode,
        "human_disposition": request.disposition,
        "analyst": request.analyst,
    }
    collection = item.review.get("collection")
    if collection is not None:
        trace["collection"] = collection
    result = {
        "status": "confirmed",
        "disposition": request.disposition,
        "analyst": request.analyst,
        "confirmed_at": iso(utcnow()),
        "fingerprint": fingerprint,
        "formal_object_ids": {
            "source": document.source_id,
            "document": document.id,
            "snapshot": item.final_snapshot_id,
            "event": event.id,
            "entities": sorted(entity_ids.values()),
            "claims": sorted(claim_ids.values()),
            "evidence": sorted(evidence_ids.values()),
        },
        "human_changes": differences,
        "trace": trace,
        "final_event_id": event.id,
        "event_url": f"/pldr-api/v1/events/{event.id}",
        "next_task": None,
    }
    item.status = "confirmed"
    item.error = None
    item.disposition = request.disposition
    item.reviewed_by = request.analyst
    item.reviewed_at = utcnow()
    item.updated_at = item.reviewed_at
    item.confirmation_fingerprint = fingerprint
    item.confirmation_result = result
    item.final_event_id = event.id
    review = dict(item.review or {})
    review["confirmation"] = {
        "request": request.model_dump(mode="json"),
        "machine_event": machine_event,
        "differences": differences,
    }
    item.review = review
    # Topic membership and review-task state are promoted in this same
    # transaction as the formal event, so the audit trail cannot lag behind a
    # successful confirmation.
    from .investigations import record_intake_disposition

    record_intake_disposition(
        session,
        item,
        status_value="confirmed",
        actor=request.analyst,
        event_id=event.id,
    )
    session.flush()
    session.refresh(item)
    session.commit()
    return item, result, True


def reject_intake(session: Session, item: IntakeItem, analyst: str, reason: str) -> IntakeItem:
    item_id = item.id
    session.rollback()
    item = lock_intake_for_mutation(session, item_id, action="rejecting it")
    if item.status == "confirmed":
        raise ValueError("A confirmed item cannot be rejected; preserve its history")
    now = utcnow()
    item.status = "rejected"
    item.disposition = "reject"
    item.reviewed_by = analyst
    item.reviewed_at = now
    item.updated_at = now
    item.rejection_reason = reason
    item.review["rejection"] = {"analyst": analyst, "reason": reason, "reviewed_at": iso(now)}
    for candidate in item.candidates:
        candidate.disposition = "rejected"
        candidate.reviewed_at = now
    from .investigations import record_intake_disposition

    record_intake_disposition(
        session,
        item,
        status_value="rejected",
        actor=analyst,
        reason=reason,
    )
    session.commit()
    session.refresh(item)
    return item


def cancel_intake(session: Session, item: IntakeItem, analyst: str, reason: str) -> IntakeItem:
    item_id = item.id
    session.rollback()
    item = lock_intake_for_mutation(session, item_id, action="cancelling it")
    if item.status == "confirmed":
        raise ValueError("A confirmed item cannot be cancelled; preserve its history")
    now = utcnow()
    item.status = "cancelled"
    item.disposition = "cancel"
    item.reviewed_by = analyst
    item.reviewed_at = now
    item.updated_at = now
    item.error = reason
    item.review["cancellation"] = {"analyst": analyst, "reason": reason, "reviewed_at": iso(now)}
    for candidate in item.candidates:
        candidate.disposition = "cancelled"
        candidate.reviewed_at = now
    from .investigations import record_intake_disposition

    record_intake_disposition(
        session,
        item,
        status_value="cancelled",
        actor=analyst,
        reason=reason,
    )
    session.commit()
    session.refresh(item)
    return item
