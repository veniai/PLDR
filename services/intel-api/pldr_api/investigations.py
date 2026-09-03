from __future__ import annotations

import hashlib
import json
import os
import socket
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from .database import SessionLocal, get_session
from .errors import (
    ArchivedIntakeError,
    IntakeMutationConflictError,
    IntakeScopeError,
    ModelGenerationError,
    UnlinkedReviewTaskError,
)
from .extraction import assess_extraction, canonicalize_url, content_hash, extract_page
from .importers import fetch_public_text_response
from .intake import (
    extracted_material_metadata,
    generate_candidates,
    lock_intake_for_mutation,
    lock_intake_for_status_sync,
    parse_datetime,
)
from .models import (
    Assessment,
    Claim,
    CollectionRun,
    CollectionTarget,
    DecisionLog,
    Entity,
    Evidence,
    Event,
    EventDocument,
    EventEntity,
    IntakeItem,
    Investigation,
    InvestigationLink,
    ProcessingBatch,
    ProcessingBatchEntry,
    ReviewTask,
    SearchQueryRun,
    SearchResult,
    SearchSelection,
    SearchSelectionEvent,
)
from .repository import event_query, serialize_event_card, serialize_event_detail
from .reporting import compose_current_answer
from .schemas import (
    ArchiveRequest,
    IntakeConfirmationRequest,
    InvestigationCreate,
    InvestigationLinkRequest,
    InvestigationReorganizationConfirmRequest,
    InvestigationUpdate,
    ReviewTaskRetryRequest,
)


router = APIRouter(prefix="/pldr-api/v1/investigations", tags=["investigations"])
task_router = APIRouter(prefix="/pldr-api/v1/tasks", tags=["investigation-tasks"])

DEMO_INVESTIGATION_ID = "inv_demo_suez_2021"
UNCLASSIFIED_INVESTIGATION_ID = "inv_unclassified"
ACTIVE_TASK_STATUSES = {"queued", "fetching", "generating"}
TERMINAL_TASK_STATUSES = {"ready", "failed", "confirmed", "rejected"}
DEFAULT_TASK_LEASE_SECONDS = 180
DEFAULT_MODEL_AUTO_RETRY_ATTEMPTS = 3
DEFAULT_MODEL_AUTO_RETRY_BASE_SECONDS = 60
DEFAULT_MODEL_AUTO_RETRY_MAX_SECONDS = 900
SOURCE_EVENT_LINK_ROLE = "source_event"


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def model_auto_retry_attempts() -> int:
    """Number of automatic model retries after the initial attempt."""
    return _env_int(
        "PLDR_MODEL_AUTO_RETRY_ATTEMPTS",
        DEFAULT_MODEL_AUTO_RETRY_ATTEMPTS,
        minimum=0,
        maximum=20,
    )


def model_auto_retry_delay_seconds(retry_number: int) -> int:
    base = _env_int(
        "PLDR_MODEL_AUTO_RETRY_BASE_SECONDS",
        DEFAULT_MODEL_AUTO_RETRY_BASE_SECONDS,
        minimum=0,
        maximum=86400,
    )
    maximum = _env_int(
        "PLDR_MODEL_AUTO_RETRY_MAX_SECONDS",
        DEFAULT_MODEL_AUTO_RETRY_MAX_SECONDS,
        minimum=0,
        maximum=86400,
    )
    return min(maximum, base * (2 ** max(0, retry_number - 1)))


def _model_api_configured() -> bool:
    return all(
        os.getenv(name, "").strip()
        for name in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL_NAME")
    )


def _task_has_current_intake_link():
    return (
        select(InvestigationLink.id)
        .where(
            InvestigationLink.investigation_id == ReviewTask.investigation_id,
            InvestigationLink.object_type == "intake",
            InvestigationLink.object_id == ReviewTask.intake_item_id,
        )
        .exists()
    )


def _task_intake_is_visible():
    return (
        select(IntakeItem.id)
        .where(
            IntakeItem.id == ReviewTask.intake_item_id,
            IntakeItem.archived_at.is_(None),
        )
        .exists()
    )


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _require_unarchived_intake(
    session: Session, item: IntakeItem, *, action: str
) -> None:
    lock_intake_for_mutation(session, item.id, action=action)


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def new_investigation_id() -> str:
    return f"inv_{uuid.uuid4().hex[:20]}"


def new_link_id() -> str:
    return f"invl_{uuid.uuid4().hex[:22]}"


def new_task_id() -> str:
    return f"rvt_{uuid.uuid4().hex[:22]}"


def new_batch_id() -> str:
    return f"rvb_{uuid.uuid4().hex[:22]}"


def new_log_id() -> str:
    return f"act_{uuid.uuid4().hex[:22]}"


def new_batch_entry_id() -> str:
    return f"rvbe_{uuid.uuid4().hex[:21]}"


def review_worker_identity() -> str:
    return f"{socket.gethostname()}:review:{uuid.uuid4().hex[:10]}"


def model_task_lease_seconds() -> float:
    """Cover every active worker's worst-case turn through the model limiter."""
    model_timeout = max(1.0, float(os.getenv("LLM_TIMEOUT_SECONDS", "60")))
    worker_slots = max(1, int(os.getenv("PLDR_COLLECTOR_CONCURRENCY", "4")))
    model_slots = max(1, int(os.getenv("LLM_MAX_CONCURRENCY", "1")))
    model_waves = (worker_slots + model_slots - 1) // model_slots
    return model_timeout * model_waves + 60


