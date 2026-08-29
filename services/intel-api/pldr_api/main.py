from __future__ import annotations

import html as html_lib
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import Session, selectinload

from .collection_routes import router as collection_router
from .database import Base, REPO_ROOT, SessionLocal, engine, get_session
from .intake import (
    build_confirmation_preview,
    cancel_intake,
    confirm_intake,
    get_intake_item,
    reject_intake,
    serialize_intake,
    submit_file_intake,
    submit_rss_intake,
    submit_text_intake,
    submit_web_intake,
    generate_candidates,
)
from .llm import run_model_task
from .models import Claim, Document, Entity, Event, Evidence, IntakeItem, Snapshot, Source
from .reporting import REPORT_DIR, build_report
from .repository import (
    get_event,
    get_events,
    serialize_claim,
    serialize_event_card,
    serialize_event_detail,
    serialize_source,
)
from .schemas import (
    ExternalSearchRequest,
    ExternalSearchSelectionRequest,
    ImportRssRequest,
    ImportUrlRequest,
    IntakeCancelRequest,
    IntakeConfirmationRequest,
    IntakeRejectRequest,
    IntakeTextRequest,
    ModelTaskRequest,
    ReportRequest,
)
from .seed import counts, seed_database
from .search import (
    ExternalSearchError,
    execute_external_search,
    provider_metadata,
    retry_search_result,
    select_search_results,
)

DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
ASSET_DIR = DASHBOARD_DIR / "assets"


def configured_cors_origins() -> list[str]:
    raw = os.getenv(
        "PLDR_CORS_ORIGINS",
        "http://127.0.0.1:8765,http://localhost:8765",
    )
    return [item.strip() for item in raw.split(",") if item.strip()]


def require_admin_token(x_pldr_admin_token: str | None = Header(default=None)) -> None:
    expected = os.getenv("PLDR_ADMIN_TOKEN", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="Admin API is disabled; set PLDR_ADMIN_TOKEN to enable it")
    if not x_pldr_admin_token or not secrets.compare_digest(x_pldr_admin_token, expected):
        raise HTTPException(status_code=403, detail="Invalid admin token")


def ensure_compatible_schema() -> None:
    """Add additive P0.3 columns to an existing P0.2 database without rebuilding user data."""
    inspector = inspect(engine)
    if "events" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("events")}
        if "metadata_json" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE events ADD COLUMN metadata_json JSON"))
    if "evidence" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("evidence")}
        if "snapshot_id" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE evidence ADD COLUMN snapshot_id VARCHAR(64)"))
                connection.execute(text("CREATE INDEX IF NOT EXISTS idx_evidence_snapshot_id ON evidence (snapshot_id)"))
    if "snapshots" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("snapshots")}
        if "metadata_json" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE snapshots ADD COLUMN metadata_json JSON"))
    if "collection_runs" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("collection_runs")}
        if "active_key" not in columns:
            with engine.begin() as connection:
                connection.execute(
                    text("ALTER TABLE collection_runs ADD COLUMN active_key VARCHAR(80)")
                )
        # Repair only a half-finished migration. Rewriting every historical run on
        # every startup would take an unnecessary SQLite write lock, while a clean
        # schema already has either the named unique index or an equivalent unique
        # constraint covering active_key.
        collection_inspector = inspect(engine)
        unique_constraints = collection_inspector.get_unique_constraints("collection_runs")
        unique_indexes = collection_inspector.get_indexes("collection_runs")
        has_active_guard = any(
            set(constraint.get("column_names") or []) == {"active_key"}
            for constraint in unique_constraints
        ) or any(
            index.get("unique")
            and set(index.get("column_names") or []) == {"active_key"}
            for index in unique_indexes
        )
        if not has_active_guard:
            with engine.begin() as connection:
                connection.execute(text("UPDATE collection_runs SET active_key = NULL"))
                connection.execute(
                    text(
                        "UPDATE collection_runs SET active_key = target_id "
                        "WHERE status IN ('queued', 'running') AND id IN ("
                        "SELECT MIN(id) FROM collection_runs "
                        "WHERE status IN ('queued', 'running') GROUP BY target_id)"
                    )
                )
                connection.execute(
                    text(
                        "CREATE UNIQUE INDEX uq_collection_run_active_key "
                        "ON collection_runs (active_key)"
                    )
                )


