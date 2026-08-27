from __future__ import annotations

import html as html_lib
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .database import Base, REPO_ROOT, SessionLocal, engine, get_session
from .importers import import_rss, import_url_document
from .llm import run_model_task
from .models import Claim, Document, Event, Evidence, Source
from .reporting import REPORT_DIR, build_report
from .repository import get_event,get_events,serialize_claim,serialize_document,serialize_event_card,serialize_event_detail,serialize_source
from .schemas import ImportRssRequest, ImportUrlRequest, ModelTaskRequest, ReportRequest
from .seed import counts, seed_database

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


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as session:
        seed_database(session)
    yield


app = FastAPI(
    title="PLDR P0 Intelligence API",
    version="0.1.1",
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
app.mount("/assets", StaticFiles(directory=ASSET_DIR), name="assets")
app.mount("/reports", StaticFiles(directory=REPORT_DIR), name="reports")


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(DASHBOARD_DIR / "index.html")


@app.get("/health", include_in_schema=False)
@app.get("/pldr-api/health")
def health(session: Session = Depends(get_session)) -> dict[str, Any]:
    return {"status": "ok", "version": "0.1.1", "mode": "demo", "counts": counts(session)}


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
        "events": cards,
        "information_gaps": list(dict.fromkeys(all_gaps))[:12],
        "last_updated": max((c["end_at"] or c["start_at"] for c in cards), default=None),
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
    try:
        document = await import_url_document(
            session,
            str(request.url),
            request.source_name,
            request.title,
            request.html,
            request.language,
        )
        session.refresh(document)
        document = session.scalar(
            select(Document)
            .where(Document.id == document.id)
            .options(selectinload(Document.source))
        )
        assert document is not None
        return {"status": "ok", "document": serialize_document(document)}
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/import/rss", include_in_schema=False)
@app.post("/pldr-api/v1/import/rss")
async def import_rss_feed(request: ImportRssRequest, session: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        documents = await import_rss(
            session,
            str(request.url) if request.url else None,
            request.xml,
            request.source_name,
            request.language,
        )
        ids = [x.id for x in documents]
        hydrated = (
            list(
                session.scalars(
                    select(Document)
                    .where(Document.id.in_(ids))
                    .options(selectinload(Document.source))
                )
            )
            if ids
            else []
        )
        return {"status": "ok", "count": len(hydrated), "documents": [serialize_document(x) for x in hydrated]}
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
    document = session.scalar(
        select(Document)
        .where(Document.id == document_id)
        .options(selectinload(Document.source), selectinload(Document.evidence_items))
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    body = html_lib.escape(document.body)
    for evidence in sorted(document.evidence_items, key=lambda x: len(x.snippet), reverse=True):
        escaped = html_lib.escape(evidence.snippet)
        body = body.replace(
            escaped,
            f'<mark class="{html_lib.escape(evidence.stance)}">{escaped}</mark>',
            1,
        )
    back_link = f"/?event={html_lib.escape(event_id)}" if event_id else "/"
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{html_lib.escape(document.title)}</title><style>body{{font-family:Inter,'Noto Sans SC',system-ui,sans-serif;background:#071018;color:#d7e5ef;margin:0}}main{{max-width:900px;margin:0 auto;padding:42px 28px}}a{{color:#5bd6ff}}.meta{{color:#7894a7;font-size:13px;line-height:1.8}}article{{background:#0e1b25;border:1px solid #244052;border-radius:12px;padding:24px;line-height:1.85;margin-top:20px}}mark{{padding:2px 4px;border-radius:4px}}mark.supports{{background:#174f36;color:#d9ffe8}}mark.contradicts{{background:#6a3527;color:#ffe5dc}}mark.context{{background:#544b20;color:#fff5bc}}</style></head><body><main><a href='{back_link}'>← 返回 PLDR</a><h1>{html_lib.escape(document.title)}</h1><div class='meta'>来源：{html_lib.escape(document.source.name)} · 类型：{html_lib.escape(document.source.source_type)} · 发布时间：{document.published_at.isoformat()}<br>抓取时间：{document.fetched_at.isoformat()} · 正文 SHA-256：{document.content_hash}<br>独立来源组：{html_lib.escape(document.source.independence_group)}</div><article>{body}</article></main></body></html>"""


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
        ],
    }