def record_action(
    session: Session,
    investigation_id: str,
    action: str,
    *,
    actor: str = "system",
    object_type: str | None = None,
    object_id: str | None = None,
    task_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> DecisionLog:
    entry = DecisionLog(
        id=new_log_id(),
        investigation_id=investigation_id,
        action=action,
        actor=actor,
        object_type=object_type,
        object_id=object_id,
        task_id=task_id,
        detail_json=detail or {},
        created_at=utcnow(),
    )
    session.add(entry)
    return entry


def _question_from_request(request: InvestigationCreate | InvestigationUpdate) -> str | None:
    question = getattr(request, "question", None)
    objective = getattr(request, "objective", None)
    if question is not None and question.strip():
        return question.strip()
    if objective is not None:
        return objective.strip()
    return question


def create_investigation_record(
    session: Session,
    request: InvestigationCreate,
    *,
    investigation_id: str | None = None,
    actor: str | None = None,
) -> Investigation:
    now = utcnow()
    investigation = Investigation(
        id=investigation_id or new_investigation_id(),
        title=request.title.strip(),
        question=_question_from_request(request) or "",
        description=request.description.strip(),
        tracking_mode=request.tracking_mode,
        event_start_at=request.event_start_at,
        event_end_at=request.event_end_at,
        settings_json=request.settings.model_dump(),
        status=request.status,
        created_at=now,
        updated_at=now,
    )
    session.add(investigation)
    session.flush()
    record_action(
        session,
        investigation.id,
        "investigation.created",
        actor=actor or request.actor,
        object_type="investigation",
        object_id=investigation.id,
        detail={
            "title": investigation.title,
            "question": investigation.question,
            "tracking_mode": investigation.tracking_mode,
            "event_start_at": iso(investigation.event_start_at),
            "event_end_at": iso(investigation.event_end_at),
            "status": investigation.status,
        },
    )
    return investigation


def _ensure_system_investigation(
    session: Session,
    investigation_id: str,
    *,
    title: str,
    question: str,
    description: str,
) -> Investigation:
    existing = session.get(Investigation, investigation_id)
    if existing is not None:
        return existing
    request = InvestigationCreate(
        title=title,
        question=question,
        description=description,
        status="active",
        actor="system:migration",
    )
    return create_investigation_record(
        session,
        request,
        investigation_id=investigation_id,
        actor="system:migration",
    )


def resolve_investigation_context(
    session: Session,
    *,
    investigation_id: str | None,
    new_investigation: InvestigationCreate | None,
    actor: str = "analyst",
    default_unclassified: bool = False,
) -> tuple[Investigation | None, bool]:
    if investigation_id and new_investigation:
        raise ValueError("Use investigation_id or new_investigation, not both")
    if investigation_id:
        investigation = session.get(Investigation, investigation_id)
        if investigation is None:
            raise ValueError("Investigation not found")
        return investigation, False
    if new_investigation is not None:
        return create_investigation_record(
            session, new_investigation, actor=actor or new_investigation.actor
        ), True
    if default_unclassified:
        return (
            _ensure_system_investigation(
                session,
                UNCLASSIFIED_INVESTIGATION_ID,
                title="未分类工作",
                question="尚未归入明确专题的既有或兼容 API 工作",
                description="系统安全迁移和旧 API 兼容使用；可继续关联到一个或多个正式专题。",
            ),
            False,
        )
    return None, False


def link_object(
    session: Session,
    investigation_id: str,
    object_type: str,
    object_id: str,
    *,
    role: str = "member",
    actor: str = "system",
    metadata: dict[str, Any] | None = None,
    action: str = "object.linked",
) -> tuple[InvestigationLink, bool]:
    if object_type == "intake":
        item = session.get(IntakeItem, object_id)
        if item is not None:
            _require_unarchived_intake(
                session, item, action="linking it to an investigation"
            )
    existing = session.scalar(
        select(InvestigationLink).where(
            InvestigationLink.investigation_id == investigation_id,
            InvestigationLink.object_type == object_type,
            InvestigationLink.object_id == object_id,
        )
    )
    if existing is not None:
        return existing, False
    link = InvestigationLink(
        id=new_link_id(),
        investigation_id=investigation_id,
        object_type=object_type,
        object_id=object_id,
        role=role,
        metadata_json=metadata or {},
        created_at=utcnow(),
    )
    session.add(link)
    investigation = session.get(Investigation, investigation_id)
    if investigation is not None:
        investigation.updated_at = utcnow()
    session.flush()
    record_action(
        session,
        investigation_id,
        action,
        actor=actor,
        object_type=object_type,
        object_id=object_id,
        detail={"role": role, **(metadata or {})},
    )
    return link, True


def _has_any_link(session: Session, object_type: str, object_id: str) -> bool:
    return (
        session.scalar(
            select(InvestigationLink.id)
            .where(
                InvestigationLink.object_type == object_type,
                InvestigationLink.object_id == object_id,
            )
            .limit(1)
        )
        is not None
    )


def _has_any_intake_removal_history(session: Session, item_id: str) -> bool:
    return (
        session.scalar(
            select(DecisionLog.id)
            .where(
                DecisionLog.action == "intake.removed_from_investigation",
                DecisionLog.object_type == "intake",
                DecisionLog.object_id == item_id,
            )
            .limit(1)
        )
        is not None
    )


def bootstrap_legacy_investigations(session: Session) -> dict[str, int]:
    """Idempotently classify old rows without deleting or rewriting source objects."""
    demo = _ensure_system_investigation(
        session,
        DEMO_INVESTIGATION_ID,
        title="苏伊士运河阻塞事件链",
        question="公开证据如何支持 2021 年苏伊士运河阻塞事件链？",
        description="PLDR 内置、人工整理的演示专题。",
    )
    unclassified = _ensure_system_investigation(
        session,
        UNCLASSIFIED_INVESTIGATION_ID,
        title="未分类工作",
        question="尚未归入明确专题的既有或兼容 API 工作",
        description="迁移时保留的 intake、搜索查询、来源监测和非演示事件。",
    )
    counts = {"demo_events": 0, "unclassified": 0}

    events = list(
        session.scalars(
            select(Event).options(
                selectinload(Event.document_links).selectinload(EventDocument.document)
            )
        ).unique()
    )
    for event in events:
        is_demo = any(
            bool((link.document.metadata_json or {}).get("demo"))
            for link in event.document_links
        )
        target = demo if is_demo else unclassified
        if is_demo or not _has_any_link(session, "event", event.id):
            _, created = link_object(
                session,
                target.id,
                "event",
                event.id,
                actor="system:migration",
                metadata={"classification": "demo-document" if is_demo else "legacy"},
                action="migration.classified",
            )
            if created:
                counts["demo_events" if is_demo else "unclassified"] += 1

    for model, object_type in (
        (SearchQueryRun, "search_query"),
        (IntakeItem, "intake"),
        (CollectionTarget, "collection_target"),
    ):
        for object_id in session.scalars(select(model.id)):
            if _has_any_link(session, object_type, object_id):
                continue
            if object_type == "intake":
                item = session.get(IntakeItem, object_id)
                if item is not None and item.archived_at is not None:
                    # Global archive is an explicit inbox decision, not a
                    # legacy row awaiting automatic classification.
                    continue
                if _has_any_intake_removal_history(session, object_id):
                    # A user may intentionally remove an item from its last
                    # topic. Do not silently undo that decision by moving it
                    # into the unclassified inbox on restart.
                    continue
            _, created = link_object(
                session,
                unclassified.id,
                object_type,
                object_id,
                actor="system:migration",
                metadata={"classification": "legacy"},
                action="migration.classified",
            )
            if created:
                counts["unclassified"] += 1

    # Preserve the pre-upgrade analyst inbox.  Parsed rows remain actionable;
    # terminal rows remain visible and auditable.  This is intentionally
    # idempotent and does not rewrite or delete the legacy intake object.
    task_statuses = {
        "parsed": "queued",
        "candidate_ready": "ready",
        "failed": "failed",
        "generation_failed": "failed",
        "confirmed": "confirmed",
        "rejected": "rejected",
        "cancelled": "rejected",
    }
    for item in session.scalars(select(IntakeItem)):
        if item.archived_at is not None:
            continue
        if session.scalar(
            select(InvestigationLink.id).where(
                InvestigationLink.investigation_id == unclassified.id,
                InvestigationLink.object_type == "intake",
                InvestigationLink.object_id == item.id,
            )
        ) is None:
            continue
        status_value = task_statuses.get(item.status)
        if status_value is None:
            continue
        if session.scalar(
            select(ReviewTask.id).where(
                ReviewTask.investigation_id == unclassified.id,
                ReviewTask.intake_item_id == item.id,
            )
        ):
            continue
        selection = session.scalar(
            select(SearchSelection).where(SearchSelection.intake_item_id == item.id)
        )
        result = session.get(SearchResult, selection.result_id) if selection else None
        now = utcnow()
        subject_type = "search_result" if result is not None else "intake"
        subject_id = result.id if result is not None else item.id
        fingerprint = (
            result.result_fingerprint if result is not None else f"legacy-intake:{item.id}"
        )
        payload: dict[str, Any] = {
            "legacy_migration": True,
            "result_fingerprint": fingerprint,
        }
        if result is not None:
            payload.update(
                {
                    "result_id": result.id,
                    "query_run_id": result.query_run_id,
                    "requested_url": result.original_url,
                }
            )
        fallback_class = _candidate_fallback_class(item)
        task = ReviewTask(
            id=new_task_id(),
            investigation_id=unclassified.id,
            batch_id=None,
            task_type=(
                "search_result_intake" if result is not None else "intake_candidate_generation"
            ),
            subject_type=subject_type,
            subject_id=subject_id,
            active_key=(
                _active_task_key(unclassified.id, fingerprint)
                if status_value == "queued"
                else None
            ),
            status=status_value,
            attempt_number=1,
            queued_at=now,
            completed_at=now if status_value in TERMINAL_TASK_STATUSES else None,
            error_class=(
                fallback_class
                if fallback_class
                else "legacy_intake_failed"
                if status_value == "failed"
                else None
            ),
            error_message=(
                item.candidate_error
                if fallback_class == "model_fallback"
                else (item.error or item.candidate_error)
                if status_value == "failed"
                else None
            ),
            intake_item_id=item.id,
            selection_id=selection.id if selection is not None else None,
            payload_json=payload,
            created_at=now,
            updated_at=now,
        )
        session.add(task)
        session.flush()
        if item.final_event_id and session.get(Event, item.final_event_id) is not None:
            link_object(
                session,
                unclassified.id,
                "event",
                item.final_event_id,
                actor="system:migration",
                metadata={"intake_item_id": item.id, "classification": "legacy-confirmed"},
                action="migration.classified",
            )
        record_action(
            session,
            unclassified.id,
            "migration.task_backfilled",
            actor="system:migration",
            object_type="intake",
            object_id=item.id,
            task_id=task.id,
            detail={"intake_status": item.status, "task_status": status_value},
        )
        counts["unclassified"] += 1
    session.commit()
    return counts


def attach_search_run(
    session: Session,
    request: Any,
    run: SearchQueryRun,
) -> Investigation:
    """Attach every query; context-free legacy calls go to unclassified."""
    investigation, _ = resolve_investigation_context(
        session,
        investigation_id=getattr(request, "investigation_id", None),
        new_investigation=getattr(request, "new_investigation", None),
        actor=getattr(request, "actor", "analyst"),
        default_unclassified=True,
    )
    assert investigation is not None
    link_object(
        session,
        investigation.id,
        "search_query",
        run.id,
        actor=getattr(request, "actor", "analyst"),
        metadata={"keyword": run.keyword, "scope": run.scope},
        action="search.query_linked",
    )
    return investigation


def serialize_investigation(
    session: Session,
    investigation: Investigation,
    *,
    include_detail: bool = False,
) -> dict[str, Any]:
    links = list(
        session.scalars(
            select(InvestigationLink)
            .where(InvestigationLink.investigation_id == investigation.id)
            .order_by(InvestigationLink.created_at.asc(), InvestigationLink.id.asc())
        )
    )
    visible_links: list[InvestigationLink] = []
    for link in links:
        if link.object_type == "intake":
            item = session.get(IntakeItem, link.object_id)
            if item is None or item.archived_at is not None:
                continue
        elif link.object_type == "search_query":
            run = session.get(SearchQueryRun, link.object_id)
            if run is None or run.archived_at is not None:
                continue
        elif link.object_type == "event" and link.role == SOURCE_EVENT_LINK_ROLE:
            continue
        visible_links.append(link)
    counts: dict[str, int] = {
        "search_queries": 0,
        "intake_items": 0,
        "collection_targets": 0,
        "events": 0,
    }
    count_key = {
        "search_query": "search_queries",
        "intake": "intake_items",
        "collection_target": "collection_targets",
        "event": "events",
    }
    for link in visible_links:
        key = count_key.get(link.object_type)
        if key:
            counts[key] += 1
    task_counts = {
        value: int(
            session.scalar(
                select(func.count())
                .select_from(ReviewTask)
                .where(
                    ReviewTask.investigation_id == investigation.id,
                    ReviewTask.status == value,
                    _task_has_current_intake_link(),
                    _task_intake_is_visible(),
                )
            )
            or 0
        )
        for value in [
            "queued",
            "fetching",
            "generating",
            "ready",
            "failed",
            "confirmed",
            "rejected",
        ]
    }
    payload: dict[str, Any] = {
        "id": investigation.id,
        "kind": (
            "demo"
            if investigation.id == DEMO_INVESTIGATION_ID
            else "system"
            if investigation.id == UNCLASSIFIED_INVESTIGATION_ID
            else "user"
        ),
        "title": investigation.title,
        "question": investigation.question,
        "objective": investigation.question,
        "description": investigation.description,
        "tracking_mode": investigation.tracking_mode or "one_time",
        "event_start_at": iso(investigation.event_start_at),
        "event_end_at": iso(investigation.event_end_at),
        "settings": dict(investigation.settings_json or {}),
        "status": investigation.status,
        "created_at": iso(investigation.created_at),
        "updated_at": iso(investigation.updated_at),
        "counts": {**counts, "tasks": sum(task_counts.values())},
        "task_status": task_counts,
    }
    if not include_detail:
        return payload

    grouped: dict[str, list[dict[str, Any]]] = {
        "search_queries": [],
        "intake_items": [],
        "collection_targets": [],
        "events": [],
    }
    for link in visible_links:
        if link.object_type == "search_query":
            run = session.get(SearchQueryRun, link.object_id)
            if run is not None:
                grouped["search_queries"].append(
                    {
                        "id": run.id,
                        "keyword": run.keyword,
                        "scope": run.scope,
                        "status": run.status,
                        "result_count": run.result_count,
                        "created_at": iso(run.created_at),
                    }
                )
        elif link.object_type == "intake":
            item = session.get(IntakeItem, link.object_id)
            if item is not None:
                grouped["intake_items"].append(
                    {
                        "id": item.id,
                        "input_type": item.input_type,
                        "status": item.status,
                        "title": item.title,
                        "source_url": item.source_url,
                        "error": item.error or item.candidate_error,
                        "created_at": iso(item.created_at),
                    }
                )
        elif link.object_type == "collection_target":
            target = session.get(CollectionTarget, link.object_id)
            if target is not None:
                grouped["collection_targets"].append(
                    {
                        "id": target.id,
                        "name": target.name,
                        "url": target.url,
                        "health": target.health,
                        "enabled": target.enabled,
                        "last_success_at": iso(target.last_success_at),
                    }
                )
        elif link.object_type == "event":
            event = session.get(Event, link.object_id)
            if event is not None:
                # Keep topic detail aligned with the canonical event contract.
                # Confirming an event with no known time stores a sentinel date
                # for persistence while metadata_json.start_at_known=false is
                # the public signal that API clients must receive as null.
                grouped["events"].append(serialize_event_card(event))
    payload.update(grouped)
    payload["links"] = [serialize_link(link) for link in visible_links]
    report_entries = list(
        session.scalars(
            select(DecisionLog)
            .where(
                DecisionLog.investigation_id == investigation.id,
                DecisionLog.action == "report.generated",
            )
            .order_by(DecisionLog.created_at.desc(), DecisionLog.id.desc())
        )
    )
    payload["reports"] = [
        {
            "id": entry.id,
            "filename": entry.object_id,
            "title": (entry.detail_json or {}).get("title"),
            "url": (entry.detail_json or {}).get("url"),
            "generated_at": (entry.detail_json or {}).get("generated_at")
            or iso(entry.created_at),
            "event_count": (entry.detail_json or {}).get("event_count"),
            "evidence_count": (entry.detail_json or {}).get("evidence_count"),
            "event_ids": (entry.detail_json or {}).get("event_ids", []),
        }
        for entry in report_entries
    ]
    return payload


def serialize_investigation_outcome(
    session: Session,
    investigation: Investigation,
) -> dict[str, Any]:
    """Build the user-facing result from confirmed formal objects only.

    Review tasks are exposed only as counts so that candidates and failures can
    guide the analyst back to the work queue without leaking draft content into
    the topic result.
    """
    detail = serialize_investigation(session, investigation, include_detail=True)
    linked_event_ids = [
        event["id"] for event in detail.get("events", []) if event.get("id")
    ]
    event_models = []
    if linked_event_ids:
        event_models = list(
            session.scalars(
                event_query().where(
                    Event.id.in_(linked_event_ids),
                    Event.status == "confirmed",
                )
            ).unique()
        )
    by_id = {event.id: event for event in event_models}
    ordered_models = [
        by_id[event_id] for event_id in linked_event_ids if event_id in by_id
    ]
    ordered_models.sort(
        key=lambda event: (
            (event.metadata_json or {}).get("start_at_known") is not False,
            event.start_at,
            event.updated_at,
            event.id,
        ),
        reverse=True,
    )
    events = [serialize_event_detail(event) for event in ordered_models]

    claims: list[dict[str, Any]] = []
    entity_index: dict[str, dict[str, Any]] = {}
    source_index: dict[str, dict[str, Any]] = {}
    information_gaps: list[str] = []
    missing_evidence_claim_ids: list[str] = []
    for event in events:
        for entity in event.get("entities", []):
            entity_index.setdefault(entity["id"], entity)
        for document in event.get("documents", []):
            source = document.get("source") or {}
            source_id = source.get("id")
            if source_id:
                source_index.setdefault(source_id, source)
        assessment = event.get("assessment") or {}
        for gap in assessment.get("information_gaps") or []:
            cleaned = str(gap).strip()
            if cleaned and cleaned not in information_gaps:
                information_gaps.append(cleaned)
        for claim in event.get("claims", []):
            evidence_items = claim.get("evidence") or []
            source_verification = claim.get("source_verification") or {}
            display_status = source_verification.get("status") or claim.get("status")
            claims.append(
                {
                    **claim,
                    "raw_status": claim.get("status"),
                    "status": display_status,
                    "event_id": event["id"],
                    "event_title": event["title"],
                    "evidence_count": len(evidence_items),
                }
            )
            if not evidence_items:
                missing_evidence_claim_ids.append(claim["id"])

    reports = detail.get("reports", [])
    latest_report = reports[0] if reports else None
    latest_report_event_ids = set((latest_report or {}).get("event_ids") or [])
    baseline_at = (latest_report or {}).get("generated_at") or detail.get("created_at")
    baseline_time: datetime | None = None
    if baseline_at:
        try:
            baseline_time = datetime.fromisoformat(str(baseline_at).replace("Z", "+00:00"))
            if baseline_time.tzinfo is None:
                baseline_time = baseline_time.replace(tzinfo=timezone.utc)
        except ValueError:
            baseline_time = None
    new_event_ids = [
        event.id for event in ordered_models if event.id not in latest_report_event_ids
    ] if latest_report else [event.id for event in ordered_models]
    updated_event_ids = [
        event.id
        for event in ordered_models
        if latest_report
        and event.id in latest_report_event_ids
        and baseline_time is not None
        and (event.updated_at.replace(tzinfo=timezone.utc) if event.updated_at.tzinfo is None else event.updated_at)
        > baseline_time
    ]

    latest_reorganization = session.scalar(
        select(DecisionLog)
        .where(
            DecisionLog.investigation_id == investigation.id,
            DecisionLog.action == "reorganization.confirmed",
        )
        .order_by(DecisionLog.created_at.desc(), DecisionLog.id.desc())
        .limit(1)
    )
    reorganization_detail = (latest_reorganization.detail_json or {}) if latest_reorganization else {}
    reorganization_is_current = bool(latest_reorganization) and set(
        reorganization_detail.get("event_ids") or []
    ) == set(linked_event_ids)
    if reorganization_is_current:
        for gap in reorganization_detail.get("information_gaps") or []:
            cleaned = str(gap).strip()
            if cleaned and cleaned not in information_gaps:
                information_gaps.append(cleaned)

    latest_event = events[0] if events else None
    latest_assessment = (latest_event or {}).get("assessment") or {}
    if latest_event:
        answer_text = str(
            (
                reorganization_detail.get("current_answer")
                if reorganization_is_current
                else latest_assessment.get("judgement")
            )
            or ""
        ).strip()
        answer_text, answer_basis = compose_current_answer(
            claims,
            assessment=answer_text,
            fallback_summary=latest_event.get("summary"),
        )
        current_answer = {
            "status": "available",
            "headline": (
                f"已将 {len(reorganization_detail.get('source_event_ids') or [])} 份资料整理为 {len(events)} 个真实事件"
                if reorganization_is_current
                else f"已确认 {len(events)} 个事件，当前最新进展：{latest_event['title']}"
            ),
            "text": answer_text,
            "basis": "confirmed_topic_synthesis" if reorganization_is_current else answer_basis,
            "event_id": latest_event["id"],
            "notice": "仅汇总本专题已人工确认的正式对象；未确认候选不会进入成果。",
        }
    else:
        current_answer = {
            "status": "empty",
            "headline": "尚未形成正式成果",
            "text": "资料可以继续在后台处理；只有经过人工采用的内容才会出现在这里。",
            "basis": "no_confirmed_event",
            "event_id": None,
            "notice": "没有使用搜索摘要或 AI 草稿填充结论。",
        }

    unresolved_claims = [
        claim for claim in claims if claim.get("status") in {"contested", "unverified"}
    ]
    single_source_claims = [
        claim for claim in claims if claim.get("status") == "single_source"
    ]
    task_status = detail.get("task_status") or {}
    return {
        "investigation": {
            "id": investigation.id,
            "title": investigation.title,
            "question": investigation.question,
            "tracking_mode": investigation.tracking_mode or "one_time",
            "status": investigation.status,
        },
        "generated_at": iso(utcnow()),
        "current_answer": current_answer,
        "changes": {
            "basis": "latest_report" if latest_report else "topic_created",
            "label": "自上次报告以来" if latest_report else "当前累计",
            "since": baseline_at,
            "new_event_ids": new_event_ids,
            "new_event_count": len(new_event_ids),
            "updated_event_ids": updated_event_ids,
            "updated_event_count": len(updated_event_ids),
            "latest_report": latest_report,
        },
        "counts": {
            "events": len(events),
            "claims": len(claims),
            "evidence": sum(claim["evidence_count"] for claim in claims),
            "sources": len(source_index),
            "entities": len(entity_index),
            "unresolved_claims": len(unresolved_claims),
            "single_source_claims": len(single_source_claims),
            "multi_source_claims": sum(1 for claim in claims if claim.get("status") == "supported"),
            "claims_without_evidence": len(missing_evidence_claim_ids),
            "waiting_for_review": int(task_status.get("ready") or 0),
            "processing": sum(int(task_status.get(status_value) or 0) for status_value in ("queued", "fetching", "generating")),
            "failed": int(task_status.get("failed") or 0),
        },
        "events": events,
        "claims": claims,
        "entities": list(entity_index.values()),
        "sources": list(source_index.values()),
        "information_gaps": information_gaps,
        "missing_evidence_claim_ids": missing_evidence_claim_ids,
        "reports": reports,
    }


def serialize_link(link: InvestigationLink) -> dict[str, Any]:
    return {
        "id": link.id,
        "investigation_id": link.investigation_id,
        "object_type": link.object_type,
        "object_id": link.object_id,
        "role": link.role,
        "metadata": link.metadata_json or {},
        "created_at": iso(link.created_at),
    }


def serialize_batch(batch: ProcessingBatch) -> dict[str, Any]:
    return {
        "id": batch.id,
        "request_id": batch.request_id,
        "investigation_id": batch.investigation_id,
        "status": batch.status,
        "requested_count": batch.requested_count,
        "created_at": iso(batch.created_at),
        "updated_at": iso(batch.updated_at),
    }


def _structured_task_error(
    error_class: str | None,
    error_message: str | None,
    *,
    task_status: str,
) -> dict[str, Any] | None:
    """Give old and new task rows the same actionable, user-facing error."""
    if not error_class and not error_message:
        return None
    normalized = (error_message or "").lower()
    code = error_class or "internal"
    if error_class == "unsafe_url" and "non-public address" in normalized:
        code = "dns_policy_blocked"
    elif error_class == "http_status":
        code = next((f"http_{value}" for value in (401, 403, 429) if str(value) in normalized), code)
    elif error_class == "model_fallback":
        code = "model_timeout_fallback" if "timeout" in normalized or "deadline" in normalized else "model_error_fallback"
    elif error_class == "extraction":
        code = "empty_or_short_body"
    elif error_class == "timeout":
        code = "fetch_timeout"
    elif error_class in {"legacy_intake_failed", "intake_failed"} and (
        "model" in normalized or "generation" in normalized
    ):
        code = "legacy_model_error"

    # stage, title, display_message, why, impact, next_action, retryable, degraded
    specs = {
        "dns_policy_blocked": ("fetch", "地址安全校验未通过", "网址解析结果包含内网或非公网地址，系统已停止抓取。", "这是 SSRF 安全保护；代理或 DNS 映射也可能产生这类结果。", "尚未取得正文，也未进入正式档案。", "请先检查代理/DNS，或换用公开正文页；当前配置不变时重复重试不会解决问题。", False, False),
        "unsafe_url": ("fetch", "网址不符合安全策略", "系统为保护内部网络停止了抓取。", "网址或重定向目标不是可验证的公网 HTTP 地址。", "尚未取得正文，也未进入正式档案。", "改用可信的公开 HTTP(S) 网址。", False, False),
        "http_401": ("fetch", "来源需要登录", "来源要求身份验证，PLDR 没有取得正文。", "公开请求收到 HTTP 401。", "尚未生成候选。", "改用公开原文，或粘贴/上传已获授权的内容。", False, False),
        "http_403": ("fetch", "来源拒绝访问", "来源拒绝了自动抓取请求。", "公开请求收到 HTTP 403，常见于登录墙或反自动化策略。", "尚未生成候选。", "改用公开转载页，或粘贴/上传原文。", False, False),
        "http_429": ("fetch", "来源请求过于频繁", "来源暂时限制了抓取频率。", "公开请求收到 HTTP 429。", "这条资料仍在待处理箱。", "等待一段时间后只重试这条资料。", True, False),
        "http_status": ("fetch", "来源返回异常状态", "来源没有返回可处理的正文。", "网页请求返回失败的 HTTP 状态。", "尚未生成候选。", "稍后重试或更换公开来源。", task_status == "failed", False),
        "redirect_limit": ("fetch", "网页跳转次数过多", "来源在多个网址之间反复跳转，系统停止了抓取。", "可能是来源跳转循环、地区入口或代理网络导致。", "尚未生成候选。", "稍后重试；持续失败时更换最终正文网址。", True, False),
        "response_too_large": ("fetch", "页面超过安全上限", "页面体积过大，系统停止了抓取。", "采集器限制单条资料大小以保护批量任务。", "尚未生成候选。", "改用正文页、精简文件或粘贴相关内容。", False, False),
        "unsupported_content_type": ("fetch", "暂不支持这种内容", "链接返回的格式不能作为文本资料处理。", "当前采集器只接受受支持的文本内容。", "尚未生成候选。", "下载后上传受支持文件，或寻找正文网页。", False, False),
        "unsupported_content_encoding": ("fetch", "暂不支持这种压缩格式", "链接返回的压缩方式不能安全处理。", "采集器拒绝无法受控解压的响应。", "尚未生成候选。", "下载后上传受支持文件，或寻找正文网页。", False, False),
        "empty_or_short_body": ("extract", "没有提取到有效正文", "页面可访问，但正文为空或过短。", "页面可能依赖脚本、只有导航或登录提示。", "原网页未形成可审核候选。", "改用正文页，或直接粘贴/上传原文。", False, False),
        "fetch_timeout": ("fetch", "抓取网页超时", "来源未在限定时间内返回完整正文。", "来源响应过慢或网络暂时不稳定。", "这条资料仍在待处理箱。", "稍后只重试这条资料。", True, False),
        "network": ("fetch", "无法连接来源", "采集器没有建立稳定连接。", "可能是网络、DNS 或来源服务临时故障。", "尚未生成候选。", "稍后重试；持续失败时检查代理/DNS。", True, False),
        "model_timeout": ("generate", "AI 分析超时", "原文已经保存，但本次 AI 分析没有按时完成。", "模型响应超过本次时限；系统没有用规则内容冒充 AI 结果。", "尚未生成候选，也未进入正式档案。", "点击“重新分析”；系统只重试 AI，不会重复抓取网页。", True, False),
        "model_error": ("generate", "AI 分析失败", "原文已经保存，但模型没有返回可用的结构化结果。", "模型服务返回错误，或结果未通过格式与原文依据校验。", "尚未生成候选，也未进入正式档案。", "点击“重新分析”；系统只重试 AI，不会重复抓取网页。", True, False),
        "model_timeout_fallback": ("generate", "AI 超时，已生成规则候选", "原文已保存，系统生成了可人工审核的降级候选。", "外部模型超过本次请求时限。", "可以审核入档，但候选字段可能不完整。", "直接人工审核，或选择“只重试 AI”；不会重复抓取。", True, True),
        "model_error_fallback": ("generate", "AI 失败，已生成规则候选", "原文已保存，系统生成了可人工审核的降级候选。", "模型返回错误或结果不符合候选结构。", "可以审核入档，但候选字段可能不完整。", "直接人工审核，或选择“只重试 AI”；不会重复抓取。", True, True),
        "rule_fallback": ("generate", "已使用规则生成候选", "原文已保存，并由确定性规则生成候选。", "当前没有调用可用的外部 AI 模型。", "可以继续审核，但候选字段通常更少。", "继续人工审核；需要时配置模型后重新生成。", False, True),
        "legacy_model_error": ("generate", "旧候选生成失败", "旧资料没有形成候选，但原文可能已保存。", "旧版模型生成流程曾返回错误。", "尚未进入正式档案。", "使用现有重试入口；失败后会生成可审核降级候选。", True, False),
    }
    stage, title, display, why, impact, next_action, retryable, degraded = specs.get(
        code,
        ("fetch", "资料处理失败", "系统未能完成这条资料的处理。", "底层处理返回未分类错误。", "尚未进入正式档案。", "稍后重试；持续失败时查看技术详情。", task_status == "failed", False),
    )
    return {
        "class": error_class,
        "message": error_message,
        "code": code,
        "stage": stage,
        "title": title,
        "display_message": display,
        "why": why,
        "impact": impact,
        "retryable": retryable,
        "next_action": next_action,
        "degraded": degraded,
        "technical_detail": error_message,
    }


def serialize_task(
    task: ReviewTask,
    *,
    session: Session | None = None,
    include_intake_detail: bool = False,
) -> dict[str, Any]:
    structured_error = _structured_task_error(
        task.error_class, task.error_message, task_status=task.status
    )
    if structured_error is not None:
        upstream_status = next(
            (
                value
                for value in (401, 403, 429)
                if task.error_class == f"http_{value}"
                or str(value) in (task.error_message or "")
            ),
            None,
        )
        structured_error.update(
            {
                "trace_id": task.id,
                "upstream_status": upstream_status,
                "retry_after": None,
                "diagnostic": {
                    "trace_id": task.id,
                    "task_id": task.id,
                    "batch_id": task.batch_id,
                    "investigation_id": task.investigation_id,
                    "subject_type": task.subject_type,
                    "subject_id": task.subject_id,
                },
            }
        )
    fallback_used = task.error_class in {"model_fallback", "rule_fallback"}
    task_payload = task.payload_json or {}
    model_retry = task_payload.get("model_retry")
    waiting_for_model_retry = bool(
        task.status == "queued"
        and isinstance(model_retry, dict)
        and model_retry.get("status") == "waiting"
    )
    payload = {
        "id": task.id,
        "task_id": task.id,
        "investigation_id": task.investigation_id,
        "batch_id": task.batch_id,
        "task_type": task.task_type,
        "subject_type": task.subject_type,
        "subject_id": task.subject_id,
        "subject": {"type": task.subject_type, "id": task.subject_id},
        "status": task.status,
        "attempt_number": task.attempt_number,
        "queued_at": iso(task.queued_at),
        "started_at": iso(task.started_at),
        "completed_at": iso(task.completed_at),
        "lease": {
            "owner": task.lease_owner,
            "expires_at": iso(task.lease_expires_at),
            "recoveries": task.lease_recoveries,
        },
        "lease_owner": task.lease_owner,
        "lease_expires_at": iso(task.lease_expires_at),
        "lease_recoveries": task.lease_recoveries,
        "error_class": task.error_class,
        "error_message": task.error_message,
        "last_error": task.error_message,
        "error": structured_error,
        "fallback_used": fallback_used,
        "degraded": bool(structured_error and structured_error["degraded"]),
        "retryable": (
            False
            if waiting_for_model_retry
            else bool(structured_error["retryable"])
            if structured_error is not None
            else task.status == "failed"
        ),
        "waiting_for_model_retry": waiting_for_model_retry,
        "model_retry": model_retry if waiting_for_model_retry else None,
        "intake_item_id": task.intake_item_id,
        "selection_id": task.selection_id,
        "payload": task_payload,
        "payload_json": task_payload,
        "created_at": iso(task.created_at),
        "updated_at": iso(task.updated_at),
    }
    if session is not None and task.task_type == "search_result_intake":
        batch = session.get(ProcessingBatch, task.batch_id) if task.batch_id else None
        payload["selection_origin"] = (
            "topic_onboarding"
            if (batch is not None and (batch.request_id or "").startswith("topic-onboarding-"))
            else "manual"
        )
    if session is not None and task.selection_id:
        selection = session.get(SearchSelection, task.selection_id)
        result = (
            session.get(SearchResult, selection.result_id)
            if selection is not None
            else None
        )
        investigation = session.get(Investigation, task.investigation_id)
        if result is not None and investigation is not None:
            from .search import assess_topic_relevance

            payload["topic_relevance"] = assess_topic_relevance(
                result,
                investigation,
            )
    if session is not None and task.intake_item_id:
        item = session.scalar(
            select(IntakeItem)
            .where(IntakeItem.id == task.intake_item_id)
            .options(selectinload(IntakeItem.candidates))
        )
        if item is not None:
            from .intake import serialize_intake, serialize_intake_summary

            payload["intake_item"] = (
                serialize_intake(item, session=session)
                if include_intake_detail
                else serialize_intake_summary(item, session=session)
            )
            has_link = _object_in_investigation(
                session,
                task.investigation_id,
                "intake",
                item.id,
            )
            payload["membership"] = "active" if has_link else "removed"
            payload["removed_from_investigation"] = not has_link
            payload["intake_archived"] = item.archived_at is not None
            if (
                item.archived_at is not None
                or item.status == "confirmed"
                or task.status in ACTIVE_TASK_STATUSES
            ):
                scoped_actions: list[str] = []
            elif has_link:
                scoped_actions = ["remove_from_investigation"]
            else:
                scoped_actions = ["restore"]
            # Keep these topic-membership actions on the task. The embedded
            # intake item's allowed_actions continue to describe only global
            # inbox archive/restore semantics.
            payload["allowed_actions"] = scoped_actions
            if item.candidate_mode in {"fallback", "fallback-after-error"}:
                payload["degraded"] = True
            if item.candidate_mode == "fallback-after-error":
                payload["fallback_used"] = True
            if item.candidate_mode == "fallback" and task.status == "ready":
                payload["degradation"] = {
                    "code": "rule_fallback",
                    "stage": "generate",
                    "title": "已使用规则生成候选",
                    "message": "原文已保存；候选字段可能较少，确认前必须人工核对。",
                    "retryable": False,
                    "next_action": "继续人工审核；需要时配置模型后重新生成。",
                }
    return payload


def serialize_activity(entry: DecisionLog) -> dict[str, Any]:
    return {
        "id": entry.id,
        "investigation_id": entry.investigation_id,
        "action": entry.action,
        "actor": entry.actor,
        "object_type": entry.object_type,
        "object_id": entry.object_id,
        "task_id": entry.task_id,
        "detail": entry.detail_json or {},
        "created_at": iso(entry.created_at),
    }


def _selection_trace(
    selection: SearchSelection,
    result: SearchResult,
    event_id: str,
    *,
    outcome: str,
) -> dict[str, Any]:
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
        "selected_at": iso(utcnow()),
        "outcome": outcome,
        "attempt_count": selection.attempt_count,
    }