def backfill_evidence_snapshots() -> None:
    """Attach pre-P0.3 evidence to the exact stored snapshot containing its snippet."""
    with SessionLocal() as session:
        evidence_rows = list(session.scalars(select(Evidence).where(Evidence.snapshot_id.is_(None))))
        for evidence in evidence_rows:
            snapshots = list(
                session.scalars(
                    select(Snapshot)
                    .where(Snapshot.document_id == evidence.document_id)
                    .order_by(Snapshot.captured_at.desc())
                )
            )
            if not snapshots:
                continue
            matching = next(
                (
                    snapshot
                    for snapshot in snapshots
                    if snapshot.excerpt[evidence.start_offset : evidence.end_offset] == evidence.snippet
                ),
                None,
            )
            evidence.snapshot_id = (matching or snapshots[0]).id
        session.commit()


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_compatible_schema()
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as session:
        seed_database(session)
    backfill_evidence_snapshots()
    yield


app = FastAPI(
    title="PLDR P0 Intelligence API",
    version="0.3.0",
    description="Evidence-centered OSINT P0 with a World Monitor-inspired map-first dashboard.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=configured_cors_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-PLDR-Admin-Token"],
)
app.include_router(collection_router)
app.mount("/assets", StaticFiles(directory=ASSET_DIR), name="assets")
app.mount("/reports", StaticFiles(directory=REPORT_DIR), name="reports")


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(DASHBOARD_DIR / "index.html")


@app.get("/health", include_in_schema=False)
@app.get("/pldr-api/health")
def health(session: Session = Depends(get_session)) -> dict[str, Any]:
    return {"status": "ok", "version": "0.3.0", "mode": "demo", "counts": counts(session)}


