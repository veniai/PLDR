from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .collection import (
    build_version_diff,
    collection_summary,
    enqueue_target_run,
    list_target_runs,
    list_discovered_items,
    list_version_runs,
    new_target_id,
    serialize_discovered_item,
    serialize_run,
    serialize_target,
    utcnow,
)
from .database import get_session
from .extraction import canonicalize_url
from .models import CollectionRun, CollectionTarget
from .schemas import CollectionTargetCreate
from .security import UnsafeUrlError, validate_public_http_url


router = APIRouter(prefix="/pldr-api/v1/collection", tags=["collection"])


def _target_or_404(session: Session, target_id: str) -> CollectionTarget:
    target = session.get(CollectionTarget, target_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Collection target not found")
    return target


def _run_or_404(session: Session, run_id: str) -> CollectionRun:
    run = session.get(CollectionRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Collection run not found")
    return run


@router.get("/summary")
def get_collection_summary(session: Session = Depends(get_session)) -> dict[str, Any]:
    return collection_summary(session)


@router.get("/targets")
def list_collection_targets(
    enabled: bool | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    query = select(CollectionTarget).order_by(CollectionTarget.created_at.desc())
    if enabled is not None:
        query = query.where(CollectionTarget.enabled.is_(enabled))
    targets = list(session.scalars(query.limit(limit)))
    return {
        "items": [serialize_target(session, target) for target in targets],
        "count": len(targets),
    }


@router.post("/targets", status_code=status.HTTP_201_CREATED)
def create_collection_target(
    request: CollectionTargetCreate,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    if request.run_immediately and not request.enabled:
        raise HTTPException(
            status_code=409,
            detail="A disabled target cannot run immediately; enable it first",
        )
    url = canonicalize_url(str(request.url))
    try:
        validate_public_http_url(url)
    except UnsafeUrlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if session.scalar(select(CollectionTarget.id).where(CollectionTarget.url == url)):
        raise HTTPException(status_code=409, detail="Collection target URL already exists")
    now = utcnow()
    target = CollectionTarget(
        id=new_target_id(),
        name=request.name.strip(),
        target_type=request.target_type,
        url=url,
        language=request.language,
        interval_seconds=request.interval_seconds,
        enabled=request.enabled,
        next_run_at=(now + timedelta(seconds=request.interval_seconds)) if request.enabled else None,
        health="new" if request.enabled else "paused",
        created_at=now,
        updated_at=now,
    )
    session.add(target)
    try:
        session.flush()
        from .investigations import link_object, resolve_investigation_context

        investigation, _ = resolve_investigation_context(
            session,
            investigation_id=request.investigation_id,
            new_investigation=request.new_investigation,
            actor=request.actor,
            default_unclassified=True,
        )
        assert investigation is not None
        link_object(
            session,
            investigation.id,
            "collection_target",
            target.id,
            actor=request.actor,
            action="collection.target_linked",
        )
        session.commit()
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Collection target URL already exists") from exc
    queued_run = None
    if request.run_immediately:
        queued_run, _ = enqueue_target_run(session, target, trigger="created")
    return {
        "target": serialize_target(session, target),
        "queued_run": serialize_run(queued_run) if queued_run else None,
        "investigation_id": investigation.id,
    }


@router.get("/targets/{target_id}")
def get_collection_target(
    target_id: str,
    run_limit: int = Query(default=50, ge=1, le=500),
    version_limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    target = _target_or_404(session, target_id)
    return serialize_target(
        session,
        target,
        include_runs=True,
        run_limit=run_limit,
        version_limit=version_limit,
    )


@router.get("/targets/{target_id}/versions")
def list_collection_versions(
    target_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    target = _target_or_404(session, target_id)
    runs = list_version_runs(session, target.id, offset=offset, limit=limit)
    return {
        "items": [serialize_run(run) for run in runs],
        "count": serialize_target(session, target)["version_count"],
        "offset": offset,
        "limit": limit,
    }


@router.get("/targets/{target_id}/runs")
def list_collection_runs(
    target_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    target = _target_or_404(session, target_id)
    runs = list_target_runs(session, target.id, offset=offset, limit=limit)
    return {
        "items": [serialize_run(run) for run in runs],
        "count": serialize_target(session, target)["run_count"],
        "offset": offset,
        "limit": limit,
    }


@router.get("/targets/{target_id}/items")
def list_collection_discovered_items(
    target_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    target = _target_or_404(session, target_id)
    items = list_discovered_items(session, target.id, offset=offset, limit=limit)
    return {
        "items": [serialize_discovered_item(item) for item in items],
        "count": serialize_target(session, target)["discovered_item_count"],
        "offset": offset,
        "limit": limit,
    }


@router.post("/targets/{target_id}/run")
def run_collection_target(
    target_id: str, session: Session = Depends(get_session)
) -> dict[str, Any]:
    target = _target_or_404(session, target_id)
    if not target.enabled:
        raise HTTPException(status_code=409, detail="Collection target is paused")
    run, created = enqueue_target_run(session, target, trigger="manual")
    return {"created": created, "run": serialize_run(run)}


@router.post("/targets/{target_id}/pause")
def pause_collection_target(
    target_id: str, session: Session = Depends(get_session)
) -> dict[str, Any]:
    target = _target_or_404(session, target_id)
    target.enabled = False
    target.health = "paused"
    target.next_run_at = None
    session.commit()
    return serialize_target(session, target)


@router.post("/targets/{target_id}/resume")
def resume_collection_target(
    target_id: str, session: Session = Depends(get_session)
) -> dict[str, Any]:
    target = _target_or_404(session, target_id)
    target.enabled = True
    target.next_run_at = utcnow()
    if target.consecutive_failures:
        target.health = "error" if target.consecutive_failures >= 3 else "degraded"
    elif target.last_success_at:
        target.health = "healthy"
    else:
        target.health = "new"
    session.commit()
    return serialize_target(session, target)


@router.post("/runs/{run_id}/retry")
def retry_collection_run(
    run_id: str, session: Session = Depends(get_session)
) -> dict[str, Any]:
    failed = _run_or_404(session, run_id)
    if failed.status != "failed":
        raise HTTPException(status_code=409, detail="Only a failed collection run can be retried")
    target = _target_or_404(session, failed.target_id)
    if not target.enabled:
        raise HTTPException(status_code=409, detail="Collection target is paused")
    retry, created = enqueue_target_run(
        session, target, trigger="retry", retry_of=failed, deduplicate=True
    )
    if not created:
        raise HTTPException(
            status_code=409,
            detail={"message": "Target already has a queued or running attempt", "run_id": retry.id},
        )
    return {"created": True, "run": serialize_run(retry)}


@router.get("/runs/{run_id}/diff")
def get_collection_version_diff(
    run_id: str, session: Session = Depends(get_session)
) -> dict[str, Any]:
    run = _run_or_404(session, run_id)
    try:
        return build_version_diff(session, run)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