def _record_search_selection_event(
    session: Session,
    selection: SearchSelection,
    result: SearchResult,
    *,
    outcome: str,
) -> SearchSelectionEvent:
    event = SearchSelectionEvent(
        id=f"srche_{uuid.uuid4().hex[:24]}",
        selection_id=selection.id,
        query_run_id=result.query_run_id,
        result_id=result.id,
        outcome=outcome,
        trace_json={},
        created_at=utcnow(),
    )
    event.trace_json = _selection_trace(selection, result, event.id, outcome=outcome)
    session.add(event)
    review = dict(selection.intake_item.review or {})
    history = list(review.get("external_search_history", []))
    legacy = review.get("external_search")
    if not history and legacy:
        history.append(legacy)
    history.append(event.trace_json)
    review["external_search"] = event.trace_json
    review["external_search_history"] = history
    selection.intake_item.review = review
    selection.intake_item.updated_at = utcnow()
    return event


def _placeholder_intake(result: SearchResult) -> IntakeItem:
    now = utcnow()
    host = urlparse(result.canonical_url).hostname or result.site_name or "Unknown source"
    return IntakeItem(
        id=f"int_sear_{uuid.uuid4().hex[:16]}",
        input_type="search",
        status="queued",
        error=None,
        source_description=host,
        source_url=result.original_url,
        canonical_url=result.canonical_url,
        title=result.title or None,
        language=result.query_run.language,
        raw_snapshot="",
        raw_hash="",
        extracted_snapshot="",
        extracted_hash="",
        review={"material": {"queued_at": iso(now)}},
        created_at=now,
        updated_at=now,
    )


def _task_terminal_status_for_item(item: IntakeItem) -> str | None:
    if item.status == "confirmed":
        return "confirmed"
    if item.status in {"rejected", "cancelled"}:
        return "rejected"
    if item.status == "candidate_ready":
        return "ready"
    if item.status in {"failed", "generation_failed"}:
        return "failed"
    return None


def _candidate_fallback_class(item: IntakeItem) -> str | None:
    if item.status != "candidate_ready":
        return None
    if item.candidate_mode == "fallback-after-error":
        return "model_fallback"
    return None


def _task_state_for_intake(
    item: IntakeItem,
) -> tuple[str, str | None, str | None, str]:
    task_status = _task_terminal_status_for_item(item) or "queued"
    if task_status == "ready":
        error_class = _candidate_fallback_class(item)
        error_message = item.candidate_error if error_class else None
        selection_status = "candidate_ready"
    elif task_status == "failed":
        error_class = "intake_failed"
        error_message = item.error or item.candidate_error
        selection_status = item.status
    elif task_status == "queued":
        error_class = None
        error_message = None
        selection_status = item.status
    else:
        error_class = None
        error_message = None
        selection_status = item.status
    return task_status, error_class, error_message, selection_status


def sync_review_task_with_intake(
    session: Session,
    task: ReviewTask,
    item: IntakeItem,
    *,
    actor: str,
    include_active: bool = False,
) -> bool:
    """Make a durable task reflect its Intake without reviving active work."""
    if task.status in ACTIVE_TASK_STATUSES and not include_active:
        return False
    desired, error_class, error_message, selection_status = _task_state_for_intake(item)
    now = utcnow()
    changed = False
    if task.status != desired:
        if desired in TERMINAL_TASK_STATUSES:
            _mark_task_terminal(
                session,
                task,
                desired,
                actor=actor,
                error_class=error_class,
                error_message=error_message,
                detail={"synchronized_from_intake_status": item.status},
            )
        else:
            task.status = "queued"
            task.active_key = _active_task_key(
                task.investigation_id,
                str((task.payload_json or {}).get("result_fingerprint") or f"intake:{item.id}"),
            )
            task.queued_at = now
            task.started_at = None
            task.completed_at = None
            task.lease_owner = None
            task.lease_expires_at = None
            task.error_class = None
            task.error_message = None
            task.updated_at = now
            record_action(
                session,
                task.investigation_id,
                "task.queued",
                actor=actor,
                object_type=task.subject_type,
                object_id=task.subject_id,
                task_id=task.id,
                detail={
                    "intake_item_id": item.id,
                    "synchronized_from_intake_status": item.status,
                },
            )
            _update_task_batches(session, task)
        changed = True
    else:
        expected_active_key = (
            _active_task_key(
                task.investigation_id,
                str((task.payload_json or {}).get("result_fingerprint") or f"intake:{item.id}"),
            )
            if desired == "queued"
            else None
        )
        for field, value in (
            ("active_key", expected_active_key),
            ("error_class", error_class),
            ("error_message", error_message[:4000] if error_message else None),
        ):
            if getattr(task, field) != value:
                setattr(task, field, value)
                changed = True
        if desired in TERMINAL_TASK_STATUSES and task.completed_at is None:
            task.completed_at = now
            changed = True
        if changed:
            task.updated_at = now
            _update_task_batches(session, task)
    payload = dict(task.payload_json or {})
    if desired == "ready" and payload.get("candidate_mode") != item.candidate_mode:
        payload["candidate_mode"] = item.candidate_mode
        task.payload_json = payload
        task.updated_at = now
        changed = True
    selection = (
        session.get(SearchSelection, task.selection_id) if task.selection_id else None
    )
    if selection is not None:
        selection_outcome = (
            "ready"
            if desired == "ready"
            else "failed"
            if desired == "failed"
            else selection_status
        )
        selection_error = error_message[:4000] if error_message else None
        if (
            selection.status != selection_status
            or selection.outcome != selection_outcome
            or selection.last_error != selection_error
        ):
            selection.status = selection_status
            selection.outcome = selection_outcome
            selection.last_error = selection_error
            selection.updated_at = now
            changed = True
    return changed


def sync_linked_review_tasks_for_intake(
    session: Session,
    item: IntakeItem,
    *,
    actor: str,
    investigation_id: str | None = None,
) -> int:
    query = select(ReviewTask).where(
        ReviewTask.intake_item_id == item.id,
        _task_has_current_intake_link(),
    )
    if investigation_id is not None:
        query = query.where(ReviewTask.investigation_id == investigation_id)
    changed = 0
    for task in session.scalars(query):
        changed += int(
            sync_review_task_with_intake(
                session,
                task,
                item,
                actor=actor,
            )
        )
    return changed


def _active_task_key(investigation_id: str, fingerprint: str) -> str:
    return f"{investigation_id}:{fingerprint}"


def _batch_tasks(session: Session, batch_id: str) -> list[ReviewTask]:
    return list(
        session.scalars(
            select(ReviewTask)
            .join(ProcessingBatchEntry, ProcessingBatchEntry.task_id == ReviewTask.id)
            .where(ProcessingBatchEntry.batch_id == batch_id)
            .order_by(ProcessingBatchEntry.created_at.asc(), ProcessingBatchEntry.id.asc())
        )
    )


def _add_batch_entry(
    session: Session, batch: ProcessingBatch, task: ReviewTask, result_id: str
) -> ProcessingBatchEntry:
    entry = ProcessingBatchEntry(
        id=new_batch_entry_id(),
        batch_id=batch.id,
        task_id=task.id,
        result_id=result_id,
        created_at=utcnow(),
    )
    session.add(entry)
    return entry


def _update_batch_status(session: Session, batch_id: str | None) -> None:
    if not batch_id:
        return
    batch = session.get(ProcessingBatch, batch_id)
    if batch is None:
        return
    session.flush()
    statuses = [task.status for task in _batch_tasks(session, batch_id)]
    if not statuses:
        batch.status = "queued"
    elif any(value in ACTIVE_TASK_STATUSES for value in statuses):
        batch.status = "running" if any(value != "queued" for value in statuses) else "queued"
    elif any(value == "failed" for value in statuses):
        batch.status = "partial_failure"
    else:
        batch.status = "completed"
    batch.updated_at = utcnow()


def _update_task_batches(session: Session, task: ReviewTask) -> None:
    batch_ids = set(
        session.scalars(
            select(ProcessingBatchEntry.batch_id).where(
                ProcessingBatchEntry.task_id == task.id
            )
        )
    )
    if task.batch_id:
        batch_ids.add(task.batch_id)
    for batch_id in batch_ids:
        _update_batch_status(session, batch_id)


def _batch_response(session: Session, batch: ProcessingBatch) -> dict[str, Any]:
    _update_batch_status(session, batch.id)
    tasks = _batch_tasks(session, batch.id)
    investigation = session.get(Investigation, batch.investigation_id)
    return {
        "status": batch.status,
        "queued": any(task.status in ACTIVE_TASK_STATUSES for task in tasks),
        "investigation": (
            serialize_investigation(session, investigation) if investigation is not None else None
        ),
        "batch": serialize_batch(batch),
        "tasks": [serialize_task(task, session=session) for task in tasks],
        "results": [
            {
                "result_id": (task.payload_json or {}).get("result_id"),
                "outcome": "queued" if task.status in ACTIVE_TASK_STATUSES else task.status,
                "task_id": task.id,
                "intake_item_id": task.intake_item_id,
                "intake_status": (
                    session.get(IntakeItem, task.intake_item_id).status
                    if task.intake_item_id and session.get(IntakeItem, task.intake_item_id)
                    else None
                ),
                "error": task.error_message,
            }
            for task in tasks
        ],
    }