@app.post("/api/v1/admin/reseed", include_in_schema=False)
@app.post("/pldr-api/v1/admin/reseed")
def reseed(
    force: bool = Query(default=True),
    _: None = Depends(require_admin_token),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return {"status": "ok", "counts": seed_database(session, force=force)}


@app.get("/api/v1/overview", include_in_schema=False)
@app.get("/pldr-api/v1/overview")
def overview(session: Session = Depends(get_session)) -> dict[str, Any]:
    evs = get_events(session)
    sources = list(
        session.scalars(
            select(Source)
            .options(selectinload(Source.documents))
            .order_by(Source.reliability_tier, Source.name)
        )
    )
    source_status = {status: 0 for status in ["healthy", "stale", "error", "disabled"]}
    for source in sources:
        source_status[source.status] = source_status.get(source.status, 0) + 1
    cards = [serialize_event_card(event) for event in evs]
    all_gaps: list[str] = []
    for event in evs:
        if event.assessments:
            all_gaps.extend(max(event.assessments, key=lambda x: x.generated_at).information_gaps)
    return {
        "topic": {
            "id": "suez-2021-demo",
            "title": "苏伊士运河阻塞事件链",
            "subtitle": "证据化开源情报纵向切片",
            "description": "人工整理的公开事件演示快照。用于验证事件聚合、来源独立性、冲突主张、证据回跳和报告生成。",
            "mode": "curated-demo",
            "time_range": {
                "start": cards[0]["start_at"] if cards else None,
                "end": (cards[-1]["end_at"] or cards[-1]["start_at"]) if cards else None,
            },
        },
        "metrics": {
            **counts(session),
            "independence_groups": len({s.independence_group for s in sources}),
            "contested_claims": session.scalar(
                select(func.count())
                .select_from(Claim)
                .where(Claim.status.in_(["contested", "unverified"]))
            )
            or 0,
            "source_status": source_status,
        },
        "intake": {
            "total": session.scalar(select(func.count()).select_from(IntakeItem)) or 0,
            "candidate_ready": session.scalar(
                select(func.count()).select_from(IntakeItem).where(IntakeItem.status == "candidate_ready")
            )
            or 0,
            "confirmed": session.scalar(
                select(func.count()).select_from(IntakeItem).where(IntakeItem.status == "confirmed")
            )
            or 0,
        },
        "events": cards,
        "information_gaps": list(dict.fromkeys(all_gaps))[:12],
        "last_updated": max(
            (value for c in cards if (value := c["end_at"] or c["start_at"]) is not None),
            default=None,
        ),
        "disclaimer": "Demo snapshots are paraphrased and must be replaced by freshly captured originals before operational use.",
    }


@app.get("/api/v1/events", include_in_schema=False)
@app.get("/pldr-api/v1/events")
def events(
    importance: str | None = None,
    source_type: str | None = None,
    language: str | None = None,
    contested_only: bool = False,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    cards = [serialize_event_card(e) for e in get_events(session)]
    if importance:
        cards = [c for c in cards if c["importance"] == importance]
    if source_type:
        cards = [c for c in cards if source_type in c["source_types"]]
    if language:
        cards = [c for c in cards if language in c["languages"]]
    if contested_only:
        cards = [c for c in cards if c["has_contested_claim"]]
    return {"items": cards, "count": len(cards)}


@app.get("/api/v1/events/{event_id}", include_in_schema=False)
@app.get("/pldr-api/v1/events/{event_id}")
def event_detail(event_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    event = get_event(session, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return serialize_event_detail(event)


@app.get("/api/v1/claims/{claim_id}/evidence", include_in_schema=False)
@app.get("/pldr-api/v1/claims/{claim_id}/evidence")
def claim_evidence(claim_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    claim = session.scalar(
        select(Claim)
        .where(Claim.id == claim_id)
        .options(selectinload(Claim.evidence_items).selectinload(Evidence.document).selectinload(Document.source))
    )
    if claim is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    return serialize_claim(claim)


@app.get("/api/v1/sources/health", include_in_schema=False)
@app.get("/pldr-api/v1/sources/health")
def source_health(session: Session = Depends(get_session)) -> dict[str, Any]:
    sources = list(
        session.scalars(
            select(Source)
            .options(selectinload(Source.documents))
            .order_by(Source.status, Source.reliability_tier, Source.name)
        )
    )
    return {"items": [serialize_source(s) for s in sources], "count": len(sources)}


@app.post("/api/v1/reports", include_in_schema=False)
@app.post("/pldr-api/v1/reports")
def create_report(request: ReportRequest, session: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        return build_report(session, request.event_ids, request.title)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/import/url", include_in_schema=False)
@app.post("/pldr-api/v1/import/url")
async def import_url(request: ImportUrlRequest, session: Session = Depends(get_session)) -> dict[str, Any]:
    item = await submit_web_intake(
        session,
        str(request.url),
        request.source_name,
        request.title,
        request.html,
        request.language,
    )
    return {"status": item.status, "intake_item": serialize_intake(item)}


@app.post("/api/v1/import/rss", include_in_schema=False)
@app.post("/pldr-api/v1/import/rss")
async def import_rss_feed(request: ImportRssRequest, session: Session = Depends(get_session)) -> dict[str, Any]:
    items = await submit_rss_intake(
        session,
        str(request.url) if request.url else None,
        request.xml,
        request.source_name,
        request.language,
    )
    serialized = [serialize_intake(item) for item in items]
    return {
        "status": "ok" if all(item.status != "failed" for item in items) else "partial_failure",
        "count": len(serialized),
        "intake_items": serialized,
        "documents": [
            {
                "id": item.id,
                "title": item.title,
                "status": item.status,
                "error": item.error,
            }
            for item in items
        ],
    }


@app.post("/api/v1/search", include_in_schema=False)
@app.post("/pldr-api/v1/search")
async def external_search(
    request: ExternalSearchRequest, session: Session = Depends(get_session)
) -> dict[str, Any]:
    try:
        return await execute_external_search(session, request)
    except ExternalSearchError as exc:
        session.rollback()
        raise HTTPException(
            status_code=exc.status_code,
            detail={"message": str(exc), "reason": exc.reason, "query_run_id": exc.query_run_id},
        ) from exc


@app.post("/api/v1/search/select", include_in_schema=False)
@app.post("/pldr-api/v1/search/select")
async def select_external_search_results(
    request: ExternalSearchSelectionRequest, session: Session = Depends(get_session)
) -> dict[str, Any]:
    try:
        return await select_search_results(session, request)
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/v1/search/results/{result_id}/retry", include_in_schema=False)
@app.post("/pldr-api/v1/search/results/{result_id}/retry")
async def retry_external_search_result(
    result_id: str, session: Session = Depends(get_session)
) -> dict[str, Any]:
    try:
        return await retry_search_result(session, result_id)
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/v1/intake/text", include_in_schema=False)
@app.post("/pldr-api/v1/intake/text")
async def intake_text(request: IntakeTextRequest, session: Session = Depends(get_session)) -> dict[str, Any]:
    item = await submit_text_intake(session, request)
    return {"status": item.status, "intake_item": serialize_intake(item)}


@app.post("/api/v1/intake/files", include_in_schema=False)
@app.post("/pldr-api/v1/intake/files")
async def intake_file(
    file: UploadFile = File(...),
    source_description: str = Form(...),
    language: str = Form("en"),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    item = await submit_file_intake(session, file, source_description, language)
    return {"status": item.status, "intake_item": serialize_intake(item)}


@app.get("/api/v1/intake", include_in_schema=False)
@app.get("/pldr-api/v1/intake")
def intake_list(
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    query = select(IntakeItem).options(selectinload(IntakeItem.candidates)).order_by(IntakeItem.created_at.desc())
    if status:
        query = query.where(IntakeItem.status == status)
    items = list(session.scalars(query.limit(limit)))
    return {"items": [serialize_intake(item) for item in items], "count": len(items)}


@app.get("/api/v1/intake/options", include_in_schema=False)
@app.get("/pldr-api/v1/intake/options")
def intake_options(session: Session = Depends(get_session)) -> dict[str, Any]:
    events = list(session.scalars(select(Event).order_by(Event.start_at.desc())))
    entities = list(session.scalars(select(Entity).order_by(Entity.name)))
    return {
        "events": [{"id": event.id, "title": event.title} for event in events],
        "entities": [{"id": entity.id, "name": entity.name, "type": entity.entity_type} for entity in entities],
    }


@app.post("/api/v1/intake/{item_id}/regenerate", include_in_schema=False)
@app.post("/pldr-api/v1/intake/{item_id}/regenerate")
async def regenerate_intake_candidates(
    item_id: str,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    item = get_intake_item(session, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Intake item not found")
    if item.status not in {"parsed", "generation_failed"}:
        raise HTTPException(status_code=409, detail=f"Candidates cannot be regenerated from status {item.status}")
    item = await generate_candidates(session, item)
    return serialize_intake(item)


@app.post("/api/v1/intake/{item_id}/preview", include_in_schema=False)
@app.post("/pldr-api/v1/intake/{item_id}/preview")
def intake_preview(
    item_id: str,
    request: IntakeConfirmationRequest,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    item = get_intake_item(session, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Intake item not found")
    return build_confirmation_preview(session, item, request)


@app.post("/api/v1/intake/{item_id}/confirm", include_in_schema=False)
@app.post("/pldr-api/v1/intake/{item_id}/confirm")
def intake_confirm(
    item_id: str,
    request: IntakeConfirmationRequest,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    item = get_intake_item(session, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Intake item not found")
    try:
        item, result, created = confirm_intake(session, item, request)
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "confirmed", "created": created, "result": result, "intake_item": serialize_intake(item)}


@app.post("/api/v1/intake/{item_id}/reject", include_in_schema=False)
@app.post("/pldr-api/v1/intake/{item_id}/reject")
def intake_reject(
    item_id: str,
    request: IntakeRejectRequest,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    item = get_intake_item(session, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Intake item not found")
    try:
        item = reject_intake(session, item, request.analyst, request.reason)
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "rejected", "intake_item": serialize_intake(item)}


@app.post("/api/v1/intake/{item_id}/cancel", include_in_schema=False)
@app.post("/pldr-api/v1/intake/{item_id}/cancel")
def intake_cancel(
    item_id: str,
    request: IntakeCancelRequest,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    item = get_intake_item(session, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Intake item not found")
    try:
        item = cancel_intake(session, item, request.analyst, request.reason)
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "cancelled", "intake_item": serialize_intake(item)}


@app.get("/api/v1/intake/{item_id}", include_in_schema=False)
@app.get("/pldr-api/v1/intake/{item_id}")
def intake_detail(item_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    item = get_intake_item(session, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Intake item not found")
    return serialize_intake(item)


@app.post("/api/v1/model/task", include_in_schema=False)
@app.post("/pldr-api/v1/model/task")
async def model_task(request: ModelTaskRequest) -> dict[str, Any]:
    try:
        return await run_model_task(request.task, request.payload)
    except Exception as exc:
        return JSONResponse(
            status_code=502,
            content={"status": "error", "detail": str(exc), "fallback_available": True},
        )


@app.get("/snapshots/{document_id}", response_class=HTMLResponse)
def snapshot(document_id: str, event_id: str | None = None, session: Session = Depends(get_session)) -> str:
    selected_snapshot = session.scalar(
        select(Snapshot)
        .where(Snapshot.id == document_id)
        .options(selectinload(Snapshot.document).selectinload(Document.source))
    )
    document = (
        selected_snapshot.document
        if selected_snapshot is not None
        else session.scalar(
            select(Document)
            .where(Document.id == document_id)
            .options(selectinload(Document.source), selectinload(Document.evidence_items))
        )
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    session.refresh(document, attribute_names=["evidence_items"])
    relevant_evidence = [
        evidence
        for evidence in document.evidence_items
        if selected_snapshot is None or evidence.snapshot_id == selected_snapshot.id
    ]
    snapshot_body = selected_snapshot.excerpt if selected_snapshot is not None else document.body
    body = html_lib.escape(snapshot_body)
    for evidence in sorted(relevant_evidence, key=lambda x: len(x.snippet), reverse=True):
        escaped = html_lib.escape(evidence.snippet)
        body = body.replace(
            escaped,
            f'<mark class="{html_lib.escape(evidence.stance)}">{escaped}</mark>',
            1,
        )
    back_link = f"/?event={html_lib.escape(event_id)}" if event_id else "/"
    document_metadata = document.metadata_json or {}
    snapshot_metadata = (selected_snapshot.metadata_json or {}) if selected_snapshot else {}
    if selected_snapshot is not None:
        latest_snapshot_id = document_metadata.get("latest_snapshot_id")
        selected_is_head = latest_snapshot_id == selected_snapshot.id or (
            not latest_snapshot_id
            and selected_snapshot.content_hash == document.content_hash
            and selected_snapshot.excerpt == document.body
        )
        if "title_known" not in snapshot_metadata and selected_is_head:
            title_display = (
                "未知标题" if document_metadata.get("title_known") is False else document.title
            )
            published_display = (
                document.published_at.isoformat().replace("+00:00", "Z")
                if document.published_at
                and document_metadata.get("published_at_known", True) is not False
                else "未知"
            )
        else:
            snapshot_title = snapshot_metadata.get("title")
            title_display = (
                str(snapshot_title)
                if snapshot_metadata.get("title_known") is True and snapshot_title
                else "历史快照（该版本标题未记录）"
            )
            snapshot_published = snapshot_metadata.get("published_at")
            published_display = (
                str(snapshot_published)
                if snapshot_metadata.get("published_at_known") is True and snapshot_published
                else "未知"
            )
    else:
        title_display = (
            "未知标题" if document_metadata.get("title_known") is False else document.title
        )
        published_known = document_metadata.get("published_at_known", True) is not False
        published_display = (
            document.published_at.isoformat().replace("+00:00", "Z")
            if document.published_at and published_known
            else "未知"
        )
    source_url = document.canonical_url if not document.canonical_url.startswith("pldr:") else ""
    source_url_display = (
        f"<a href='{html_lib.escape(source_url, quote=True)}' target='_blank' rel='noopener'>{html_lib.escape(source_url)}</a>"
        if source_url
        else "未知"
    )
    captured_at = (
        selected_snapshot.captured_at if selected_snapshot is not None else document.fetched_at
    ).isoformat()
    snapshot_hash = (
        selected_snapshot.content_hash if selected_snapshot is not None else document.content_hash
    )
    snapshot_id_display = selected_snapshot.id if selected_snapshot is not None else "未知"
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{html_lib.escape(title_display)}</title><style>body{{font-family:Inter,'Noto Sans SC',system-ui,sans-serif;background:#071018;color:#d7e5ef;margin:0}}main{{max-width:900px;margin:0 auto;padding:42px 28px}}a{{color:#5bd6ff}}h1,.meta,article{{overflow-wrap:anywhere}}.meta{{color:#7894a7;font-size:13px;line-height:1.8}}article{{background:#0e1b25;border:1px solid #244052;border-radius:12px;padding:24px;line-height:1.85;margin-top:20px}}mark{{padding:2px 4px;border-radius:4px}}mark.supports{{background:#174f36;color:#d9ffe8}}mark.contradicts{{background:#6a3527;color:#ffe5dc}}mark.context{{background:#544b20;color:#fff5bc}}@media(max-width:580px){{main{{padding:24px 16px}}article{{padding:18px}}}}</style></head><body><main><a href='{back_link}'>← 返回 PLDR</a><h1>{html_lib.escape(title_display)}</h1><div class='meta'>来源：{html_lib.escape(document.source.name)} · 类型：{html_lib.escape(document.source.source_type)} · 发布时间：{published_display}<br>抓取时间：{captured_at} · 正文 SHA-256：{snapshot_hash}<br>Snapshot：{html_lib.escape(snapshot_id_display)}<br>独立来源组：{html_lib.escape(document.source.independence_group)}<br>原始地址：{source_url_display}</div><article>{body}</article></main></body></html>"""


@app.get("/api/v1/timeline", include_in_schema=False)
@app.get("/pldr-api/v1/timeline")
def timeline(session: Session = Depends(get_session)) -> dict[str, Any]:
    cards = [serialize_event_card(e) for e in get_events(session)]
    return {"items": cards, "count": len(cards)}


@app.get("/api/v1/snapshot", include_in_schema=False)
@app.get("/pldr-api/v1/snapshot")
def export_snapshot(session: Session = Depends(get_session)) -> dict[str, Any]:
    events_data = [serialize_event_detail(e) for e in get_events(session)]
    sources = list(
        session.scalars(
            select(Source)
            .options(selectinload(Source.documents))
            .order_by(Source.reliability_tier, Source.name)
        )
    )
    return {
        "schema_version": "pldr-p0.1",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "mode": "curated-demo",
        "events": events_data,
        "sources": [serialize_source(s) for s in sources],
    }


@app.get("/api/v1/config", include_in_schema=False)
@app.get("/pldr-api/v1/config")
def runtime_config() -> dict[str, Any]:
    return {
        "pldr_mode": True,
        "dashboard": "world-monitor-inspired-shell",
        "model_configured": bool(
            os.getenv("LLM_API_KEY") and os.getenv("LLM_BASE_URL") and os.getenv("LLM_MODEL_NAME")
        ),
        "features": [
            "events",
            "map",
            "timeline",
            "claims",
            "evidence",
            "source_health",
            "html_reports",
            "url_import",
            "rss_import",
            "intake_inbox",
            "candidate_isolation",
            "human_confirmation",
            "file_intake",
            "external_keyword_discovery",
            "reliable_collection",
        ],
        "external_search": provider_metadata(),
    }
