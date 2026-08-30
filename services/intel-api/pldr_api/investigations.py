from __future__ import annotations

import hashlib
import socket
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from .database import SessionLocal, get_session
from .extraction import canonicalize_url, content_hash, extract_page
from .importers import fetch_public_text_response
from .intake import generate_candidates
from .models import (
    CollectionRun,
    CollectionTarget,
    DecisionLog,
    Event,
    EventDocument,
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
from .repository import serialize_event_card
from .schemas import (
    InvestigationCreate,
    InvestigationLinkRequest,
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


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
        fallback = (
            item.status == "candidate_ready"
            and item.candidate_mode == "fallback-after-error"
        )
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
                "model_fallback"
                if fallback
                else "legacy_intake_failed"
                if status_value == "failed"
                else None
            ),
            error_message=(
                item.candidate_error
                if fallback
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
    for link in links:
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
    for link in links:
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
    payload["links"] = [serialize_link(link) for link in links]
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


def serialize_task(task: ReviewTask) -> dict[str, Any]:
    return {
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
        "error": (
            {"class": task.error_class, "message": task.error_message}
            if task.error_class or task.error_message
            else None
        ),
        "fallback_used": task.error_class == "model_fallback",
        "retryable": task.status == "failed"
        or (task.status == "ready" and task.error_class == "model_fallback"),
        "intake_item_id": task.intake_item_id,
        "selection_id": task.selection_id,
        "payload": task.payload_json or {},
        "payload_json": task.payload_json or {},
        "created_at": iso(task.created_at),
        "updated_at": iso(task.updated_at),
    }


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
        title=None,
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
        "tasks": [serialize_task(task) for task in tasks],
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
                "model_fallback"
                if item.status == "candidate_ready"
                and item.candidate_mode == "fallback-after-error"
                else "intake_failed"
                if terminal_status == "failed"
                else None
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
    fallback = (
        item.status == "candidate_ready"
        and item.candidate_mode == "fallback-after-error"
    )
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
            "model_fallback"
            if fallback
            else "intake_failed"
            if task_status == "failed"
            else None
        ),
        error_message=(
            item.candidate_error
            if fallback
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
        if item is None:
            continue
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
        created_count += int(created)
    return created_count


def link_legacy_search_selection(
    session: Session,
    *,
    query_run_id: str,
    intake_item_id: str,
    actor: str = "system:legacy-api",
) -> None:
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
    item = session.get(IntakeItem, intake_item_id)
    if item is not None:
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
        task_id = session.scalar(
            select(ReviewTask.id)
            .where(ReviewTask.status == "queued")
            .order_by(ReviewTask.queued_at.asc(), ReviewTask.id.asc())
            .limit(1)
        )
        if task_id is None:
            return None
        claimed = session.execute(
            update(ReviewTask)
            .where(ReviewTask.id == task_id, ReviewTask.status == "queued")
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


def _populate_intake_from_fetch(item: IntakeItem, fetched: Any) -> None:
    if not fetched.text or not fetched.text.strip():
        raise ValueError("Fetched page is empty")
    page = extract_page(fetched.text)
    if len(page.body) < 40:
        raise ValueError("Extracted page body is too short")
    item.status = "parsed"
    item.error = None
    item.source_url = item.source_url or fetched.resolved_url
    item.canonical_url = canonicalize_url(fetched.resolved_url)
    item.source_description = item.source_description or (
        urlparse(fetched.resolved_url).hostname or "Unknown source"
    )
    item.title = page.title or None
    item.media_type = fetched.media_type
    item.size_bytes = fetched.size_bytes
    item.raw_snapshot = fetched.text
    item.raw_hash = hashlib.sha256(fetched.text.encode("utf-8")).hexdigest()
    item.extracted_snapshot = page.body
    item.extracted_hash = content_hash(page.body)
    item.candidate_error = None
    item.candidate_relations = []
    review = dict(item.review or {})
    review["material"] = {
        **(review.get("material") or {}),
        "resolved_url": fetched.resolved_url,
        "fetched_at": iso(utcnow()),
        "http_status": fetched.status_code,
    }
    item.review = review
    item.updated_at = utcnow()


async def execute_claimed_review_task(task_id: str) -> ReviewTask:
    """Process exactly one task; all failures are persisted and never escape the batch."""
    with SessionLocal() as session:
        task = session.get(ReviewTask, task_id)
        if task is None:
            raise ValueError("Review task not found")
        if task.status not in {"fetching", "generating"}:
            raise ValueError(f"Review task must be claimed, got {task.status}")
        actor = f"collector:{task.lease_owner or 'unknown'}"
        try:
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
            item = session.get(IntakeItem, task.intake_item_id) if task.intake_item_id else None
            if item is None:
                raise ValueError("Queued intake item is missing")
            if task.selection_id and selection is None:
                raise ValueError("Queued search selection is missing")

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

            if result is not None and (
                not item.raw_snapshot or item.status in {"queued", "failed"}
            ):
                fetched = await fetch_public_text_response(result.original_url)
                _populate_intake_from_fetch(item, fetched)
            elif result is None and not item.extracted_snapshot.strip():
                raise ValueError("Intake has no persisted extracted content to process")

            task.status = "generating"
            task.updated_at = utcnow()
            item.status = "parsed"
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
            task = session.get(ReviewTask, task_id)
            item = session.get(IntakeItem, task.intake_item_id) if task else None
            selection = (
                session.get(SearchSelection, task.selection_id)
                if task is not None and task.selection_id
                else None
            )
            if task is None or item is None:
                raise RuntimeError("Review task disappeared before candidate generation")

            item = await generate_candidates(session, item)
            fallback_error: str | None = None
            if item.status == "generation_failed":
                fallback_error = item.candidate_error or "Configured model candidate generation failed"
                from .intake import generate_deterministic_candidates

                item = generate_deterministic_candidates(
                    session, item, model_error=fallback_error
                )
            task = session.get(ReviewTask, task_id)
            selection = (
                session.get(SearchSelection, task.selection_id)
                if task is not None and task.selection_id
                else None
            )
            if task is None:
                raise RuntimeError("Review task disappeared after candidate generation")
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
        except Exception as exc:
            session.rollback()
            task = session.get(ReviewTask, task_id)
            if task is None:
                raise
            item = session.get(IntakeItem, task.intake_item_id) if task.intake_item_id else None
            selection = session.get(SearchSelection, task.selection_id) if task.selection_id else None
            if item is not None:
                item.status = "failed"
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
    payload["force_ai_retry"] = fallback_retry
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
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    query = select(Investigation).order_by(
        Investigation.updated_at.desc(), Investigation.id.asc()
    )
    if status_value:
        query = query.where(Investigation.status == status_value)
    items = list(session.scalars(query.limit(limit)))
    return {
        "items": [serialize_investigation(session, item) for item in items],
        "count": len(items),
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
            )
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
    return serialize_task(task)


@router.get("/{investigation_id}")
def get_investigation(
    investigation_id: str,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    investigation = session.get(Investigation, investigation_id)
    if investigation is None:
        raise HTTPException(status_code=404, detail="Investigation not found")
    return serialize_investigation(session, investigation, include_detail=True)


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
        "status": investigation.status,
    }
    if request.title is not None:
        investigation.title = request.title.strip()
    question = _question_from_request(request)
    if question is not None:
        investigation.question = question
    if request.description is not None:
        investigation.description = request.description.strip()
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
    link, created = link_object(
        session,
        investigation_id,
        request.object_type,
        request.object_id,
        role=request.role,
        actor=request.actor,
    )
    task_payload = None
    if request.object_type == "intake":
        item = session.get(IntakeItem, request.object_id)
        assert item is not None
        task, task_created = ensure_review_task_for_intake(
            session,
            investigation_id,
            item,
            actor=request.actor,
            payload_extra={"manual_link": True},
        )
        task_payload = {"created": task_created, "task": serialize_task(task)}
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


@router.get("/{investigation_id}/tasks")
def list_investigation_tasks(
    investigation_id: str,
    status_value: str | None = Query(default=None, alias="status"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    if session.get(Investigation, investigation_id) is None:
        raise HTTPException(status_code=404, detail="Investigation not found")
    query = select(ReviewTask).where(ReviewTask.investigation_id == investigation_id)
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
        "items": [serialize_task(task) for task in tasks],
        "count": total,
        "offset": offset,
        "limit": limit,
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
