from __future__ import annotations

import difflib
import hashlib
import re
import socket
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import case, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .database import SessionLocal
from .extraction import canonicalize_url, content_hash, extract_page
from .importers import (
    FetchedPublicText,
    ResponseTooLargeError,
    UnsupportedContentEncodingError,
    UnsupportedContentTypeError,
    fetch_public_text_response,
)
from .intake import generate_candidates, submit_web_intake
from .models import CollectionRun, CollectionTarget, IntakeItem
from .security import UnsafeUrlError


RUN_STATUSES = {"queued", "running", "succeeded", "failed"}
VERSION_OUTCOMES = {"baseline", "changed"}
DEFAULT_LEASE_SECONDS = 180
MIN_OVERDUE_GRACE_SECONDS = 60
MAX_OVERDUE_GRACE_SECONDS = 300
MAX_EXACT_DIFF_WORDS = 4_000
MAX_DIFF_SEGMENTS = 400
MAX_DIFF_TEXT_CHARS = 120_000
MAX_UNIFIED_INPUT_LINES = 2_000
MAX_UNIFIED_DIFF_LINES = 2_000


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def new_target_id() -> str:
    return f"col_tgt_{uuid.uuid4().hex[:16]}"


def new_run_id() -> str:
    return f"col_run_{uuid.uuid4().hex[:18]}"


def worker_identity() -> str:
    return f"{socket.gethostname()}:{uuid.uuid4().hex[:10]}"


def serialize_run(run: CollectionRun) -> dict[str, Any]:
    intake = run.current_intake_item
    return {
        "id": run.id,
        "target_id": run.target_id,
        "status": run.status,
        "outcome": run.outcome,
        "trigger": run.trigger,
        "retry_of_run_id": run.retry_of_run_id,
        "attempt_number": run.attempt_number,
        "queued_at": iso(run.queued_at),
        "started_at": iso(run.started_at),
        "completed_at": iso(run.completed_at),
        "lease": {
            "owner": run.lease_owner,
            "expires_at": iso(run.lease_expires_at),
            "recoveries": run.lease_recoveries,
        },
        "duration_ms": run.duration_ms,
        "error": (
            {"class": run.error_class, "message": run.error_message}
            if run.error_class or run.error_message
            else None
        ),
        "response": {
            "resolved_url": run.resolved_url,
            "http_status": run.http_status,
            "media_type": run.media_type,
            "size_bytes": run.size_bytes,
        },
        "content": {
            "raw_hash": run.raw_hash,
            "body_hash": run.body_hash,
        },
        "version_number": run.version_number,
        "intake_chain": {
            "previous": run.previous_intake_item_id,
            "current": run.current_intake_item_id,
        },
        "intake": (
            {
                "id": intake.id,
                "status": intake.status,
                "disposition": intake.disposition,
                "final_object_ids": {
                    "event": intake.final_event_id,
                    "document": intake.final_document_id,
                    "snapshot": intake.final_snapshot_id,
                },
            }
            if intake is not None
            else None
        ),
    }


def _latest_run(session: Session, target_id: str) -> CollectionRun | None:
    return session.scalar(
        select(CollectionRun)
        .where(CollectionRun.target_id == target_id)
        .order_by(CollectionRun.queued_at.desc(), CollectionRun.id.desc())
        .limit(1)
    )


def _version_count(session: Session, target_id: str) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(CollectionRun)
            .where(
                CollectionRun.target_id == target_id,
                CollectionRun.status == "succeeded",
                CollectionRun.outcome.in_(VERSION_OUTCOMES),
            )
        )
        or 0
    )


def _run_count(session: Session, target_id: str) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(CollectionRun)
            .where(CollectionRun.target_id == target_id)
        )
        or 0
    )