def enqueue_search_result_tasks(session: Session, request: Any) -> dict[str, Any]:
    """Short transaction only: no network and no model call may occur here."""
    requested_ids = list(dict.fromkeys(request.result_ids))
    results = list(
        session.scalars(
            select(SearchResult)
            .where(SearchResult.id.in_(requested_ids))
            .options(selectinload(SearchResult.query_run))
        )
    )
    found = {result.id for result in results}
    missing = [result_id for result_id in requested_ids if result_id not in found]
    if missing:
        raise ValueError(f"Search results not found: {', '.join(missing)}")

    if request.request_id:
        replay = session.scalar(
            select(ProcessingBatch).where(ProcessingBatch.request_id == request.request_id)
        )
        if replay is not None:
            expected_ids = set(
                session.scalars(
                    select(ProcessingBatchEntry.result_id).where(
                        ProcessingBatchEntry.batch_id == replay.id
                    )
                )
            )
            if replay.investigation_id != request.investigation_id and request.investigation_id:
                raise ValueError("request_id was already used for another investigation")
            if expected_ids != set(requested_ids):
                raise ValueError("request_id was already used with different search results")
            archived_item_id = session.scalar(
                select(IntakeItem.id)
                .join(ReviewTask, ReviewTask.intake_item_id == IntakeItem.id)
                .join(
                    ProcessingBatchEntry,
                    ProcessingBatchEntry.task_id == ReviewTask.id,
                )
                .where(
                    ProcessingBatchEntry.batch_id == replay.id,
                    IntakeItem.archived_at.is_not(None),
                )
                .limit(1)
            )
            if archived_item_id is not None:
                raise ArchivedIntakeError("reusing this search selection")
            return _batch_response(session, replay)

    investigation, _ = resolve_investigation_context(
        session,
        investigation_id=request.investigation_id,
        new_investigation=request.new_investigation,
        actor=request.actor,
        default_unclassified=True,
    )
    assert investigation is not None

    batch = ProcessingBatch(
        id=new_batch_id(),
        request_id=request.request_id,
        investigation_id=investigation.id,
        status="queued",
        requested_count=len(requested_ids),
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    session.add(batch)
    session.flush()
    ordered = sorted(results, key=lambda result: requested_ids.index(result.id))
    for result in ordered:
        link_object(
            session,
            investigation.id,
            "search_query",
            result.query_run_id,
            actor=request.actor,
            metadata={"keyword": result.query_run.keyword, "scope": result.query_run.scope},
            action="search.query_linked",
        )
        selection = session.scalar(
            select(SearchSelection)
            .where(SearchSelection.result_fingerprint == result.result_fingerprint)
            .options(selectinload(SearchSelection.intake_item))
        )
        if selection is None:
            item = _placeholder_intake(result)
            session.add(item)
            session.flush()
            selection = SearchSelection(
                id="srchs_"
                + hashlib.sha256(result.result_fingerprint.encode()).hexdigest()[:24],
                result_id=result.id,
                result_fingerprint=result.result_fingerprint,
                intake_item_id=item.id,
                status="queued",
                outcome="queued",
                attempt_count=0,
                last_attempt_at=utcnow(),
                last_error=None,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            session.add(selection)
            session.flush()
        else:
            item = selection.intake_item
            selection.result_id = result.id
            selection.updated_at = utcnow()
        _require_unarchived_intake(
            session, item, action="selecting this search result"
        )
        _record_search_selection_event(
            session, selection, result, outcome="queued" if item.status == "queued" else "linked"
        )
        link_object(
            session,
            investigation.id,
            "intake",
            item.id,
            actor=request.actor,
            metadata={"result_id": result.id, "query_run_id": result.query_run_id},
            action="search.result_selected",
        )
        if item.status == "confirmed" and item.final_event_id:
            if session.get(Event, item.final_event_id) is not None:
                link_object(
                    session,
                    investigation.id,
                    "event",
                    item.final_event_id,
                    actor=request.actor,
                    metadata={"intake_item_id": item.id},
                    action="event.linked_from_confirmation",
                )

        terminal_status = _task_terminal_status_for_item(item)
        existing_task = session.scalar(
            select(ReviewTask)
            .where(
                ReviewTask.investigation_id == investigation.id,
                ReviewTask.intake_item_id == item.id,
            )
            .order_by(ReviewTask.created_at.asc())
            .limit(1)
        )
        if existing_task is not None:
            # This call is idempotent and also links final_event when the reused
            # intake was confirmed before it joined this investigation.
            existing_task, _ = ensure_review_task_for_intake(
                session,
                investigation.id,
                item,
                actor=request.actor,
                payload_extra={"result_id": result.id, "query_run_id": result.query_run_id},
            )
            _add_batch_entry(session, batch, existing_task, result.id)
            record_action(
                session,
                investigation.id,
                "task.reused",
                actor=request.actor,
                object_type="search_result",
                object_id=result.id,
                task_id=existing_task.id,
                detail={"batch_id": batch.id, "status": existing_task.status},
            )
            continue
        existing_active = session.scalar(
            select(ReviewTask)
            .where(
                ReviewTask.investigation_id == investigation.id,
                ReviewTask.active_key
                == _active_task_key(investigation.id, result.result_fingerprint),
            )
            .limit(1)
        )
        if existing_active is not None:
            _add_batch_entry(session, batch, existing_active, result.id)
            record_action(
                session,
                investigation.id,
                "task.deduplicated",
                actor=request.actor,
                object_type="search_result",
                object_id=result.id,
                task_id=existing_active.id,
                detail={"batch_id": batch.id},
            )
            continue

        now = utcnow()
        task = ReviewTask(
            id=new_task_id(),
            investigation_id=investigation.id,
            batch_id=batch.id,
            task_type="search_result_intake",
            subject_type="search_result",
            subject_id=result.id,
            active_key=(
                None
                if terminal_status is not None
                else _active_task_key(investigation.id, result.result_fingerprint)
            ),
            status=terminal_status or "queued",
            attempt_number=1,
            queued_at=now,
            completed_at=now if terminal_status is not None else None,
            error_class=(
                _candidate_fallback_class(item)
                or ("intake_failed" if terminal_status == "failed" else None)
            ),
            error_message=(item.error or item.candidate_error) if terminal_status else None,
            intake_item_id=item.id,
            selection_id=selection.id,
            payload_json={
                "result_id": result.id,
                "result_fingerprint": result.result_fingerprint,
                "query_run_id": result.query_run_id,
                "requested_url": result.original_url,
            },
            created_at=now,
            updated_at=now,
        )
        session.add(task)
        session.flush()
        _add_batch_entry(session, batch, task, result.id)
        record_action(
            session,
            investigation.id,
            f"task.{task.status}",
            actor=request.actor,
            object_type="search_result",
            object_id=result.id,
            task_id=task.id,
            detail={"batch_id": batch.id, "intake_item_id": item.id},
        )
    _update_batch_status(session, batch.id)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        if request.request_id:
            replay = session.scalar(
                select(ProcessingBatch).where(
                    ProcessingBatch.request_id == request.request_id
                )
            )
            if replay is not None:
                return _batch_response(session, replay)
        raise
    return _batch_response(session, batch)


def ensure_review_task_for_intake(
    session: Session,
    investigation_id: str,
    item: IntakeItem,
    *,
    actor: str,
    payload_extra: dict[str, Any] | None = None,
) -> tuple[ReviewTask, bool]:
    """Idempotently expose a linked intake item in an investigation's task API."""
    _require_unarchived_intake(
        session, item, action="creating or reusing its review task"
    )
    if item.status == "confirmed" and item.final_event_id:
        if session.get(Event, item.final_event_id) is not None:
            link_object(
                session,
                investigation_id,
                "event",
                item.final_event_id,
                actor=actor,
                metadata={"intake_item_id": item.id},
                action="event.linked_from_confirmation",
            )
    existing = session.scalar(
        select(ReviewTask)
        .where(
            ReviewTask.investigation_id == investigation_id,
            ReviewTask.intake_item_id == item.id,
        )
        .order_by(ReviewTask.created_at.asc())
        .limit(1)
    )
    if existing is not None:
        sync_review_task_with_intake(
            session,
            existing,
            item,
            actor=actor,
        )
        return existing, False
    selection = session.scalar(
        select(SearchSelection).where(SearchSelection.intake_item_id == item.id)
    )
    result = session.get(SearchResult, selection.result_id) if selection else None
    terminal_status = _task_terminal_status_for_item(item)
    task_status = terminal_status or "queued"
    now = utcnow()
    fingerprint = (
        result.result_fingerprint if result is not None else f"intake:{item.id}"
    )
    payload: dict[str, Any] = {
        "result_fingerprint": fingerprint,
        **(payload_extra or {}),
    }
    if result is not None:
        payload.update(
            {
                "result_id": result.id,
                "query_run_id": result.query_run_id,
                "requested_url": result.original_url,
            }
        )
    fallback_class = _candidate_fallback_class(item)
    task = ReviewTask(
        id=new_task_id(),
        investigation_id=investigation_id,
        batch_id=None,
        task_type="search_result_intake" if result is not None else "intake_candidate_generation",
        subject_type="search_result" if result is not None else "intake",
        subject_id=result.id if result is not None else item.id,
        active_key=(
            _active_task_key(investigation_id, fingerprint)
            if task_status == "queued"
            else None
        ),
        status=task_status,
        attempt_number=1,
        queued_at=now,
        completed_at=now if task_status in TERMINAL_TASK_STATUSES else None,
        error_class=(
            fallback_class
            if fallback_class
            else "intake_failed"
            if task_status == "failed"
            else None
        ),
        error_message=(
            item.candidate_error
            if fallback_class == "model_fallback"
            else (item.error or item.candidate_error)
            if task_status == "failed"
            else None
        ),
        intake_item_id=item.id,
        selection_id=selection.id if selection is not None else None,
        payload_json=payload,
        created_at=now,
        updated_at=now,
    )
    session.add(task)
    session.flush()
    record_action(
        session,
        investigation_id,
        f"task.{task.status}",
        actor=actor,
        object_type=task.subject_type,
        object_id=task.subject_id,
        task_id=task.id,
        detail={"intake_item_id": item.id, **(payload_extra or {})},
    )
    return task, True


def attach_collection_intake_to_investigations(
    session: Session,
    *,
    target_id: str,
    item: IntakeItem,
    run_id: str,
    outcome: str,
) -> int:
    """Propagate a new monitored version to every topic containing its target."""
    if session.scalar(
        select(IntakeItem.archived_at).where(IntakeItem.id == item.id)
    ) is not None:
        return 0
    investigation_ids = list(
        session.scalars(
            select(InvestigationLink.investigation_id).where(
                InvestigationLink.object_type == "collection_target",
                InvestigationLink.object_id == target_id,
            )
        )
    )
    created_count = 0
    for investigation_id in investigation_ids:
        try:
            link_object(
                session,
                investigation_id,
                "intake",
                item.id,
                actor="system:collector",
                metadata={"target_id": target_id, "run_id": run_id, "outcome": outcome},
                action="collection.version_linked",
            )
            _, created = ensure_review_task_for_intake(
                session,
                investigation_id,
                item,
                actor="system:collector",
                payload_extra={"target_id": target_id, "run_id": run_id, "outcome": outcome},
            )
        except ArchivedIntakeError:
            # Collection history remains valid, but globally archived material
            # must not be revived into a topic or hidden worker queue.
            continue
        created_count += int(created)
    return created_count


def attach_existing_collection_versions_to_investigation(
    session: Session,
    *,
    investigation_id: str,
    target_id: str,
    actor: str,
) -> int:
    """Close the create-then-link race by attaching already captured versions."""
    runs = list(
        session.scalars(
            select(CollectionRun)
            .where(
                CollectionRun.target_id == target_id,
                CollectionRun.status == "succeeded",
                CollectionRun.outcome.in_(["baseline", "changed"]),
                CollectionRun.current_intake_item_id.is_not(None),
            )
            .order_by(CollectionRun.version_number.asc(), CollectionRun.completed_at.asc())
        )
    )
    seen: set[str] = set()
    created_count = 0
    for run in runs:
        item_id = run.current_intake_item_id
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        item = session.get(IntakeItem, item_id)
        if item is None or session.scalar(
            select(IntakeItem.archived_at).where(IntakeItem.id == item.id)
        ) is not None:
            continue
        try:
            link_object(
                session,
                investigation_id,
                "intake",
                item.id,
                actor=actor,
                metadata={"target_id": target_id, "run_id": run.id, "outcome": run.outcome},
                action="collection.version_linked",
            )
            _, created = ensure_review_task_for_intake(
                session,
                investigation_id,
                item,
                actor=actor,
                payload_extra={"target_id": target_id, "run_id": run.id, "outcome": run.outcome},
            )
        except ArchivedIntakeError:
            continue
        created_count += int(created)
    return created_count


def link_legacy_search_selection(
    session: Session,
    *,
    query_run_id: str,
    intake_item_id: str,
    actor: str = "system:legacy-api",
) -> None:
    item = session.get(IntakeItem, intake_item_id)
    if item is None:
        raise ValueError("Selected intake item not found")
    session.rollback()
    item = lock_intake_for_mutation(
        session,
        intake_item_id,
        action="linking this search selection",
    )
    investigation, _ = resolve_investigation_context(
        session,
        investigation_id=None,
        new_investigation=None,
        actor=actor,
        default_unclassified=True,
    )
    assert investigation is not None
    link_object(
        session,
        investigation.id,
        "search_query",
        query_run_id,
        actor=actor,
        action="search.query_linked",
    )
    link_object(
        session,
        investigation.id,
        "intake",
        intake_item_id,
        actor=actor,
        action="search.result_selected",
    )
    ensure_review_task_for_intake(
        session,
        investigation.id,
        item,
        actor=actor,
        payload_extra={"legacy_api": True},
    )


def recover_expired_review_task_leases(
    session: Session, *, now: datetime | None = None
) -> int:
    now = now or utcnow()
    tasks = list(
        session.scalars(
            select(ReviewTask).where(
                ReviewTask.status.in_(["fetching", "generating"]),
                ReviewTask.lease_expires_at.is_not(None),
                ReviewTask.lease_expires_at <= now,
            )
        )
    )
    for task in tasks:
        previous = task.status
        task.status = "queued"
        task.started_at = None
        task.lease_owner = None
        task.lease_expires_at = None
        task.lease_recoveries += 1
        task.updated_at = now
        record_action(
            session,
            task.investigation_id,
            "task.lease_recovered",
            actor="system:collector",
            object_type=task.subject_type,
            object_id=task.subject_id,
            task_id=task.id,
            detail={"from_status": previous, "to_status": "queued"},
        )
        _update_task_batches(session, task)
    session.commit()
    return len(tasks)


def claim_next_review_task(
    session: Session,
    *,
    worker_id: str,
    lease_seconds: int = DEFAULT_TASK_LEASE_SECONDS,
    now: datetime | None = None,
) -> ReviewTask | None:
    now = now or utcnow()
    recover_expired_review_task_leases(session, now=now)
    while True:
        candidate = session.execute(
            select(ReviewTask.id, ReviewTask.intake_item_id)
            .where(
                ReviewTask.status == "queued",
                ReviewTask.queued_at <= now,
                _task_has_current_intake_link(),
                _task_intake_is_visible(),
            )
            .order_by(ReviewTask.queued_at.asc(), ReviewTask.id.asc())
            .limit(1)
        ).first()
        if candidate is None:
            return None
        task_id, intake_item_id = candidate
        # Make the Intake fence the first write after ending the candidate read
        # snapshot. Archive either commits first (and this claim is skipped) or
        # waits until the fetching task is committed and is then rejected.
        session.rollback()
        if not intake_item_id:
            continue
        try:
            lock_intake_for_mutation(
                session,
                intake_item_id,
                action="claiming its review task",
            )
        except ArchivedIntakeError:
            session.rollback()
            continue
        claimed = session.execute(
            update(ReviewTask)
            .where(
                ReviewTask.id == task_id,
                ReviewTask.status == "queued",
                ReviewTask.queued_at <= now,
                _task_has_current_intake_link(),
                _task_intake_is_visible(),
            )
            .values(
                status="fetching",
                started_at=now,
                lease_owner=worker_id,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                updated_at=now,
            )
        )
        session.commit()
        if not claimed.rowcount:
            continue
        task = session.get(ReviewTask, task_id)
        assert task is not None
        record_action(
            session,
            task.investigation_id,
            "task.fetching",
            actor=f"collector:{worker_id}",
            object_type=task.subject_type,
            object_id=task.subject_id,
            task_id=task.id,
            detail={"attempt_number": task.attempt_number},
        )
        _update_task_batches(session, task)
        session.commit()
        return task


def _task_error_class(exc: Exception) -> str:
    if isinstance(exc, ModelGenerationError):
        return "model_timeout" if exc.timed_out else "model_error"
    from .importers import ReaderFallbackError
    if isinstance(exc, ReaderFallbackError):
        return _task_error_class(exc.direct_error)
    # Preserve the HTTP status while the exception object is still available;
    # a persisted message alone is not reliable enough for UI decisions.
    try:
        import httpx

        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {
            401,
            403,
            429,
        }:
            return f"http_{exc.response.status_code}"
    except Exception:
        pass
    try:
        from .collection import classify_collection_error

        return classify_collection_error(exc)
    except Exception:
        return "internal"


def _mark_task_terminal(
    session: Session,
    task: ReviewTask,
    status_value: str,
    *,
    actor: str,
    error_class: str | None = None,
    error_message: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    task.status = status_value
    task.active_key = None
    task.completed_at = utcnow()
    task.lease_owner = None
    task.lease_expires_at = None
    task.error_class = error_class
    task.error_message = error_message[:4000] if error_message else None
    task.updated_at = utcnow()
    record_action(
        session,
        task.investigation_id,
        f"task.{status_value}",
        actor=actor,
        object_type=task.subject_type,
        object_id=task.subject_id,
        task_id=task.id,
        detail={
            "intake_item_id": task.intake_item_id,
            "error_class": error_class,
            "error_message": error_message[:4000] if error_message else None,
            **(detail or {}),
        },
    )
    _update_task_batches(session, task)


def _schedule_model_retry(
    session: Session,
    task: ReviewTask,
    item: IntakeItem,
    selection: SearchSelection | None,
    exc: ModelGenerationError,
    *,
    actor: str,
    now: datetime | None = None,
    recovered_legacy_fallback: bool = False,
) -> bool:
    """Return a failed model call to the durable queue with bounded backoff."""
    if not _model_api_configured():
        return False
    payload = dict(task.payload_json or {})
    try:
        retries_used = max(0, int(payload.get("model_auto_retry_count") or 0))
    except (TypeError, ValueError):
        retries_used = 0
    retry_limit = model_auto_retry_attempts()
    if retries_used >= retry_limit:
        return False

    retry_number = retries_used + 1
    scheduled_at = now or utcnow()
    next_attempt_at = scheduled_at + timedelta(
        seconds=model_auto_retry_delay_seconds(retry_number)
    )
    error_class = _task_error_class(exc)
    error_message = str(exc)[:4000]
    fingerprint = payload.get("result_fingerprint") or task.subject_id
    active_key = _active_task_key(task.investigation_id, str(fingerprint))
    conflicting = session.scalar(
        select(ReviewTask.id).where(
            ReviewTask.active_key == active_key,
            ReviewTask.id != task.id,
        )
    )
    if conflicting:
        return False

    task.status = "queued"
    task.active_key = active_key
    task.attempt_number += 1
    # queued_at doubles as the eligibility time; claim_next_review_task excludes
    # future values so retries cannot hot-loop against a slow provider.
    task.queued_at = next_attempt_at
    task.started_at = None
    task.completed_at = None
    task.lease_owner = None
    task.lease_expires_at = None
    task.error_class = error_class
    task.error_message = error_message
    payload["force_ai_retry"] = True
    payload["model_auto_retry_count"] = retry_number
    payload["model_retry"] = {
        "status": "waiting",
        "retry_number": retry_number,
        "retry_limit": retry_limit,
        "next_attempt_at": iso(next_attempt_at),
        "last_error_class": error_class,
        "last_error_message": error_message,
        "recovered_legacy_fallback": recovered_legacy_fallback,
    }
    task.payload_json = payload
    task.updated_at = scheduled_at

    # The original snapshot is already durable. Keep the intake parsed and
    # non-reviewable until a real model result succeeds.
    item.status = "parsed"
    item.error = None
    item.candidate_mode = "pending-ai-retry"
    item.candidate_model = None
    item.candidate_error = error_message
    item.candidate_relations = []
    item.updated_at = scheduled_at
    if selection is not None:
        selection.status = "queued"
        selection.outcome = "retry_waiting"
        selection.last_error = error_message
        selection.updated_at = scheduled_at

    record_action(
        session,
        task.investigation_id,
        "task.retry_scheduled",
        actor=actor,
        object_type=task.subject_type,
        object_id=task.subject_id,
        task_id=task.id,
        detail={
            "intake_item_id": item.id,
            "error_class": error_class,
            "error_message": error_message,
            "retry_number": retry_number,
            "retry_limit": retry_limit,
            "next_attempt_at": iso(next_attempt_at),
            "recovered_legacy_fallback": recovered_legacy_fallback,
        },
    )
    _update_task_batches(session, task)
    return True


def recover_retryable_model_tasks(session: Session) -> int:
    """Move pre-auto-retry model failures back to the durable queue once."""
    if not _model_api_configured() or model_auto_retry_attempts() == 0:
        return 0
    tasks = list(
        session.scalars(
            select(ReviewTask).where(
                ReviewTask.status.in_(("ready", "failed")),
                ReviewTask.error_class.in_(("model_fallback", "model_timeout", "model_error")),
                _task_has_current_intake_link(),
                _task_intake_is_visible(),
            )
        )
    )
    recovered = 0
    for task in tasks:
        item = session.get(IntakeItem, task.intake_item_id) if task.intake_item_id else None
        if item is None or item.status in {"confirmed", "rejected", "cancelled"}:
            continue
        payload = dict(task.payload_json or {})
        if payload.get("model_auto_retry_count") is not None:
            continue
        message = task.error_message or item.candidate_error or "Previous model analysis did not complete"
        exc = ModelGenerationError(
            message,
            timed_out="timeout" in message.lower() or "deadline" in message.lower(),
        )
        selection = session.get(SearchSelection, task.selection_id) if task.selection_id else None
        if _schedule_model_retry(
            session,
            task,
            item,
            selection,
            exc,
            actor="system:model-retry-recovery",
            recovered_legacy_fallback=True,
        ):
            for candidate in list(item.candidates):
                session.delete(candidate)
            session.flush()
            recovered += 1
    if recovered:
        session.commit()
    return recovered


def _populate_intake_from_fetch(item: IntakeItem, fetched: Any) -> None:
    if not fetched.text or not fetched.text.strip():
        raise ValueError("Fetched page is empty")
    page = extract_page(fetched.text, fallback_title=item.title or "", url=fetched.resolved_url)
    quality = assess_extraction(page)
    if quality.status != "usable":
        raise ValueError("Extracted page body is too short")
    item.status = "parsed"
    item.error = None
    item.source_url = item.source_url or fetched.resolved_url
    item.canonical_url = canonicalize_url(fetched.resolved_url)
    item.source_description = item.source_description or (
        urlparse(fetched.resolved_url).hostname or "Unknown source"
    )
    item.title = page.title or item.title or None
    item.published_at = item.published_at or page.published_at
    item.media_type = fetched.media_type
    item.size_bytes = fetched.size_bytes
    item.raw_snapshot = fetched.text
    item.raw_hash = hashlib.sha256(fetched.text.encode("utf-8")).hexdigest()
    item.extracted_snapshot = page.body
    item.extracted_hash = content_hash(page.body)
    item.candidate_error = None
    item.candidate_relations = []
    review = dict(item.review or {})
    review["material"] = extracted_material_metadata(
        page,
        resolved_url=fetched.resolved_url,
        fetched_at=utcnow(),
        fetch_method=getattr(fetched, "fetch_method", "direct_http"),
        fetch_metadata=getattr(fetched, "metadata", {}),
        existing=review.get("material") or {},
        http_status=fetched.status_code,
    )
    item.review = review
    item.updated_at = utcnow()


def _require_actionable_review_task(session: Session, task: ReviewTask) -> None:
    if not task.intake_item_id:
        raise ValueError("Queued review task has no intake item")
    item_row = session.execute(
        select(IntakeItem.id, IntakeItem.archived_at).where(
            IntakeItem.id == task.intake_item_id
        )
    ).first()
    if item_row is None:
        raise ValueError("Queued intake item is missing")
    if item_row.archived_at is not None:
        raise ArchivedIntakeError("processing its review task")
    has_link = session.scalar(
        select(InvestigationLink.id)
        .where(
            InvestigationLink.investigation_id == task.investigation_id,
            InvestigationLink.object_type == "intake",
            InvestigationLink.object_id == task.intake_item_id,
        )
        .limit(1)
    )
    if has_link is None:
        raise UnlinkedReviewTaskError()


def _converge_task_with_terminal_intake(
    session: Session,
    *,
    item: IntakeItem,
    task: ReviewTask,
    selection: SearchSelection | None,
    actor: str,
) -> bool:
    """Preserve an analyst terminal decision when stale worker work completes."""
    if item.status == "confirmed":
        selection_status = "confirmed"
        task_status = "confirmed"
    elif item.status in {"rejected", "cancelled"}:
        selection_status = item.status
        task_status = "rejected"
    else:
        return False
    if selection is not None:
        selection.status = selection_status
        selection.outcome = selection_status
        selection.last_error = None
        selection.updated_at = utcnow()
    if task.status != task_status:
        _mark_task_terminal(session, task, task_status, actor=actor)
    return True


def _converge_fresh_terminal_task(
    session: Session,
    *,
    task_id: str,
    intake_item_id: str,
    actor: str,
) -> ReviewTask | None:
    """Converge task/selection under a fence that also covers archived Intake."""
    session.rollback()
    item = lock_intake_for_status_sync(session, intake_item_id)
    task = session.get(ReviewTask, task_id, populate_existing=True)
    if task is None:
        raise RuntimeError("Review task disappeared while converging its status")
    if task.intake_item_id != item.id:
        raise RuntimeError("Review task Intake changed while converging its status")
    selection = (
        session.get(SearchSelection, task.selection_id, populate_existing=True)
        if task.selection_id
        else None
    )
    if not _converge_task_with_terminal_intake(
        session,
        item=item,
        task=task,
        selection=selection,
        actor=actor,
    ):
        session.rollback()
        return None
    session.commit()
    return task


def _review_intake_baseline(item: IntakeItem) -> tuple[str, str | None]:
    return item.status, iso(item.updated_at)


def _require_review_intake_baseline(
    item: IntakeItem,
    baseline: tuple[str, str | None],
    *,
    action: str,
) -> None:
    if _review_intake_baseline(item) != baseline:
        raise IntakeMutationConflictError(action)


async def execute_claimed_review_task(task_id: str) -> ReviewTask:
    """Process exactly one task; all failures are persisted and never escape the batch."""
    with SessionLocal() as session:
        task = session.get(ReviewTask, task_id)
        if task is None:
            raise ValueError("Review task not found")
        actor = f"collector:{task.lease_owner or 'unknown'}"
        if task.status in {"confirmed", "rejected"}:
            # The claim may have committed immediately before an analyst
            # disposition closed the task in another transaction. Treat that
            # handoff as an idempotent completion instead of killing the loop,
            # and reconcile its search selection when the Intake remains visible.
            if task.intake_item_id:
                intake_item_id = task.intake_item_id
                session.rollback()
                try:
                    item = lock_intake_for_mutation(
                        session,
                        intake_item_id,
                        action="finalizing its terminal review task",
                    )
                except ArchivedIntakeError:
                    session.rollback()
                    task = _converge_fresh_terminal_task(
                        session,
                        task_id=task_id,
                        intake_item_id=intake_item_id,
                        actor=actor,
                    ) or session.get(ReviewTask, task_id)
                    if task is None:
                        raise ValueError("Review task not found")
                    return task
                task = session.get(ReviewTask, task_id)
                if task is None:
                    raise ValueError("Review task not found")
                selection = (
                    session.get(SearchSelection, task.selection_id)
                    if task.selection_id
                    else None
                )
                if _converge_task_with_terminal_intake(
                    session,
                    item=item,
                    task=task,
                    selection=selection,
                    actor=actor,
                ):
                    session.commit()
            return task
        if task.status not in {"fetching", "generating"}:
            raise ValueError(f"Review task must be claimed, got {task.status}")
        fetch_baseline: tuple[str, str | None] | None = None
        try:
            intake_item_id = task.intake_item_id
            if not intake_item_id:
                raise ValueError("Queued review task has no intake item")
            # End the task lookup snapshot and fence the Intake before the
            # worker records any processing state. This pairs task claim with
            # archive's active-task CAS instead of relying on stale reads.
            session.rollback()
            item = lock_intake_for_mutation(
                session,
                intake_item_id,
                action="processing its review task",
            )
            task = session.get(ReviewTask, task_id)
            if task is None:
                raise ValueError("Review task not found")
            payload = dict(task.payload_json or {})
            result_id = payload.get("result_id")
            if result_id is None and task.subject_type == "search_result":
                result_id = task.subject_id
            result = (
                session.scalar(
                    select(SearchResult)
                    .where(SearchResult.id == result_id)
                    .options(selectinload(SearchResult.query_run))
                )
                if result_id
                else None
            )
            if result_id and result is None:
                raise ValueError("Search result no longer exists")
            selection = session.get(SearchSelection, task.selection_id) if task.selection_id else None
            if task.selection_id and selection is None:
                raise ValueError("Queued search selection is missing")
            _require_actionable_review_task(session, task)

            if item.status == "confirmed":
                _mark_task_terminal(session, task, "confirmed", actor=actor)
                session.commit()
                return task
            if item.status in {"rejected", "cancelled"}:
                _mark_task_terminal(session, task, "rejected", actor=actor)
                session.commit()
                return task
            force_ai_retry = bool(payload.get("force_ai_retry"))
            if item.status == "candidate_ready" and not force_ai_retry:
                fallback = item.candidate_mode == "fallback-after-error"
                _mark_task_terminal(
                    session,
                    task,
                    "ready",
                    actor=actor,
                    error_class="model_fallback" if fallback else None,
                    error_message=item.candidate_error if fallback else None,
                    detail={"fallback_used": fallback},
                )
                session.commit()
                return task

            if selection is not None:
                selection.attempt_count += 1
                selection.last_attempt_at = utcnow()
                selection.outcome = "processing"
                selection.updated_at = utcnow()
            session.commit()
            task = session.get(ReviewTask, task_id)
            item = session.get(IntakeItem, task.intake_item_id) if task else None
            selection = (
                session.get(SearchSelection, task.selection_id)
                if task is not None and task.selection_id
                else None
            )
            if task is None or item is None:
                raise RuntimeError("Review task disappeared during processing")
            _require_actionable_review_task(session, task)

            if result is not None and not force_ai_retry and (
                not item.raw_snapshot or item.status in {"queued", "failed"}
            ):
                fetch_baseline = _review_intake_baseline(item)
                fetched = await fetch_public_text_response(result.original_url)
                session.rollback()
                item = lock_intake_for_mutation(
                    session,
                    intake_item_id,
                    action="processing its fetched material",
                )
                _require_review_intake_baseline(
                    item,
                    fetch_baseline,
                    action="applying its fetched material",
                )
                task = session.get(ReviewTask, task_id)
                selection = (
                    session.get(SearchSelection, task.selection_id)
                    if task is not None and task.selection_id
                    else None
                )
                if task is None:
                    raise RuntimeError("Review task disappeared after fetching")
                _require_actionable_review_task(session, task)
                _populate_intake_from_fetch(item, fetched)
            elif result is None and not item.extracted_snapshot.strip():
                raise ValueError("Intake has no persisted extracted content to process")
            else:
                session.rollback()
                item = lock_intake_for_mutation(
                    session,
                    intake_item_id,
                    action="starting candidate generation",
                )
                task = session.get(ReviewTask, task_id)
                selection = (
                    session.get(SearchSelection, task.selection_id)
                    if task is not None and task.selection_id
                    else None
                )
                if task is None:
                    raise RuntimeError("Review task disappeared before generation")
                _require_actionable_review_task(session, task)

            task.status = "generating"
            task.updated_at = utcnow()
            task.lease_expires_at = utcnow() + timedelta(seconds=model_task_lease_seconds())
            item.status = "parsed"
            item.updated_at = utcnow()
            record_action(
                session,
                task.investigation_id,
                "task.generating",
                actor=actor,
                object_type=task.subject_type,
                object_id=task.subject_id,
                task_id=task.id,
                detail={"intake_item_id": item.id},
            )
            _update_task_batches(session, task)
            session.commit()
            fetch_baseline = None
            task = session.get(ReviewTask, task_id)
            item = session.get(IntakeItem, task.intake_item_id) if task else None
            selection = (
                session.get(SearchSelection, task.selection_id)
                if task is not None and task.selection_id
                else None
            )
            if task is None or item is None:
                raise RuntimeError("Review task disappeared before candidate generation")
            _require_actionable_review_task(session, task)

            item = await generate_candidates(session, item)
            if item.status == "generation_failed":
                message = item.candidate_error or "Configured model candidate generation failed"
                lowered = message.lower()
                raise ModelGenerationError(
                    message,
                    timed_out="timeout" in lowered or "deadline" in lowered or "timed out" in lowered,
                )
            # Candidate generation commits independently. An analyst may have
            # confirmed/rejected/cancelled the item immediately afterward, so
            # restart and fence before writing the selection trace or terminal
            # task state. Always derive the outcome from the refreshed item.
            session.rollback()
            item = lock_intake_for_mutation(
                session,
                intake_item_id,
                action="finalizing its review task",
            )
            task = session.get(ReviewTask, task_id)
            selection = (
                session.get(SearchSelection, task.selection_id)
                if task is not None and task.selection_id
                else None
            )
            if task is None:
                raise RuntimeError("Review task disappeared after candidate generation")
            result = (
                session.scalar(
                    select(SearchResult)
                    .where(SearchResult.id == result_id)
                    .options(selectinload(SearchResult.query_run))
                )
                if result_id
                else None
            )
            if result_id and result is None:
                raise ValueError("Search result no longer exists")
            _require_actionable_review_task(session, task)
            if _converge_task_with_terminal_intake(
                session,
                item=item,
                task=task,
                selection=selection,
                actor=actor,
            ):
                session.commit()
                return task
            if item.status != "candidate_ready":
                raise IntakeMutationConflictError("finalizing its review task")
            fallback_error = None
            task_payload = dict(task.payload_json or {})
            task_payload["candidate_mode"] = item.candidate_mode
            task_payload.pop("model_retry", None)
            task_payload.pop("force_ai_retry", None)
            task.payload_json = task_payload
            if selection is not None:
                selection.status = item.status
                selection.outcome = "ready"
                selection.last_error = fallback_error
                selection.updated_at = utcnow()
                if result is not None:
                    _record_search_selection_event(session, selection, result, outcome="ready")
            _mark_task_terminal(
                session,
                task,
                "ready",
                actor=actor,
                error_class="model_fallback" if fallback_error else None,
                error_message=fallback_error,
                detail={"fallback_used": bool(fallback_error)},
            )
            session.commit()
            return task
        except (
            ArchivedIntakeError,
            IntakeMutationConflictError,
            UnlinkedReviewTaskError,
        ) as exc:
            session.rollback()
            task = session.get(ReviewTask, task_id)
            if task is None:
                raise
            if isinstance(exc, IntakeMutationConflictError) and task.intake_item_id:
                # The common superseding mutation is an analyst decision made
                # while the model was running. Reacquire the Intake fence and
                # derive the task/selection outcome from fresh state rather
                # than overwriting that decision with a generic worker failure.
                intake_item_id = task.intake_item_id
                session.rollback()
                try:
                    item = lock_intake_for_mutation(
                        session,
                        intake_item_id,
                        action="resolving its superseded review task",
                    )
                except ArchivedIntakeError as archived_exc:
                    session.rollback()
                    task = _converge_fresh_terminal_task(
                        session,
                        task_id=task_id,
                        intake_item_id=intake_item_id,
                        actor=actor,
                    )
                    if task is not None:
                        # Archive may legally follow a terminal decision. The
                        # task and selection now reflect that decision; do not
                        # rewrite it merely because the worker noticed later.
                        return task
                    exc = archived_exc
                else:
                    task = session.get(ReviewTask, task_id)
                    if task is None:
                        raise RuntimeError(
                            "Review task disappeared while resolving superseded work"
                        )
                    selection = (
                        session.get(SearchSelection, task.selection_id)
                        if task.selection_id
                        else None
                    )
                    if _converge_task_with_terminal_intake(
                        session,
                        item=item,
                        task=task,
                        selection=selection,
                        actor=actor,
                    ):
                        session.commit()
                        return task
            _mark_task_terminal(
                session,
                task,
                "failed",
                actor=actor,
                error_class=exc.code,
                error_message=str(exc),
                detail={"blocked_without_intake_mutation": True},
            )
            session.commit()
            return task
        except Exception as exc:
            session.rollback()
            task = session.get(ReviewTask, task_id)
            if task is None:
                raise
            item = session.get(IntakeItem, task.intake_item_id) if task.intake_item_id else None
            selection = session.get(SearchSelection, task.selection_id) if task.selection_id else None
            if item is not None:
                item_id = item.id
                session.rollback()
                try:
                    item = lock_intake_for_mutation(
                        session,
                        item_id,
                        action="recording its worker failure",
                    )
                except ArchivedIntakeError as archived_exc:
                    session.rollback()
                    task = _converge_fresh_terminal_task(
                        session,
                        task_id=task_id,
                        intake_item_id=item_id,
                        actor=actor,
                    )
                    if task is not None:
                        return task
                    task = session.get(ReviewTask, task_id)
                    if task is None:
                        raise
                    _mark_task_terminal(
                        session,
                        task,
                        "failed",
                        actor=actor,
                        error_class=archived_exc.code,
                        error_message=str(archived_exc),
                        detail={"blocked_without_intake_mutation": True},
                    )
                    session.commit()
                    return task
                task = session.get(ReviewTask, task_id)
                selection = (
                    session.get(SearchSelection, task.selection_id)
                    if task is not None and task.selection_id
                    else None
                )
                if task is None:
                    raise RuntimeError("Review task disappeared while recording failure")
                if _converge_task_with_terminal_intake(
                    session,
                    item=item,
                    task=task,
                    selection=selection,
                    actor=actor,
                ):
                    session.commit()
                    return task
                if fetch_baseline is not None:
                    try:
                        _require_review_intake_baseline(
                            item,
                            fetch_baseline,
                            action="recording its fetch failure",
                        )
                    except IntakeMutationConflictError as conflict_exc:
                        _mark_task_terminal(
                            session,
                            task,
                            "failed",
                            actor=actor,
                            error_class=conflict_exc.code,
                            error_message=str(conflict_exc),
                            detail={"blocked_without_intake_mutation": True},
                        )
                        session.commit()
                        return task
                if isinstance(exc, ModelGenerationError) and _schedule_model_retry(
                    session,
                    task,
                    item,
                    selection,
                    exc,
                    actor=actor,
                ):
                    session.commit()
                    return task
                item.status = "generation_failed" if isinstance(exc, ModelGenerationError) else "failed"
                item.error = str(exc)[:4000]
                item.updated_at = utcnow()
            if selection is not None:
                selection.status = "failed"
                selection.outcome = "failed"
                selection.last_error = str(exc)[:4000]
                selection.updated_at = utcnow()
            _mark_task_terminal(
                session,
                task,
                "failed",
                actor=actor,
                error_class=_task_error_class(exc),
                error_message=str(exc),
            )
            session.commit()
            return task


async def run_review_task_once(
    *, worker_id: str | None = None, lease_seconds: int = DEFAULT_TASK_LEASE_SECONDS
) -> ReviewTask | None:
    identity = worker_id or review_worker_identity()
    with SessionLocal() as session:
        claimed = claim_next_review_task(
            session, worker_id=identity, lease_seconds=lease_seconds
        )
        task_id = claimed.id if claimed is not None else None
    return await execute_claimed_review_task(task_id) if task_id else None


def retry_review_task(
    session: Session,
    task: ReviewTask,
    *,
    actor: str,
) -> ReviewTask:
    if task.intake_item_id:
        task_id = task.id
        intake_item_id = task.intake_item_id
        item = session.get(IntakeItem, intake_item_id)
        if item is None:
            raise ValueError("The task intake item no longer exists")
        if not _object_in_investigation(
            session,
            task.investigation_id,
            "intake",
            intake_item_id,
        ):
            raise ValueError(
                "Restore the intake item to this investigation before retrying its review task"
            )
        session.rollback()
        lock_intake_for_mutation(
            session,
            intake_item_id,
            action="retrying its review task",
        )
        task = session.get(ReviewTask, task_id)
        if task is None:
            raise ValueError("Review task not found")
        if not _object_in_investigation(
            session,
            task.investigation_id,
            "intake",
            intake_item_id,
        ):
            raise ValueError(
                "Restore the intake item to this investigation before retrying its review task"
            )
    failed_model_retry = task.status == "failed" and task.error_class in {"model_timeout", "model_error"}
    fallback_retry = task.status == "ready" and task.error_class == "model_fallback"
    if task.status != "failed" and not fallback_retry:
        raise ValueError("Only failed tasks or model-fallback tasks can be retried")
    payload = dict(task.payload_json or {})
    fingerprint = payload.get("result_fingerprint") or task.subject_id
    active_key = _active_task_key(task.investigation_id, str(fingerprint))
    conflicting = session.scalar(
        select(ReviewTask.id).where(
            ReviewTask.active_key == active_key,
            ReviewTask.id != task.id,
        )
    )
    if conflicting:
        raise ValueError("An active retry already exists for this investigation result")
    previous = task.status
    previous_error_class = task.error_class
    previous_error_message = task.error_message
    task.status = "queued"
    task.active_key = active_key
    task.attempt_number += 1
    task.queued_at = utcnow()
    task.started_at = None
    task.completed_at = None
    task.lease_owner = None
    task.lease_expires_at = None
    task.error_class = None
    task.error_message = None
    payload["force_ai_retry"] = fallback_retry or failed_model_retry
    payload["model_auto_retry_count"] = 0
    payload.pop("model_retry", None)
    task.payload_json = payload
    task.updated_at = utcnow()
    record_action(
        session,
        task.investigation_id,
        "task.retry",
        actor=actor,
        object_type=task.subject_type,
        object_id=task.subject_id,
        task_id=task.id,
        detail={
            "from_status": previous,
            "to_status": "queued",
            "attempt_number": task.attempt_number,
            "retry_model": fallback_retry,
            "previous_error_class": previous_error_class,
            "previous_error_message": previous_error_message,
        },
    )
    _update_task_batches(session, task)
    session.commit()
    return task


def record_intake_disposition(
    session: Session,
    item: IntakeItem,
    *,
    status_value: str,
    actor: str,
    event_id: str | None = None,
    reason: str | None = None,
) -> None:
    investigation_ids = list(
        session.scalars(
            select(InvestigationLink.investigation_id).where(
                InvestigationLink.object_type == "intake",
                InvestigationLink.object_id == item.id,
            )
        )
    )
    for investigation_id in investigation_ids:
        if event_id:
            link_object(
                session,
                investigation_id,
                "event",
                event_id,
                actor=actor,
                metadata={"intake_item_id": item.id},
                action="event.linked_from_confirmation",
            )
        tasks = list(
            session.scalars(
                select(ReviewTask).where(
                    ReviewTask.investigation_id == investigation_id,
                    ReviewTask.intake_item_id == item.id,
                )
            )
        )
        task_status = "confirmed" if status_value == "confirmed" else "rejected"
        for task in tasks:
            if task.status == task_status:
                continue
            _mark_task_terminal(
                session,
                task,
                task_status,
                actor=actor,
                detail={"event_id": event_id, "reason": reason},
            )
        record_action(
            session,
            investigation_id,
            f"intake.{status_value}",
            actor=actor,
            object_type="intake",
            object_id=item.id,
            detail={"event_id": event_id, "reason": reason},
        )


def investigation_event_ids(session: Session, investigation_id: str) -> list[str]:
    if session.get(Investigation, investigation_id) is None:
        raise ValueError("Investigation not found")
    return list(
        session.scalars(
            select(InvestigationLink.object_id)
            .where(
                InvestigationLink.investigation_id == investigation_id,
                InvestigationLink.object_type == "event",
                InvestigationLink.role != SOURCE_EVENT_LINK_ROLE,
            )
            .order_by(InvestigationLink.created_at.asc(), InvestigationLink.id.asc())
        )
    )


def _object_exists(session: Session, object_type: str, object_id: str) -> bool:
    model = {
        "search_query": SearchQueryRun,
        "intake": IntakeItem,
        "collection_target": CollectionTarget,
        "event": Event,
    }[object_type]
    return session.get(model, object_id) is not None


def _investigation_kind(investigation_id: str) -> str:
    if investigation_id == DEMO_INVESTIGATION_ID:
        return "demo"
    if investigation_id == UNCLASSIFIED_INVESTIGATION_ID:
        return "system"
    return "user"


def _membership_summary(investigation: Investigation) -> dict[str, Any]:
    return {
        "id": investigation.id,
        "title": investigation.title,
        "kind": _investigation_kind(investigation.id),
        "status": investigation.status,
    }


def _object_in_investigation(
    session: Session,
    investigation_id: str,
    object_type: str,
    object_id: str,
) -> bool:
    return (
        session.scalar(
            select(InvestigationLink.id)
            .where(
                InvestigationLink.investigation_id == investigation_id,
                InvestigationLink.object_type == object_type,
                InvestigationLink.object_id == object_id,
            )
            .limit(1)
        )
        is not None
    )


def _object_memberships(
    session: Session, object_type: str, object_id: str
) -> list[dict[str, Any]]:
    query = (
            select(Investigation)
            .join(
                InvestigationLink,
                InvestigationLink.investigation_id == Investigation.id,
            )
            .where(
                InvestigationLink.object_type == object_type,
                InvestigationLink.object_id == object_id,
            )
            .order_by(Investigation.title.asc(), Investigation.id.asc())
    )
    if object_type == "event":
        query = query.where(InvestigationLink.role != SOURCE_EVENT_LINK_ROLE)
    investigations = list(session.scalars(query).unique())
    return [_membership_summary(investigation) for investigation in investigations]


def _entity_memberships(session: Session, entity_id: str) -> list[dict[str, Any]]:
    """Topic ownership of an entity is derived from its linked topic events."""
    investigations = list(
        session.scalars(
            select(Investigation)
            .join(
                InvestigationLink,
                InvestigationLink.investigation_id == Investigation.id,
            )
            .join(
                EventEntity,
                EventEntity.event_id == InvestigationLink.object_id,
            )
            .where(
                InvestigationLink.object_type == "event",
                InvestigationLink.role != SOURCE_EVENT_LINK_ROLE,
                EventEntity.entity_id == entity_id,
            )
            .order_by(Investigation.title.asc(), Investigation.id.asc())
        ).unique()
    )
    return [_membership_summary(investigation) for investigation in investigations]


def _topic_event_ids(session: Session, investigation_id: str) -> list[str]:
    return list(
        session.scalars(
            select(InvestigationLink.object_id).where(
                InvestigationLink.investigation_id == investigation_id,
                InvestigationLink.object_type == "event",
                InvestigationLink.role != SOURCE_EVENT_LINK_ROLE,
            )
        )
    )


def _topic_entity_ids(session: Session, investigation_id: str) -> list[str]:
    event_ids = _topic_event_ids(session, investigation_id)
    if not event_ids:
        return []
    return list(
        dict.fromkeys(
            session.scalars(
                select(EventEntity.entity_id)
                .where(EventEntity.event_id.in_(event_ids))
                .order_by(EventEntity.entity_id.asc())
            )
        )
    )


def _active_topic_event_models(session: Session, investigation_id: str) -> list[Event]:
    event_ids = investigation_event_ids(session, investigation_id)
    if not event_ids:
        return []
    models = list(
        session.scalars(event_query().where(Event.id.in_(event_ids))).unique()
    )
    by_id = {event.id: event for event in models}
    return [by_id[event_id] for event_id in event_ids if event_id in by_id]


def _reorganization_fingerprint(events: list[Event]) -> str:
    state = []
    for event in events:
        state.append(
            {
                "id": event.id,
                "updated_at": iso(event.updated_at),
                "documents": sorted(link.document_id for link in event.document_links),
                "claims": [
                    {
                        "id": claim.id,
                        "text": claim.text,
                        "status": claim.status,
                        "evidence": sorted(evidence.id for evidence in claim.evidence_items),
                    }
                    for claim in sorted(event.claims, key=lambda item: item.id)
                ],
            }
        )
    encoded = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _reorganization_model_payload(
    investigation: Investigation,
    events: list[Event],
) -> tuple[dict[str, Any], dict[str, Evidence]]:
    source_events: list[dict[str, Any]] = []
    evidence_by_id: dict[str, Evidence] = {}
    for event in events:
        event_metadata = event.metadata_json or {}
        evidence_entries: list[dict[str, Any]] = []
        for claim in sorted(event.claims, key=lambda item: item.id):
            for evidence in sorted(claim.evidence_items, key=lambda item: item.id):
                evidence_by_id[evidence.id] = evidence
                evidence_entries.append(
                    {
                        "evidence_id": evidence.id,
                        "claim_id": claim.id,
                        "source_name": evidence.document.source.name,
                        "source_group": evidence.document.source.independence_group,
                        "stance": evidence.stance,
                        "published_at": iso(evidence.document.published_at),
                        "snippet": evidence.snippet[:1200],
                    }
                )
        source_events.append(
            {
                "source_event_id": event.id,
                "title": event.title[:500],
                "summary": event.summary[:900],
                "event_time": None
                if event_metadata.get("start_at_known") is False
                else iso(event.start_at),
                "location_name": event.location_name
                if event.location_name and event.location_name.lower() != "unknown"
                else None,
                "evidence": evidence_entries[:20],
            }
        )
    return (
        {
            "topic": {
                "title": investigation.title,
                "question": investigation.question,
                "report_language": "zh-CN",
            },
            "instructions": [
                "A webpage is source material, not automatically a real-world event.",
                "Group source events that describe the same real-world occurrence.",
                "Return concise Simplified Chinese findings instead of copied article paragraphs.",
                "Use only supplied source_event_id and evidence_id values.",
                "Every finding must cite at least one supplied evidence_id.",
                "When grouped source events support the same finding, cite at least one evidence_id from each independent source.",
                "Do not invent a time or location; use an exact supplied value or null.",
                "Cover every supplied source_event_id exactly once.",
            ],
            "source_events": source_events,
        },
        evidence_by_id,
    )


def _clean_reorganization_text(value: Any, *, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _evidence_source_status(evidence_models: list[Evidence]) -> dict[str, Any]:
    all_groups = {
        evidence.document.source.independence_group or evidence.document.source.id
        for evidence in evidence_models
    }
    supporting = {
        evidence.document.source.independence_group or evidence.document.source.id
        for evidence in evidence_models
        if evidence.stance in {"supports", "context"}
    }
    contradicting = {
        evidence.document.source.independence_group or evidence.document.source.id
        for evidence in evidence_models
        if evidence.stance == "contradicts"
    }
    if contradicting:
        status_value = "contested"
    elif len(supporting) >= 2:
        status_value = "supported"
    elif all_groups:
        status_value = "single_source"
    else:
        status_value = "unverified"
    return {
        "status": status_value,
        "independent_source_count": len(all_groups),
        "supporting_source_count": len(supporting),
        "contradicting_source_count": len(contradicting),
    }


def _validate_reorganization_result(
    result: Any,
    events: list[Event],
    evidence_by_id: dict[str, Evidence],
) -> dict[str, Any]:
    if not isinstance(result, dict) or not isinstance(result.get("groups"), list):
        raise ValueError("模型没有返回可用的事件分组")
    available_event_ids = {event.id for event in events}
    event_by_id = {event.id: event for event in events}
    used_event_ids: list[str] = []
    groups: list[dict[str, Any]] = []
    for raw_group in result["groups"][:30]:
        if not isinstance(raw_group, dict):
            continue
        title = _clean_reorganization_text(raw_group.get("title"), limit=500)
        summary = _clean_reorganization_text(raw_group.get("summary"), limit=2000)
        source_event_ids = list(
            dict.fromkeys(
                str(value)
                for value in (raw_group.get("source_event_ids") or [])
                if str(value) in available_event_ids
            )
        )
        if not title or not summary or not source_event_ids:
            raise ValueError("事件分组缺少标题、摘要或资料引用")
        allowed_evidence_ids = {
            evidence.id
            for event_id in source_event_ids
            for claim in event_by_id[event_id].claims
            for evidence in claim.evidence_items
        }
        findings: list[dict[str, Any]] = []
        for raw_finding in (raw_group.get("findings") or [])[:8]:
            if not isinstance(raw_finding, dict):
                continue
            text_value = _clean_reorganization_text(raw_finding.get("text"), limit=600)
            evidence_ids = list(
                dict.fromkeys(
                    str(value)
                    for value in (raw_finding.get("evidence_ids") or [])
                    if str(value) in allowed_evidence_ids
                )
            )
            if not text_value or not evidence_ids:
                raise ValueError(f"事件“{title}”中的发现缺少文字或原文依据")
            normalized_text = "".join(text_value.lower().split())
            if any(
                normalized_text == "".join(evidence_by_id[evidence_id].snippet.lower().split())
                for evidence_id in evidence_ids
            ):
                raise ValueError(f"事件“{title}”中的发现仍在复制原文，未完成归纳")
            status_payload = _evidence_source_status(
                [evidence_by_id[evidence_id] for evidence_id in evidence_ids]
            )
            findings.append(
                {
                    "text": text_value,
                    "evidence_ids": evidence_ids,
                    "source_verification": status_payload,
                }
            )
        if not findings:
            raise ValueError(f"事件“{title}”没有可回链的关键发现")
        allowed_times = {
            iso(event_by_id[event_id].start_at)
            for event_id in source_event_ids
            if (event_by_id[event_id].metadata_json or {}).get("start_at_known") is not False
        }
        event_time = raw_group.get("event_time")
        if event_time not in allowed_times:
            event_time = None
        allowed_locations = {
            event_by_id[event_id].location_name
            for event_id in source_event_ids
            if event_by_id[event_id].location_name
            and event_by_id[event_id].location_name.lower() != "unknown"
        }
        location_name = raw_group.get("location_name")
        if location_name not in allowed_locations:
            location_name = None
        groups.append(
            {
                "title": title,
                "summary": summary,
                "event_time": event_time,
                "location_name": location_name,
                "source_event_ids": source_event_ids,
                "findings": findings,
            }
        )
        used_event_ids.extend(source_event_ids)
    if not groups:
        raise ValueError("模型没有形成任何真实事件")
    if len(used_event_ids) != len(set(used_event_ids)):
        raise ValueError("同一份资料被模型放入了多个事件")
    if set(used_event_ids) != available_event_ids:
        raise ValueError("模型没有完整整理全部已确认资料")
    current_answer = _clean_reorganization_text(result.get("current_answer"), limit=1600)
    if not current_answer:
        current_answer = "；".join(group["summary"] for group in groups[:3])[:1600]
    information_gaps = [
        cleaned
        for value in (result.get("information_gaps") or [])[:8]
        if (cleaned := _clean_reorganization_text(value, limit=500))
    ]
    return {
        "current_answer": current_answer,
        "groups": groups,
        "information_gaps": information_gaps,
    }


async def create_reorganization_preview(
    session: Session,
    investigation: Investigation,
) -> dict[str, Any]:
    events = _active_topic_event_models(session, investigation.id)
    if len(events) < 2:
        raise ValueError("至少需要两个已确认事件才能重新整理专题")
    model_payload, evidence_by_id = _reorganization_model_payload(investigation, events)
    if not evidence_by_id:
        raise ValueError("当前专题没有可回链的原文依据，无法重新整理")
    from .llm import run_model_task

    response = await run_model_task("synthesize_investigation", model_payload)
    if response.get("mode") != "api":
        raise ValueError("大模型当前不可用，未修改专题内容")
    draft = _validate_reorganization_result(response.get("result"), events, evidence_by_id)
    fingerprint = _reorganization_fingerprint(events)
    entry = record_action(
        session,
        investigation.id,
        "reorganization.previewed",
        actor="analyst",
        object_type="reorganization",
        detail={
            "fingerprint": fingerprint,
            "source_event_ids": [event.id for event in events],
            "source_event_count": len(events),
            "draft": draft,
            "model": response.get("model"),
        },
    )
    entry.object_id = entry.id
    session.commit()
    return {
        "draft_id": entry.id,
        "source_event_count": len(events),
        "proposed_event_count": len(draft["groups"]),
        "current_answer": draft["current_answer"],
        "groups": draft["groups"],
        "information_gaps": draft["information_gaps"],
        "model": response.get("model"),
        "confirmable": True,
    }


def confirm_reorganization(
    session: Session,
    investigation: Investigation,
    request: InvestigationReorganizationConfirmRequest,
) -> dict[str, Any]:
    prior = session.scalar(
        select(DecisionLog).where(
            DecisionLog.investigation_id == investigation.id,
            DecisionLog.action == "reorganization.confirmed",
            DecisionLog.object_id == request.draft_id,
        )
    )
    if prior is not None:
        return {
            "created": False,
            "draft_id": request.draft_id,
            "event_ids": (prior.detail_json or {}).get("event_ids", []),
        }
    preview = session.scalar(
        select(DecisionLog).where(
            DecisionLog.id == request.draft_id,
            DecisionLog.investigation_id == investigation.id,
            DecisionLog.action == "reorganization.previewed",
        )
    )
    if preview is None:
        raise ValueError("重新整理预览不存在或不属于当前专题")
    preview_detail = preview.detail_json or {}
    draft = preview_detail.get("draft") or {}
    events = _active_topic_event_models(session, investigation.id)
    if _reorganization_fingerprint(events) != preview_detail.get("fingerprint"):
        raise ValueError("专题内容在预览后已经变化，请重新生成预览")
    event_by_id = {event.id: event for event in events}
    evidence_by_id = {
        evidence.id: evidence
        for event in events
        for claim in event.claims
        for evidence in claim.evidence_items
    }
    active_links = list(
        session.scalars(
            select(InvestigationLink).where(
                InvestigationLink.investigation_id == investigation.id,
                InvestigationLink.object_type == "event",
                InvestigationLink.role != SOURCE_EVENT_LINK_ROLE,
            )
        )
    )
    for link in active_links:
        link.role = SOURCE_EVENT_LINK_ROLE
        link.metadata_json = {
            **(link.metadata_json or {}),
            "reorganized_by": request.draft_id,
        }
    created_event_ids: list[str] = []
    for group in draft.get("groups") or []:
        source_event_ids = group.get("source_event_ids") or []
        source_events = [event_by_id[event_id] for event_id in source_event_ids]
        known_time = None
        if group.get("event_time"):
            known_time = datetime.fromisoformat(str(group["event_time"]).replace("Z", "+00:00"))
        event = Event(
            id="evt_synthesis_" + uuid.uuid4().hex[:16],
            title=group["title"],
            summary=group["summary"],
            event_type=source_events[0].event_type if source_events else "incident",
            start_at=known_time or datetime(1970, 1, 1, tzinfo=timezone.utc),
            end_at=None,
            latitude=None,
            longitude=None,
            location_name=group.get("location_name") or "",
            importance=max(
                (event.importance for event in source_events),
                key=lambda value: {"low": 0, "medium": 1, "high": 2, "critical": 3}.get(value, 1),
                default="medium",
            ),
            status="confirmed",
            confidence=0.5,
            metadata_json={
                "start_at_known": known_time is not None,
                "confirmation_stage": "topic-reorganization-human-confirmed",
                "source_event_ids": source_event_ids,
                "reorganization_id": request.draft_id,
            },
        )
        session.add(event)
        session.flush()
        document_ids: set[str] = set()
        entity_ids: set[str] = set()
        for source_event in source_events:
            for document_link in source_event.document_links:
                if document_link.document_id not in document_ids:
                    session.add(EventDocument(event_id=event.id, document_id=document_link.document_id, relevance=1.0))
                    document_ids.add(document_link.document_id)
            for entity_link in source_event.entity_links:
                if entity_link.entity_id not in entity_ids:
                    session.add(EventEntity(event_id=event.id, entity_id=entity_link.entity_id, role=entity_link.role))
                    entity_ids.add(entity_link.entity_id)
        for finding in group.get("findings") or []:
            evidence_models = [
                evidence_by_id[evidence_id]
                for evidence_id in finding.get("evidence_ids") or []
                if evidence_id in evidence_by_id
            ]
            source_status = _evidence_source_status(evidence_models)
            claim = Claim(
                id="clm_synthesis_" + uuid.uuid4().hex[:16],
                event_id=event.id,
                text=finding["text"],
                status=source_status["status"],
                confidence=0.7 if source_status["status"] == "supported" else 0.5,
                origin="human-confirmed",
                temporal_scope="",
            )
            session.add(claim)
            session.flush()
            for source_evidence in evidence_models:
                session.add(
                    Evidence(
                        id="evd_synthesis_" + uuid.uuid4().hex[:16],
                        claim_id=claim.id,
                        document_id=source_evidence.document_id,
                        snapshot_id=source_evidence.snapshot_id,
                        snippet=source_evidence.snippet,
                        start_offset=source_evidence.start_offset,
                        end_offset=source_evidence.end_offset,
                        stance="supports" if source_evidence.stance == "context" else source_evidence.stance,
                        strength=source_evidence.strength,
                        note="由专题重新整理复用已确认原文依据",
                    )
                )
        session.add(
            Assessment(
                id="asm_synthesis_" + uuid.uuid4().hex[:16],
                event_id=event.id,
                judgement=group["summary"],
                assumptions=[],
                alternatives=[],
                information_gaps=draft.get("information_gaps") or [],
                falsifiers=[],
                confidence=0.6,
                generated_by="human-confirmed-topic-synthesis",
            )
        )
        link_object(
            session,
            investigation.id,
            "event",
            event.id,
            role="member",
            actor=request.actor,
            metadata={
                "reorganization_id": request.draft_id,
                "source_event_ids": source_event_ids,
            },
            action="event.linked_from_reorganization",
        )
        created_event_ids.append(event.id)
    investigation.updated_at = utcnow()
    record_action(
        session,
        investigation.id,
        "reorganization.confirmed",
        actor=request.actor,
        object_type="reorganization",
        object_id=request.draft_id,
        detail={
            "event_ids": created_event_ids,
            "source_event_ids": preview_detail.get("source_event_ids", []),
            "current_answer": draft.get("current_answer"),
            "information_gaps": draft.get("information_gaps", []),
        },
    )
    session.commit()
    return {
        "created": True,
        "draft_id": request.draft_id,
        "event_ids": created_event_ids,
        "source_event_count": len(preview_detail.get("source_event_ids", [])),
        "event_count": len(created_event_ids),
    }


def _confirmation_scope_errors(
    session: Session,
    investigation_id: str,
    request: IntakeConfirmationRequest,
    *,
    allow_cross_investigation: bool,
) -> list[str]:
    errors: list[str] = []
    if not allow_cross_investigation:
        if request.merge_event_id and not _object_in_investigation(
            session, investigation_id, "event", request.merge_event_id
        ):
            errors.append(
                "Selected merge event is outside this investigation; explicitly enable cross-investigation reuse to continue"
            )
        topic_entity_ids = set(_topic_entity_ids(session, investigation_id))
        for decision in request.entities:
            if (
                decision.action == "merge"
                and decision.merge_entity_id
                and decision.merge_entity_id not in topic_entity_ids
            ):
                errors.append(
                    f"Entity merge target {decision.merge_entity_id} is outside this investigation; explicitly enable cross-investigation reuse to continue"
                )

    investigation = session.get(Investigation, investigation_id)
    event_time: datetime | None = None
    if request.disposition == "merge" and request.merge_event_id:
        target = session.get(Event, request.merge_event_id)
        if target is not None and (target.metadata_json or {}).get("start_at_known") is not False:
            event_time = target.start_at
    elif request.event.start_at:
        try:
            event_time = parse_datetime(request.event.start_at)
        except ValueError:
            event_time = None
    if investigation is not None and event_time is not None:
        if event_time.tzinfo is None:
            event_time = event_time.replace(tzinfo=timezone.utc)
        event_time = event_time.astimezone(timezone.utc)
        event_start = investigation.event_start_at
        event_end = investigation.event_end_at
        if event_start is not None:
            if event_start.tzinfo is None:
                event_start = event_start.replace(tzinfo=timezone.utc)
            event_start = event_start.astimezone(timezone.utc)
            if event_time < event_start:
                errors.append(
                    f"Event time {iso(event_time)} is earlier than investigation start {iso(event_start)}"
                )
        if event_end is not None:
            if event_end.tzinfo is None:
                event_end = event_end.replace(tzinfo=timezone.utc)
            event_end = event_end.astimezone(timezone.utc)
            if event_time > event_end:
                errors.append(
                    f"Event time {iso(event_time)} is later than investigation end {iso(event_end)}"
                )
    return errors


def _review_scope_payload(
    session: Session,
    investigation_id: str,
    request: IntakeConfirmationRequest,
    *,
    allow_cross_investigation: bool,
) -> dict[str, Any]:
    merge_event = None
    if request.merge_event_id:
        owners = _object_memberships(session, "event", request.merge_event_id)
        merge_event = {
            "id": request.merge_event_id,
            "in_scope": any(owner["id"] == investigation_id for owner in owners),
            "investigations": owners,
            "unassigned": not owners,
        }
    merge_entities = []
    for decision in request.entities:
        if decision.action != "merge" or not decision.merge_entity_id:
            continue
        owners = _entity_memberships(session, decision.merge_entity_id)
        merge_entities.append(
            {
                "candidate_key": decision.candidate_key,
                "id": decision.merge_entity_id,
                "in_scope": any(owner["id"] == investigation_id for owner in owners),
                "investigations": owners,
                "unassigned": not owners,
            }
        )
    return {
        "investigation_id": investigation_id,
        "mode": "explicit-cross-investigation"
        if allow_cross_investigation
        else "investigation-only",
        "allow_cross_investigation": allow_cross_investigation,
        "merge_event": merge_event,
        "merge_entities": merge_entities,
    }


def _next_review_task(
    session: Session, investigation_id: str, *, exclude_intake_id: str
) -> ReviewTask | None:
    candidates = list(
        session.scalars(
            select(ReviewTask)
            .where(
                ReviewTask.investigation_id == investigation_id,
                ReviewTask.intake_item_id != exclude_intake_id,
                ReviewTask.status.in_(["ready", "failed", "queued", "fetching", "generating"]),
                _task_has_current_intake_link(),
                _task_intake_is_visible(),
            )
            .order_by(ReviewTask.queued_at.asc(), ReviewTask.id.asc())
            .limit(500)
        )
    )
    priority = {"ready": 0, "failed": 1, "queued": 2, "fetching": 3, "generating": 4}
    return min(
        candidates,
        key=lambda task: (priority.get(task.status, 99), task.queued_at, task.id),
        default=None,
    )


def _require_scoped_intake(
    session: Session, investigation_id: str, item_id: str
) -> IntakeItem:
    if session.get(Investigation, investigation_id) is None:
        raise HTTPException(status_code=404, detail="Investigation not found")
    if not _object_in_investigation(session, investigation_id, "intake", item_id):
        # Do not leak whether an unlinked intake object exists globally.
        raise HTTPException(
            status_code=404,
            detail="Intake item not found in this investigation",
        )
    item = session.scalar(
        select(IntakeItem)
        .where(IntakeItem.id == item_id)
        .options(selectinload(IntakeItem.candidates))
    )
    if item is None:
        raise HTTPException(
            status_code=404,
            detail="Intake item not found in this investigation",
        )
    return item


@router.post("", status_code=status.HTTP_201_CREATED)
def create_investigation(
    request: InvestigationCreate,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    investigation = create_investigation_record(session, request)
    session.commit()
    return serialize_investigation(session, investigation, include_detail=True)


@router.get("")
def list_investigations(
    status_value: str | None = Query(default=None, alias="status"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    filters = []
    if status_value:
        filters.append(Investigation.status == status_value)
    total = int(session.scalar(select(func.count()).select_from(Investigation).where(*filters)) or 0)
    query = (
        select(Investigation)
        .where(*filters)
        .order_by(Investigation.updated_at.desc(), Investigation.id.asc())
        .offset(offset)
        .limit(limit)
    )
    items = list(session.scalars(query))
    return {
        "items": [serialize_investigation(session, item) for item in items],
        "count": total,
        "offset": offset,
        "limit": limit,
    }


@router.post("/tasks/{task_id}/retry")
@task_router.post("/{task_id}/retry")
def retry_task(
    task_id: str,
    request: ReviewTaskRetryRequest | None = None,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    task = session.get(ReviewTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Review task not found")
    try:
        return serialize_task(
            retry_review_task(
                session,
                task,
                actor=request.actor if request is not None else "analyst",
            ),
            session=session,
        )
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/tasks/{task_id}")
@task_router.get("/{task_id}")
def get_task(task_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    task = session.get(ReviewTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Review task not found")
    return serialize_task(task, session=session, include_intake_detail=True)


@router.get("/{investigation_id}")
def get_investigation(
    investigation_id: str,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    investigation = session.get(Investigation, investigation_id)
    if investigation is None:
        raise HTTPException(status_code=404, detail="Investigation not found")
    return serialize_investigation(session, investigation, include_detail=True)


@router.get("/{investigation_id}/outcome")
def get_investigation_outcome(
    investigation_id: str,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    investigation = session.get(Investigation, investigation_id)
    if investigation is None:
        raise HTTPException(status_code=404, detail="Investigation not found")
    return serialize_investigation_outcome(session, investigation)


@router.post("/{investigation_id}/reorganization/preview")
async def preview_investigation_reorganization(
    investigation_id: str,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    investigation = session.get(Investigation, investigation_id)
    if investigation is None:
        raise HTTPException(status_code=404, detail="Investigation not found")
    try:
        return await create_reorganization_preview(session, investigation)
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        session.rollback()
        raise HTTPException(
            status_code=502,
            detail="专题整理服务暂时不可用，原有专题内容没有改变",
        ) from exc


@router.post("/{investigation_id}/reorganization/confirm")
def confirm_investigation_reorganization(
    investigation_id: str,
    request: InvestigationReorganizationConfirmRequest,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    investigation = session.get(Investigation, investigation_id)
    if investigation is None:
        raise HTTPException(status_code=404, detail="Investigation not found")
    try:
        return confirm_reorganization(session, investigation, request)
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{investigation_id}/intake/{item_id}")
def get_investigation_intake(
    investigation_id: str,
    item_id: str,
    visibility: Literal["active", "removed", "all"] = Query(default="active"),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Load one full material only after the analyst opens it."""
    from .intake import serialize_intake

    if visibility == "active":
        item = _require_scoped_intake(session, investigation_id, item_id)
        if item.archived_at is not None:
            raise HTTPException(
                status_code=404,
                detail="Intake item not found in this investigation",
            )
    else:
        if session.get(Investigation, investigation_id) is None:
            raise HTTPException(status_code=404, detail="Investigation not found")
        has_link = _object_in_investigation(
            session, investigation_id, "intake", item_id
        )
        has_history = (
            _investigation_intake_task(session, investigation_id, item_id) is not None
            or (
                _removed_intake_link_entry(session, investigation_id, item_id)
                is not None
            )
        )
        visible_in_scope = (
            (visibility == "removed" and not has_link and has_history)
            or (visibility == "all" and (has_link or has_history))
        )
        if not visible_in_scope:
            # A historical task or removal log is durable proof that the item
            # previously belonged to this topic. Do not expose unrelated IDs.
            raise HTTPException(
                status_code=404,
                detail="Intake item not found in this investigation",
            )
        item = session.scalar(
            select(IntakeItem)
            .where(IntakeItem.id == item_id)
            .options(selectinload(IntakeItem.candidates))
        )
        if item is None or (
            visibility == "removed" and item.archived_at is not None
        ):
            raise HTTPException(
                status_code=404,
                detail="Intake item not found in this investigation",
            )
    return serialize_intake(
        item,
        session=session,
    )


@router.get("/{investigation_id}/review-options")
def investigation_review_options(
    investigation_id: str,
    include_reusable: bool = Query(default=False),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Return merge targets, strict by default and explicit when cross-topic."""
    investigation = session.get(Investigation, investigation_id)
    if investigation is None:
        raise HTTPException(status_code=404, detail="Investigation not found")
    topic_event_ids = set(_topic_event_ids(session, investigation_id))
    event_query = select(Event).order_by(Event.start_at.desc(), Event.id.asc())
    if not include_reusable:
        event_query = event_query.where(Event.id.in_(topic_event_ids))
    events = list(session.scalars(event_query))

    topic_entity_ids = set(_topic_entity_ids(session, investigation_id))
    entity_query = select(Entity).order_by(Entity.name.asc(), Entity.id.asc())
    if not include_reusable:
        entity_query = entity_query.where(Entity.id.in_(topic_entity_ids))
    entities = list(session.scalars(entity_query))

    claim_query = select(Claim).order_by(Claim.created_at.desc(), Claim.id.asc())
    if not include_reusable:
        claim_query = claim_query.where(Claim.event_id.in_(topic_event_ids))
    claims = list(session.scalars(claim_query))

    event_options = []
    for event in events:
        owners = _object_memberships(session, "event", event.id)
        event_options.append(
            {
                "id": event.id,
                "title": event.title,
                "summary": event.summary,
                "start_at": iso(event.start_at),
                "in_scope": event.id in topic_event_ids,
                "reusable": event.id not in topic_event_ids,
                "investigations": owners,
                "unassigned": not owners,
            }
        )
    entity_options = []
    for entity in entities:
        owners = _entity_memberships(session, entity.id)
        entity_options.append(
            {
                "id": entity.id,
                "name": entity.name,
                "type": entity.entity_type,
                "in_scope": entity.id in topic_entity_ids,
                "reusable": entity.id not in topic_entity_ids,
                "investigations": owners,
                "unassigned": not owners,
            }
        )
    claim_options = []
    for claim in claims:
        owners = _object_memberships(session, "event", claim.event_id)
        claim_options.append(
            {
                "id": claim.id,
                "event_id": claim.event_id,
                "text": claim.text,
                "status": claim.status,
                "in_scope": claim.event_id in topic_event_ids,
                "reusable": claim.event_id not in topic_event_ids,
                "investigations": owners,
                "unassigned": not owners,
            }
        )
    return {
        "events": event_options,
        "entities": entity_options,
        "claims": claim_options,
        "scope": {
            "mode": "explicit-cross-investigation"
            if include_reusable
            else "investigation-only",
            "investigation": _membership_summary(investigation),
            "include_reusable": include_reusable,
        },
    }


@router.post("/{investigation_id}/intake/{item_id}/preview")
def preview_investigation_intake(
    investigation_id: str,
    item_id: str,
    request: IntakeConfirmationRequest,
    allow_cross_investigation: bool = Query(default=False),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    item = _require_scoped_intake(session, investigation_id, item_id)
    from .intake import build_confirmation_preview

    preview = build_confirmation_preview(session, item, request)
    scope_errors = _confirmation_scope_errors(
        session,
        investigation_id,
        request,
        allow_cross_investigation=allow_cross_investigation,
    )
    preview["errors"] = [*preview["errors"], *scope_errors]
    preview["confirmable"] = not preview["errors"]
    preview["scope"] = _review_scope_payload(
        session,
        investigation_id,
        request,
        allow_cross_investigation=allow_cross_investigation,
    )
    preview["semantic_preview"]["scope"] = preview["scope"]
    return preview


@router.post("/{investigation_id}/intake/{item_id}/confirm")
def confirm_investigation_intake(
    investigation_id: str,
    item_id: str,
    request: IntakeConfirmationRequest,
    allow_cross_investigation: bool = Query(default=False),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    item = _require_scoped_intake(session, investigation_id, item_id)
    scope_errors = _confirmation_scope_errors(
        session,
        investigation_id,
        request,
        allow_cross_investigation=allow_cross_investigation,
    )
    if scope_errors:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "The review selection conflicts with this investigation scope",
                "errors": scope_errors,
                "next_action": "Review the merge ownership and event time range before confirming.",
            },
        )
    from .intake import confirm_intake, serialize_intake

    def validate_locked_scope(locked_session: Session, locked_item: IntakeItem) -> None:
        if not _object_in_investigation(
            locked_session,
            investigation_id,
            "intake",
            locked_item.id,
        ):
            raise IntakeScopeError()
        locked_scope_errors = _confirmation_scope_errors(
            locked_session,
            investigation_id,
            request,
            allow_cross_investigation=allow_cross_investigation,
        )
        if locked_scope_errors:
            raise IntakeScopeError("; ".join(locked_scope_errors))
        locked_scope_payload = _review_scope_payload(
            locked_session,
            investigation_id,
            request,
            allow_cross_investigation=allow_cross_investigation,
        )
        locked_cross_scope_approval = allow_cross_investigation and (
            (
                locked_scope_payload["merge_event"]
                and not locked_scope_payload["merge_event"]["in_scope"]
            )
            or any(
                not entity["in_scope"]
                for entity in locked_scope_payload["merge_entities"]
            )
        )
        if locked_cross_scope_approval:
            record_action(
                locked_session,
                investigation_id,
                "review.cross_investigation_reuse_approved",
                actor=request.analyst,
                object_type="intake",
                object_id=locked_item.id,
                detail=locked_scope_payload,
            )

    try:
        item, result, created = confirm_intake(
            session,
            item,
            request,
            locked_validation_hook=validate_locked_scope,
        )
    except (ArchivedIntakeError, IntakeScopeError) as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    next_task = _next_review_task(
        session, investigation_id, exclude_intake_id=item.id
    )
    result = dict(result)
    result["final_event_id"] = item.final_event_id
    result["event_url"] = (
        f"/pldr-api/v1/events/{item.final_event_id}" if item.final_event_id else None
    )
    result["next_task"] = (
        serialize_task(next_task, session=session) if next_task is not None else None
    )
    return {
        "status": "confirmed",
        "created": created,
        "investigation_id": investigation_id,
        "final_event_id": item.final_event_id,
        "event_url": result["event_url"],
        "next_task": result["next_task"],
        "result": result,
        "intake_item": serialize_intake(item),
    }


@router.patch("/{investigation_id}")
def update_investigation(
    investigation_id: str,
    request: InvestigationUpdate,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    investigation = session.get(Investigation, investigation_id)
    if investigation is None:
        raise HTTPException(status_code=404, detail="Investigation not found")
    before = {
        "title": investigation.title,
        "question": investigation.question,
        "description": investigation.description,
        "tracking_mode": investigation.tracking_mode or "one_time",
        "event_start_at": iso(investigation.event_start_at),
        "event_end_at": iso(investigation.event_end_at),
        "settings": dict(investigation.settings_json or {}),
        "status": investigation.status,
    }
    if request.title is not None:
        investigation.title = request.title.strip()
    question = _question_from_request(request)
    if question is not None:
        investigation.question = question
    if request.description is not None:
        investigation.description = request.description.strip()
    if request.tracking_mode is not None:
        investigation.tracking_mode = request.tracking_mode
        if request.tracking_mode == "continuous":
            investigation.event_end_at = None
    if "event_start_at" in request.model_fields_set:
        investigation.event_start_at = request.event_start_at
    if "event_end_at" in request.model_fields_set and investigation.tracking_mode != "continuous":
        investigation.event_end_at = request.event_end_at
    comparable_start = investigation.event_start_at
    comparable_end = investigation.event_end_at
    if comparable_start is not None and comparable_start.tzinfo is None:
        comparable_start = comparable_start.replace(tzinfo=timezone.utc)
    if comparable_end is not None and comparable_end.tzinfo is None:
        comparable_end = comparable_end.replace(tzinfo=timezone.utc)
    if (
        comparable_start is not None
        and comparable_end is not None
        and comparable_end < comparable_start
    ):
        raise HTTPException(
            status_code=422,
            detail="event_end_at must not be earlier than event_start_at",
        )
    if request.settings is not None:
        investigation.settings_json = request.settings.model_dump()
    if request.status is not None:
        investigation.status = request.status
    investigation.updated_at = utcnow()
    record_action(
        session,
        investigation.id,
        "investigation.updated",
        actor=request.actor,
        object_type="investigation",
        object_id=investigation.id,
        detail={
            "before": before,
            "after": {
                "title": investigation.title,
                "question": investigation.question,
                "description": investigation.description,
                "tracking_mode": investigation.tracking_mode or "one_time",
                "event_start_at": iso(investigation.event_start_at),
                "event_end_at": iso(investigation.event_end_at),
                "settings": dict(investigation.settings_json or {}),
                "status": investigation.status,
            },
        },
    )
    session.commit()
    return serialize_investigation(session, investigation, include_detail=True)


@router.post("/{investigation_id}/links", status_code=status.HTTP_201_CREATED)
def add_investigation_link(
    investigation_id: str,
    request: InvestigationLinkRequest,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    if session.get(Investigation, investigation_id) is None:
        raise HTTPException(status_code=404, detail="Investigation not found")
    if not _object_exists(session, request.object_type, request.object_id):
        raise HTTPException(status_code=404, detail="Linked object not found")
    if request.object_type == "intake":
        session.rollback()
        try:
            lock_intake_for_mutation(
                session,
                request.object_id,
                action="linking it to an investigation",
            )
        except ArchivedIntakeError as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        link, created = link_object(
            session,
            investigation_id,
            request.object_type,
            request.object_id,
            role=request.role,
            actor=request.actor,
        )
    except ArchivedIntakeError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    task_payload = None
    if request.object_type == "intake":
        item = session.get(IntakeItem, request.object_id)
        assert item is not None
        try:
            task, task_created = ensure_review_task_for_intake(
                session,
                investigation_id,
                item,
                actor=request.actor,
                payload_extra={"manual_link": True},
            )
        except ArchivedIntakeError as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        task_payload = {
            "created": task_created,
            "task": serialize_task(task, session=session),
        }
    version_tasks_created = 0
    if request.object_type == "collection_target":
        version_tasks_created = attach_existing_collection_versions_to_investigation(
            session,
            investigation_id=investigation_id,
            target_id=request.object_id,
            actor=request.actor,
        )
    session.commit()
    return {
        "created": created,
        "link": serialize_link(link),
        "review_task": task_payload,
        "version_tasks_created": version_tasks_created,
    }


def _investigation_intake_task(
    session: Session, investigation_id: str, item_id: str
) -> ReviewTask | None:
    return session.scalar(
        select(ReviewTask)
        .where(
            ReviewTask.investigation_id == investigation_id,
            ReviewTask.intake_item_id == item_id,
        )
        .order_by(ReviewTask.created_at.asc())
        .limit(1)
    )


def _removed_intake_link_snapshot(
    session: Session, investigation_id: str, item_id: str
) -> tuple[str, dict[str, Any], str | None]:
    entry = _removed_intake_link_entry(session, investigation_id, item_id)
    detail = entry.detail_json if entry is not None else {}
    metadata = detail.get("metadata") if isinstance(detail, dict) else None
    return (
        str(detail.get("role") or "member") if isinstance(detail, dict) else "member",
        dict(metadata) if isinstance(metadata, dict) else {},
        str(detail.get("link_id"))
        if isinstance(detail, dict) and detail.get("link_id")
        else None,
    )


def _removed_intake_link_entry(
    session: Session, investigation_id: str, item_id: str
) -> DecisionLog | None:
    return session.scalar(
        select(DecisionLog)
        .where(
            DecisionLog.investigation_id == investigation_id,
            DecisionLog.action == "intake.removed_from_investigation",
            DecisionLog.object_type == "intake",
            DecisionLog.object_id == item_id,
        )
        .order_by(DecisionLog.created_at.desc(), DecisionLog.id.desc())
        .limit(1)
    )


@router.post("/{investigation_id}/intake/{item_id}/remove")
def remove_investigation_intake(
    investigation_id: str,
    item_id: str,
    request: ArchiveRequest = ArchiveRequest(),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    investigation = session.get(Investigation, investigation_id)
    if investigation is None:
        raise HTTPException(status_code=404, detail="Investigation not found")
    item = session.get(IntakeItem, item_id)
    task = _investigation_intake_task(session, investigation_id, item_id)
    link = session.scalar(
        select(InvestigationLink).where(
            InvestigationLink.investigation_id == investigation_id,
            InvestigationLink.object_type == "intake",
            InvestigationLink.object_id == item_id,
        )
    )
    if link is None:
        removed_entry = _removed_intake_link_entry(
            session, investigation_id, item_id
        )
        if item is None or (task is None and removed_entry is None):
            raise HTTPException(
                status_code=404,
                detail="Intake item not found in this investigation",
            )
    if item is None:
        raise HTTPException(
            status_code=404,
            detail="Intake item not found in this investigation",
        )
    session.rollback()
    try:
        item = lock_intake_for_mutation(
            session,
            item_id,
            action="removing it from an investigation",
        )
    except ArchivedIntakeError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    investigation = session.get(Investigation, investigation_id)
    task = _investigation_intake_task(session, investigation_id, item_id)
    link = session.scalar(
        select(InvestigationLink).where(
            InvestigationLink.investigation_id == investigation_id,
            InvestigationLink.object_type == "intake",
            InvestigationLink.object_id == item_id,
        )
    )
    if investigation is None:
        session.rollback()
        raise HTTPException(
            status_code=404,
            detail="Intake item not found in this investigation",
        )
    if link is None:
        removed_entry = _removed_intake_link_entry(
            session, investigation_id, item_id
        )
        session.rollback()
        if removed_entry is not None or task is not None:
            return {
                "status": "removed",
                "changed": False,
                "investigation_id": investigation_id,
                "intake_item_id": item_id,
            }
        raise HTTPException(
            status_code=404,
            detail="Intake item not found in this investigation",
        )
    if item.status == "confirmed":
        raise HTTPException(
            status_code=409,
            detail="A confirmed intake item cannot be removed from its investigation",
        )
    if task is not None and task.status in ACTIVE_TASK_STATUSES:
        raise HTTPException(
            status_code=409,
            detail="An intake item with an active review task cannot be removed",
        )
    link_detail = {
        "link_id": link.id,
        "role": link.role,
        "metadata": link.metadata_json or {},
        "reason": request.reason or "Removed from investigation",
        "status": item.status,
    }
    session.delete(link)
    investigation.updated_at = utcnow()
    record_action(
        session,
        investigation_id,
        "intake.removed_from_investigation",
        actor=request.analyst,
        object_type="intake",
        object_id=item_id,
        task_id=task.id if task is not None else None,
        detail=link_detail,
    )
    session.commit()
    return {
        "status": "removed",
        "changed": True,
        "investigation_id": investigation_id,
        "intake_item_id": item_id,
    }


@router.post("/{investigation_id}/intake/{item_id}/restore")
def restore_investigation_intake(
    investigation_id: str,
    item_id: str,
    request: ArchiveRequest = ArchiveRequest(),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    investigation = session.get(Investigation, investigation_id)
    if investigation is None:
        raise HTTPException(status_code=404, detail="Investigation not found")
    item = session.get(IntakeItem, item_id)
    task = _investigation_intake_task(session, investigation_id, item_id)
    removed_entry = _removed_intake_link_entry(
        session, investigation_id, item_id
    )
    if item is None:
        raise HTTPException(
            status_code=404,
            detail="Removed intake relationship not found",
        )
    if item.archived_at is not None:
        raise HTTPException(
            status_code=409,
            detail="Restore the intake item globally before restoring its investigation relationship",
        )
    if item.status == "confirmed":
        raise HTTPException(
            status_code=409,
            detail="A confirmed intake item cannot be restored as an unconfirmed review task",
        )
    existing = session.scalar(
        select(InvestigationLink).where(
            InvestigationLink.investigation_id == investigation_id,
            InvestigationLink.object_type == "intake",
            InvestigationLink.object_id == item_id,
        )
    )
    if existing is None and removed_entry is None:
        # A removal log is the durable membership proof when legacy or manual
        # links never had a ReviewTask. Do not treat a guessed global item ID as
        # a removed relationship.
        raise HTTPException(
            status_code=404,
            detail="Removed intake relationship not found",
        )
    session.rollback()
    try:
        item = lock_intake_for_mutation(
            session,
            item_id,
            action="restoring it to an investigation",
        )
    except ArchivedIntakeError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    investigation = session.get(Investigation, investigation_id)
    task = _investigation_intake_task(session, investigation_id, item_id)
    if item.status == "confirmed":
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="A confirmed intake item cannot be restored as an unconfirmed review task",
        )
    existing = session.scalar(
        select(InvestigationLink).where(
            InvestigationLink.investigation_id == investigation_id,
            InvestigationLink.object_type == "intake",
            InvestigationLink.object_id == item_id,
        )
    )
    if existing is not None:
        if task is not None:
            sync_review_task_with_intake(
                session,
                task,
                item,
                actor=request.analyst,
            )
        payload = {
            "status": "active",
            "changed": False,
            "investigation_id": investigation_id,
            "intake_item_id": item_id,
            "link": serialize_link(existing),
            "task": serialize_task(task, session=session) if task is not None else None,
        }
        session.commit()
        return payload
    removed_entry = _removed_intake_link_entry(
        session, investigation_id, item_id
    )
    if investigation is None or removed_entry is None:
        session.rollback()
        raise HTTPException(
            status_code=404,
            detail="Removed intake relationship not found",
        )
    role, metadata, removed_link_id = _removed_intake_link_snapshot(
        session, investigation_id, item_id
    )
    link = InvestigationLink(
        id=new_link_id(),
        investigation_id=investigation_id,
        object_type="intake",
        object_id=item_id,
        role=role,
        metadata_json=metadata,
        created_at=utcnow(),
    )
    session.add(link)
    investigation.updated_at = utcnow()
    session.flush()
    if task is not None:
        sync_review_task_with_intake(
            session,
            task,
            item,
            actor=request.analyst,
        )
    record_action(
        session,
        investigation_id,
        "intake.restored_to_investigation",
        actor=request.analyst,
        object_type="intake",
        object_id=item_id,
        task_id=task.id if task is not None else None,
        detail={
            "reason": request.reason or "Restored to investigation",
            "restored_from_link_id": removed_link_id,
            "role": role,
            "metadata": metadata,
        },
    )
    session.commit()
    return {
        "status": "active",
        "changed": True,
        "investigation_id": investigation_id,
        "intake_item_id": item_id,
        "link": serialize_link(link),
        "task": serialize_task(task, session=session) if task is not None else None,
    }


@router.get("/{investigation_id}/tasks")
def list_investigation_tasks(
    investigation_id: str,
    status_value: str | None = Query(default=None, alias="status"),
    visibility: Literal["active", "removed", "all"] = Query(default="active"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    if session.get(Investigation, investigation_id) is None:
        raise HTTPException(status_code=404, detail="Investigation not found")
    query = select(ReviewTask).where(ReviewTask.investigation_id == investigation_id)
    if visibility == "active":
        query = query.where(_task_has_current_intake_link(), _task_intake_is_visible())
    elif visibility == "removed":
        query = query.where(~_task_has_current_intake_link(), _task_intake_is_visible())
    if status_value:
        query = query.where(ReviewTask.status == status_value)
    total = int(
        session.scalar(select(func.count()).select_from(query.subquery())) or 0
    )
    tasks = list(
        session.scalars(
            query.order_by(ReviewTask.queued_at.desc(), ReviewTask.id.desc())
            .offset(offset)
            .limit(limit)
        )
    )
    return {
        "items": [serialize_task(task, session=session) for task in tasks],
        "count": total,
        "offset": offset,
        "limit": limit,
        "visibility": visibility,
    }


@router.get("/{investigation_id}/activity")
def list_investigation_activity(
    investigation_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    if session.get(Investigation, investigation_id) is None:
        raise HTTPException(status_code=404, detail="Investigation not found")
    total = int(
        session.scalar(
            select(func.count())
            .select_from(DecisionLog)
            .where(DecisionLog.investigation_id == investigation_id)
        )
        or 0
    )
    entries = list(
        session.scalars(
            select(DecisionLog)
            .where(DecisionLog.investigation_id == investigation_id)
            .order_by(DecisionLog.created_at.desc(), DecisionLog.id.desc())
            .offset(offset)
            .limit(limit)
        )
    )
    return {
        "items": [serialize_activity(entry) for entry in entries],
        "count": total,
        "offset": offset,
        "limit": limit,
    }