def target_is_overdue(target: CollectionTarget, *, now: datetime | None = None) -> bool:
    if not target.enabled or target.next_run_at is None:
        return False
    now = now or utcnow()
    grace_seconds = min(
        MAX_OVERDUE_GRACE_SECONDS,
        max(MIN_OVERDUE_GRACE_SECONDS, target.interval_seconds // 10),
    )
    return _as_aware(target.next_run_at) < now - timedelta(seconds=grace_seconds)


def serialize_target(
    session: Session,
    target: CollectionTarget,
    *,
    include_runs: bool = False,
    run_limit: int = 50,
    version_limit: int = 100,
) -> dict[str, Any]:
    latest = _latest_run(session, target.id)
    overdue = target_is_overdue(target)
    effective_health = (
        "paused"
        if not target.enabled
        else target.health
        if target.health in {"error", "degraded"}
        else "stale"
        if overdue
        else target.health
    )
    payload: dict[str, Any] = {
        "id": target.id,
        "name": target.name,
        "url": target.url,
        "language": target.language,
        "interval_seconds": target.interval_seconds,
        "enabled": target.enabled,
        "next_run_at": iso(target.next_run_at),
        "health": effective_health,
        "recorded_health": target.health,
        "overdue": overdue,
        "consecutive_failures": target.consecutive_failures,
        "last_run_at": iso(target.last_run_at),
        "last_success_at": iso(target.last_success_at),
        "last_error": target.last_error,
        "version_count": _version_count(session, target.id),
        "run_count": _run_count(session, target.id),
        "created_at": iso(target.created_at),
        "updated_at": iso(target.updated_at),
        "latest_run": serialize_run(latest) if latest else None,
    }
    if include_runs:
        runs = list(
            session.scalars(
                select(CollectionRun)
                .where(CollectionRun.target_id == target.id)
                .order_by(CollectionRun.queued_at.desc(), CollectionRun.id.desc())
                .limit(run_limit)
            )
        )
        # Versions are a durable product history, not a projection of the recent run
        # window. A frequently checked but unchanged target must not lose V1 merely
        # because more than ``run_limit`` executions have happened since it changed.
        version_runs = list(
            session.scalars(
                select(CollectionRun)
                .where(
                    CollectionRun.target_id == target.id,
                    CollectionRun.status == "succeeded",
                    CollectionRun.outcome.in_(VERSION_OUTCOMES),
                )
                .order_by(
                    CollectionRun.version_number.desc(),
                    CollectionRun.completed_at.desc(),
                    CollectionRun.id.desc(),
                )
                .limit(version_limit)
            )
        )
        payload["runs"] = [serialize_run(run) for run in runs]
        payload["runs_returned"] = len(runs)
        payload["runs_truncated"] = payload["run_count"] > len(runs)
        payload["versions"] = [serialize_run(run) for run in version_runs]
        payload["versions_returned"] = len(version_runs)
        payload["versions_truncated"] = payload["version_count"] > len(version_runs)
    return payload


def list_version_runs(
    session: Session,
    target_id: str,
    *,
    offset: int = 0,
    limit: int = 100,
) -> list[CollectionRun]:
    """Return a stable page of captured versions independent from run retention views."""
    return list(
        session.scalars(
            select(CollectionRun)
            .where(
                CollectionRun.target_id == target_id,
                CollectionRun.status == "succeeded",
                CollectionRun.outcome.in_(VERSION_OUTCOMES),
            )
            .order_by(
                CollectionRun.version_number.desc(),
                CollectionRun.completed_at.desc(),
                CollectionRun.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        )
    )


def list_target_runs(
    session: Session,
    target_id: str,
    *,
    offset: int = 0,
    limit: int = 50,
) -> list[CollectionRun]:
    return list(
        session.scalars(
            select(CollectionRun)
            .where(CollectionRun.target_id == target_id)
            .order_by(CollectionRun.queued_at.desc(), CollectionRun.id.desc())
            .offset(offset)
            .limit(limit)
        )
    )


def enqueue_target_run(
    session: Session,
    target: CollectionTarget,
    *,
    trigger: str,
    retry_of: CollectionRun | None = None,
    now: datetime | None = None,
    deduplicate: bool = True,
) -> tuple[CollectionRun, bool]:
    """Persist a run before execution. Returns ``(run, created)``."""
    now = now or utcnow()
    if deduplicate:
        pending = session.scalar(
            select(CollectionRun)
            .where(
                CollectionRun.target_id == target.id,
                CollectionRun.status.in_(["queued", "running"]),
            )
            .order_by(CollectionRun.queued_at.asc())
        )
        if pending is not None:
            return pending, False
    attempt = (retry_of.attempt_number + 1) if retry_of else 1
    run = CollectionRun(
        id=new_run_id(),
        target_id=target.id,
        status="queued",
        active_key=target.id,
        trigger=trigger,
        retry_of_run_id=retry_of.id if retry_of else None,
        attempt_number=attempt,
        queued_at=now,
    )
    session.add(run)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        pending = session.scalar(
            select(CollectionRun)
            .where(
                CollectionRun.target_id == target.id,
                CollectionRun.status.in_(["queued", "running"]),
            )
            .order_by(CollectionRun.queued_at.asc())
        )
        if pending is None:
            raise
        return pending, False
    return run, True


def recover_expired_leases(session: Session, *, now: datetime | None = None) -> int:
    """Return abandoned running rows to the durable queue."""
    now = now or utcnow()
    result = session.execute(
        update(CollectionRun)
        .where(
            CollectionRun.status == "running",
            CollectionRun.lease_expires_at.is_not(None),
            CollectionRun.lease_expires_at <= now,
        )
        .values(
            status="queued",
            started_at=None,
            lease_owner=None,
            lease_expires_at=None,
            lease_recoveries=CollectionRun.lease_recoveries + 1,
        )
    )
    session.commit()
    return int(result.rowcount or 0)


def enqueue_due_runs(session: Session, *, now: datetime | None = None) -> int:
    """Atomically reserve due targets and enqueue at most one run for each."""
    now = now or utcnow()
    targets = list(
        session.scalars(
            select(CollectionTarget)
            .where(
                CollectionTarget.enabled.is_(True),
                CollectionTarget.next_run_at.is_not(None),
                CollectionTarget.next_run_at <= now,
            )
            .order_by(CollectionTarget.next_run_at.asc(), CollectionTarget.id.asc())
        )
    )
    created = 0
    for target in targets:
        due_value = target.next_run_at
        if due_value is None:
            continue
        reservation = session.execute(
            update(CollectionTarget)
            .where(
                CollectionTarget.id == target.id,
                CollectionTarget.enabled.is_(True),
                CollectionTarget.next_run_at == due_value,
            )
            .values(next_run_at=now + timedelta(seconds=target.interval_seconds))
        )
        if not reservation.rowcount:
            continue
        pending = session.scalar(
            select(CollectionRun.id).where(
                CollectionRun.target_id == target.id,
                CollectionRun.status.in_(["queued", "running"]),
            )
        )
        if pending is None:
            try:
                with session.begin_nested():
                    session.add(
                        CollectionRun(
                            id=new_run_id(),
                            target_id=target.id,
                            status="queued",
                            active_key=target.id,
                            trigger="scheduled",
                            attempt_number=1,
                            queued_at=now,
                        )
                    )
                    session.flush()
                created += 1
            except IntegrityError:
                # A concurrent manual request won the active slot after our read.
                # The due reservation is still valid and no duplicate run is needed.
                pass
    session.commit()
    return created


def claim_next_run(
    session: Session,
    *,
    worker_id: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    now: datetime | None = None,
) -> CollectionRun | None:
    """Claim one queued run conditionally; the supported runtime still uses one worker."""
    now = now or utcnow()
    recover_expired_leases(session, now=now)
    enqueue_due_runs(session, now=now)
    while True:
        run_id = session.scalar(
            select(CollectionRun.id)
            .join(CollectionTarget, CollectionTarget.id == CollectionRun.target_id)
            .where(
                CollectionRun.status == "queued",
                CollectionTarget.enabled.is_(True),
            )
            .order_by(CollectionRun.queued_at.asc(), CollectionRun.id.asc())
            .limit(1)
        )
        if run_id is None:
            return None
        claimed = session.execute(
            update(CollectionRun)
            .where(CollectionRun.id == run_id, CollectionRun.status == "queued")
            .values(
                status="running",
                started_at=now,
                lease_owner=worker_id,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
            )
        )
        session.commit()
        if claimed.rowcount:
            return session.get(CollectionRun, run_id)


def _latest_version_run(
    session: Session, target_id: str, *, exclude_run_id: str
) -> CollectionRun | None:
    return session.scalar(
        select(CollectionRun)
        .where(
            CollectionRun.target_id == target_id,
            CollectionRun.id != exclude_run_id,
            CollectionRun.status == "succeeded",
            CollectionRun.outcome.in_(VERSION_OUTCOMES),
            CollectionRun.current_intake_item_id.is_not(None),
        )
        .order_by(CollectionRun.version_number.desc(), CollectionRun.completed_at.desc())
        .limit(1)
    )


def classify_collection_error(exc: Exception) -> str:
    if isinstance(exc, UnsafeUrlError):
        return "unsafe_url"
    if isinstance(exc, ResponseTooLargeError):
        return "response_too_large"
    if isinstance(exc, UnsupportedContentTypeError):
        return "unsupported_content_type"
    if isinstance(exc, UnsupportedContentEncodingError):
        return "unsupported_content_encoding"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        return "http_status"
    if isinstance(exc, httpx.NetworkError):
        return "network"
    message = str(exc).lower()
    if "extracted page body" in message or "page is empty" in message:
        return "extraction"
    return "internal"


def _recoverable_intake(
    session: Session,
    run: CollectionRun,
    target: CollectionTarget,
    *,
    resolved_url: str,
    raw_hash: str | None = None,
    body_hash: str | None = None,
) -> IntakeItem | None:
    """Find the one intake a crashed execution may have committed before linking its run.

    ``submit_web_intake`` intentionally commits the immutable material before candidate
    generation. A process can therefore die in the narrow interval before the collection
    run stores its foreign key. The run id in ``review.collection`` is authoritative when
    present; otherwise an unclaimed collection intake created after this run was queued is
    safe to adopt because collection runs for a target are serialized by the DB lease.
    """
    candidates = list(
        session.scalars(
            select(IntakeItem)
            .where(
                IntakeItem.input_type == "collection",
                IntakeItem.source_description == target.name,
                IntakeItem.status != "failed",
            )
            .order_by(IntakeItem.created_at.desc())
            .limit(100)
        )
    )
    for item in candidates:
        trace = (item.review or {}).get("collection") or {}
        if trace.get("run_id") == run.id:
            return item
    expected_raw_hash = raw_hash or run.raw_hash
    expected_body_hash = body_hash or run.body_hash
    if not expected_raw_hash or not expected_body_hash:
        return None
    queued_at = _as_aware(run.queued_at)
    accepted_urls = {canonicalize_url(target.url), canonicalize_url(resolved_url)}
    for item in candidates:
        trace = (item.review or {}).get("collection")
        if trace:
            continue
        if _as_aware(item.created_at) < queued_at:
            continue
        if item.raw_hash != expected_raw_hash or item.extracted_hash != expected_body_hash:
            continue
        item_url = item.canonical_url or item.source_url
        if item_url and canonicalize_url(item_url) in accepted_urls:
            return item
    return None


async def execute_claimed_run(run_id: str) -> CollectionRun:
    """Fetch one claimed target and atomically finish its durable run record."""
    started_clock = time.monotonic()
    created_item_id: str | None = None
    with SessionLocal() as session:
        run = session.get(CollectionRun, run_id)
        if run is None:
            raise ValueError("Collection run not found")
        if run.status != "running":
            raise ValueError(f"Collection run must be running, got {run.status}")
        target = session.get(CollectionTarget, run.target_id)
        if target is None:
            raise ValueError("Collection target not found")
        try:
            recovered_item = _recoverable_intake(
                session,
                run,
                target,
                resolved_url=run.resolved_url or target.url,
            )
            if recovered_item is not None:
                # Recovery must not depend on the source still being reachable: the exact
                # immutable bytes were already committed before the worker crashed.
                recovered_raw = recovered_item.raw_snapshot
                fetched = FetchedPublicText(
                    resolved_url=run.resolved_url or recovered_item.canonical_url or target.url,
                    text=recovered_raw,
                    status_code=run.http_status or 200,
                    media_type=run.media_type or recovered_item.media_type or "text/html",
                    size_bytes=len(recovered_raw.encode("utf-8")),
                )
                page = extract_page(recovered_raw)
                raw_hash = recovered_item.raw_hash
                body_hash = recovered_item.extracted_hash
            else:
                fetched = await fetch_public_text_response(target.url)
                if not fetched.text.strip():
                    raise ValueError("Fetched page is empty")
                page = extract_page(fetched.text)
                if len(page.body) < 40:
                    raise ValueError("Extracted page body is too short")
                raw_hash = hashlib.sha256(fetched.text.encode("utf-8")).hexdigest()
                body_hash = content_hash(page.body)
                # Persist the fetched identity before submit_web_intake performs its own
                # commits. A lease replay can then adopt that one IntakeItem by hashes.
                run.resolved_url = canonicalize_url(fetched.resolved_url)
                run.http_status = fetched.status_code
                run.media_type = fetched.media_type
                run.size_bytes = fetched.size_bytes
                run.raw_hash = raw_hash
                run.body_hash = body_hash
                session.commit()
                run = session.get(CollectionRun, run_id)
                target = session.get(CollectionTarget, run.target_id) if run else None
                if run is None or target is None:
                    raise RuntimeError("Collection run disappeared after fetch checkpoint")
                recovered_item = _recoverable_intake(
                    session,
                    run,
                    target,
                    resolved_url=fetched.resolved_url,
                    raw_hash=raw_hash,
                    body_hash=body_hash,
                )
            if recovered_item is not None:
                # The first execution already persisted the exact material. Finish that
                # same run instead of fetching into a second IntakeItem after restart.
                raw_hash = recovered_item.raw_hash
                body_hash = recovered_item.extracted_hash
                if recovered_item.status == "parsed":
                    recovered_item = await generate_candidates(session, recovered_item)
                    run = session.get(CollectionRun, run_id)
                    target = session.get(CollectionTarget, run.target_id) if run else None
                    if run is None or target is None:
                        raise RuntimeError("Collection run disappeared during intake recovery")

            previous_run = _latest_version_run(session, target.id, exclude_run_id=run.id)
            previous_item = (
                session.get(IntakeItem, previous_run.current_intake_item_id)
                if previous_run and previous_run.current_intake_item_id
                else None
            )

            run.resolved_url = canonicalize_url(
                (
                    ((recovered_item.review or {}).get("collection") or {}).get("resolved_url")
                    if recovered_item
                    else None
                )
                or run.resolved_url
                or fetched.resolved_url
            )
            run.http_status = fetched.status_code
            run.media_type = (
                recovered_item.media_type if recovered_item and recovered_item.media_type else fetched.media_type
            )
            run.size_bytes = (
                len(recovered_item.raw_snapshot.encode("utf-8"))
                if recovered_item is not None
                else fetched.size_bytes
            )
            run.raw_hash = raw_hash
            run.body_hash = body_hash
            run.previous_intake_item_id = previous_item.id if previous_item else None

            if previous_item is not None and previous_item.extracted_hash == body_hash:
                run.outcome = "unchanged"
                run.version_number = previous_run.version_number
                run.current_intake_item_id = previous_item.id
            else:
                version_number = (previous_run.version_number or 0) + 1 if previous_run else 1
                outcome = "changed" if previous_item else "baseline"
                item = recovered_item
                if item is None:
                    item = await submit_web_intake(
                        session,
                        target.url,
                        target.name,
                        page.title,
                        fetched.text,
                        target.language,
                        input_type="collection",
                    )
                    created_item_id = item.id
                    if item.status == "failed":
                        error = item.error or "Collection intake creation failed"
                        session.delete(item)
                        session.commit()
                        created_item_id = None
                        raise ValueError(error)
                trace = {
                    "target_id": target.id,
                    "run_id": run.id,
                    "trigger": run.trigger,
                    "version_number": version_number,
                    "outcome": outcome,
                    "previous_intake_item_id": previous_item.id if previous_item else None,
                    "current_intake_item_id": item.id,
                    "requested_url": target.url,
                    "resolved_url": fetched.resolved_url,
                    "fetched_at": (
                        ((item.review or {}).get("material") or {}).get("fetched_at")
                        or iso(utcnow())
                    ),
                    "raw_hash": raw_hash,
                    "body_hash": body_hash,
                }
                existing_review = item.review or {}
                item.review = {
                    **existing_review,
                    "material": {
                        **(existing_review.get("material") or {}),
                        "resolved_url": fetched.resolved_url,
                    },
                    "collection": trace,
                }
                session.add(item)
                session.commit()
                run = session.get(CollectionRun, run_id)
                target = session.get(CollectionTarget, run.target_id) if run else None
                if run is None or target is None:
                    raise RuntimeError("Collection run disappeared during intake creation")
                run.outcome = outcome
                run.version_number = version_number
                run.previous_intake_item_id = previous_item.id if previous_item else None
                run.current_intake_item_id = item.id
                from .investigations import attach_collection_intake_to_investigations

                attach_collection_intake_to_investigations(
                    session,
                    target_id=target.id,
                    item=item,
                    run_id=run.id,
                    outcome=outcome,
                )

            finished = utcnow()
            run.status = "succeeded"
            run.active_key = None
            run.completed_at = finished
            run.duration_ms = max(0, int((time.monotonic() - started_clock) * 1000))
            run.lease_owner = None
            run.lease_expires_at = None
            run.error_class = None
            run.error_message = None
            # Evaluate enabled/paused at the final SQL write, not from the ORM object
            # loaded before a long fetch. This keeps a concurrent pause/resume from
            # leaving enabled=true with recorded health=paused (or the reverse).
            session.execute(
                update(CollectionTarget)
                .where(CollectionTarget.id == target.id)
                .values(
                    health=case(
                        (CollectionTarget.enabled.is_(True), "healthy"),
                        else_="paused",
                    ),
                    consecutive_failures=0,
                    last_run_at=finished,
                    last_success_at=finished,
                    last_error=None,
                    next_run_at=case(
                        (
                            CollectionTarget.enabled.is_(True)
                            & CollectionTarget.next_run_at.is_(None),
                            finished + timedelta(seconds=target.interval_seconds),
                        ),
                        else_=CollectionTarget.next_run_at,
                    ),
                    updated_at=finished,
                )
                .execution_options(synchronize_session=False)
            )
            session.commit()
            return run
        except Exception as exc:
            session.rollback()
            run = session.get(CollectionRun, run_id)
            target = session.get(CollectionTarget, run.target_id) if run else None
            if run is None or target is None:
                raise
            if created_item_id:
                orphan = session.get(IntakeItem, created_item_id)
                if orphan is not None:
                    session.delete(orphan)
                    session.commit()
                    run = session.get(CollectionRun, run_id)
                    target = session.get(CollectionTarget, run.target_id) if run else None
                    if run is None or target is None:
                        raise
            finished = utcnow()
            run.status = "failed"
            run.active_key = None
            run.outcome = None
            run.completed_at = finished
            run.duration_ms = max(0, int((time.monotonic() - started_clock) * 1000))
            run.lease_owner = None
            run.lease_expires_at = None
            run.error_class = classify_collection_error(exc)
            run.error_message = str(exc)[:4000]
            failure_count = target.consecutive_failures + 1
            failure_health = "error" if failure_count >= 3 else "degraded"
            delay = min(target.interval_seconds, 60 * (2 ** (failure_count - 1)))
            session.execute(
                update(CollectionTarget)
                .where(CollectionTarget.id == target.id)
                .values(
                    health=case(
                        (CollectionTarget.enabled.is_(True), failure_health),
                        else_="paused",
                    ),
                    consecutive_failures=failure_count,
                    last_run_at=finished,
                    last_error=str(exc)[:4000],
                    next_run_at=case(
                        (
                            CollectionTarget.enabled.is_(True),
                            finished + timedelta(seconds=delay),
                        ),
                        else_=None,
                    ),
                    updated_at=finished,
                )
                .execution_options(synchronize_session=False)
            )
            session.commit()
            return run


async def run_once(
    *, worker_id: str | None = None, lease_seconds: int = DEFAULT_LEASE_SECONDS
) -> CollectionRun | None:
    identity = worker_id or worker_identity()
    with SessionLocal() as session:
        claimed = claim_next_run(session, worker_id=identity, lease_seconds=lease_seconds)
        run_id = claimed.id if claimed else None
    return await execute_claimed_run(run_id) if run_id else None


def collection_summary(session: Session, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    targets = list(session.scalars(select(CollectionTarget)))
    runs = list(session.scalars(select(CollectionRun)))
    current_intake_ids = {
        run.current_intake_item_id
        for run in runs
        if run.current_intake_item_id and run.outcome in VERSION_OUTCOMES
    }
    pending_review = 0
    if current_intake_ids:
        pending_review = int(
            session.scalar(
                select(func.count())
                .select_from(IntakeItem)
                .where(
                    IntakeItem.id.in_(current_intake_ids),
                    IntakeItem.status.in_(["candidate_ready", "generation_failed", "parsed"]),
                )
            )
            or 0
        )
    effective_health = {
        target.id: (
            "paused"
            if not target.enabled
            else target.health
            if target.health in {"error", "degraded"}
            else "stale"
            if target_is_overdue(target, now=now)
            else target.health
        )
        for target in targets
    }
    enabled_target_ids = {target.id for target in targets if target.enabled}
    return {
        "targets": {
            "total": len(targets),
            "enabled": sum(1 for target in targets if target.enabled),
            "healthy": sum(1 for target in targets if effective_health[target.id] == "healthy"),
            "degraded": sum(1 for target in targets if effective_health[target.id] == "degraded"),
            "error": sum(1 for target in targets if effective_health[target.id] == "error"),
            "stale": sum(1 for target in targets if effective_health[target.id] == "stale"),
            "paused": sum(1 for target in targets if not target.enabled),
            "due": sum(
                1
                for target in targets
                if target.enabled and target.next_run_at is not None and _as_aware(target.next_run_at) <= now
            ),
        },
        "runs": {
            status: sum(
                1
                for run in runs
                if run.status == status
                and (status != "queued" or run.target_id in enabled_target_ids)
            )
            for status in sorted(RUN_STATUSES)
        },
        "changes": sum(1 for run in runs if run.outcome == "changed"),
        "pending_review": pending_review,
    }


def _as_aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def build_version_diff(session: Session, run: CollectionRun) -> dict[str, Any]:
    if run.status != "succeeded" or not run.current_intake_item_id:
        raise ValueError("Diff is available only for a succeeded run with a captured version")
    current = session.get(IntakeItem, run.current_intake_item_id)
    previous = (
        session.get(IntakeItem, run.previous_intake_item_id)
        if run.previous_intake_item_id
        else None
    )
    if current is None:
        raise ValueError("Current intake version is missing")
    old_text = previous.extracted_snapshot if previous else ""
    new_text = current.extracted_snapshot
    old_words = old_text.split()
    new_words = new_text.split()
    segments: list[dict[str, str]] = []
    added_words = removed_words = 0
    added_parts: list[str] = []
    removed_parts: list[str] = []
    exact_word_diff = len(old_words) + len(new_words) <= MAX_EXACT_DIFF_WORDS
    if exact_word_diff:
        opcodes = difflib.SequenceMatcher(
            a=old_words,
            b=new_words,
            autojunk=True,
        ).get_opcodes()
    else:
        # Large untrusted pages must not drive SequenceMatcher into quadratic work.
        # A linear common-prefix/suffix diff remains deterministic and exact about
        # which middle ranges changed, although it intentionally groups disjoint edits.
        prefix = 0
        common_limit = min(len(old_words), len(new_words))
        while prefix < common_limit and old_words[prefix] == new_words[prefix]:
            prefix += 1
        suffix = 0
        while (
            suffix < len(old_words) - prefix
            and suffix < len(new_words) - prefix
            and old_words[len(old_words) - 1 - suffix]
            == new_words[len(new_words) - 1 - suffix]
        ):
            suffix += 1
        opcodes = []
        if prefix:
            opcodes.append(("equal", 0, prefix, 0, prefix))
        old_end = len(old_words) - suffix
        new_end = len(new_words) - suffix
        if prefix < old_end and prefix < new_end:
            opcodes.append(("replace", prefix, old_end, prefix, new_end))
        elif prefix < old_end:
            opcodes.append(("delete", prefix, old_end, prefix, prefix))
        elif prefix < new_end:
            opcodes.append(("insert", prefix, prefix, prefix, new_end))
        if suffix:
            opcodes.append(
                ("equal", old_end, len(old_words), new_end, len(new_words))
            )

    segments_truncated = False
    for opcode, i1, i2, j1, j2 in opcodes:
        if opcode in {"equal", "delete", "replace"} and i1 != i2:
            kind = "equal" if opcode == "equal" else "removed"
            text = " ".join(old_words[i1:i2])
            if len(segments) < MAX_DIFF_SEGMENTS:
                segments.append({"type": kind, "text": _bounded_diff_text(text)})
            else:
                segments_truncated = True
            if kind == "removed":
                removed_words += i2 - i1
                removed_parts.append(text)
        if opcode in {"insert", "replace"} and j1 != j2:
            text = " ".join(new_words[j1:j2])
            if len(segments) < MAX_DIFF_SEGMENTS:
                segments.append({"type": "added", "text": _bounded_diff_text(text)})
            else:
                segments_truncated = True
            added_words += j2 - j1
            added_parts.append(text)
    old_lines, old_lines_truncated = _diff_lines(old_text[:MAX_DIFF_TEXT_CHARS])
    new_lines, new_lines_truncated = _diff_lines(new_text[:MAX_DIFF_TEXT_CHARS])
    unified_input_truncated = (
        len(old_text) > MAX_DIFF_TEXT_CHARS
        or len(new_text) > MAX_DIFF_TEXT_CHARS
        or old_lines_truncated
        or new_lines_truncated
    )
    unified_lines = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"version-{(run.version_number or 1) - 1}",
            tofile=f"version-{run.version_number or 1}",
            lineterm="",
        )
    )
    unified_output_truncated = len(unified_lines) > MAX_UNIFIED_DIFF_LINES
    unified = "\n".join(unified_lines[:MAX_UNIFIED_DIFF_LINES])
    return {
        "run_id": run.id,
        "target_id": run.target_id,
        "outcome": run.outcome,
        "version_number": run.version_number,
        "previous": {
            "intake_item_id": previous.id if previous else None,
            "body_hash": previous.extracted_hash if previous else None,
        },
        "current": {
            "intake_item_id": current.id,
            "body_hash": current.extracted_hash,
        },
        "stats": {"added_words": added_words, "removed_words": removed_words},
        "added_text": _bounded_diff_text(" ".join(added_parts)),
        "removed_text": _bounded_diff_text(" ".join(removed_parts)),
        "segments": segments,
        "unified_diff": unified,
        "truncated": {
            "exact_word_diff": exact_word_diff,
            "segments": segments_truncated
            or any(len(part) > MAX_DIFF_TEXT_CHARS for part in added_parts + removed_parts),
            "unified_diff": unified_input_truncated or unified_output_truncated,
            "limit_chars": MAX_DIFF_TEXT_CHARS,
            "limit_lines": MAX_UNIFIED_DIFF_LINES,
            "input_limit_lines_per_version": MAX_UNIFIED_INPUT_LINES,
        },
    }


def _bounded_diff_text(value: str) -> str:
    if len(value) <= MAX_DIFF_TEXT_CHARS:
        return value
    return value[:MAX_DIFF_TEXT_CHARS] + " … [diff output truncated]"


def _diff_lines(value: str) -> tuple[list[str], bool]:
    if not value:
        return [], False
    lines = [part.strip() for part in re.findall(r".+?(?:[.!?。！？](?:\s+|$)|$)", value)]
    lines = lines or [value]
    return lines[:MAX_UNIFIED_INPUT_LINES], len(lines) > MAX_UNIFIED_INPUT_LINES
