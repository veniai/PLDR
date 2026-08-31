from __future__ import annotations

import re
from datetime import datetime,timezone
import os
from pathlib import Path
from typing import Any
from jinja2 import Environment,FileSystemLoader,select_autoescape
from sqlalchemy.orm import Session
from .database import REPO_ROOT
from .repository import get_event,serialize_event_detail

TEMPLATE_DIR=Path(__file__).resolve().parent/"templates"
REPORT_DIR=Path(os.getenv("PLDR_REPORT_DIR",str(REPO_ROOT/"reports"))).expanduser().resolve(); REPORT_DIR.mkdir(parents=True,exist_ok=True)
env=Environment(loader=FileSystemLoader(TEMPLATE_DIR),autoescape=select_autoescape(["html","xml"]),trim_blocks=True,lstrip_blocks=True)

def safe_slug(value:str)->str:
    value=re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+","-",value).strip("-"); return value[:50] or "pldr-brief"

def build_report(
    session: Session,
    event_ids: list[str],
    title: str | None = None,
    *,
    demo_notice: bool = True,
) -> dict[str, Any]:
    events=[]
    for event_id in event_ids:
        event=get_event(session,event_id)
        if event is not None: events.append(serialize_event_detail(event))
    if not events: raise ValueError("No valid events selected")
    generated_at=datetime.now(timezone.utc); report_title=title or f"PLDR 专题简报：{events[0]['title']}"
    evidence_index=1
    claim_count=0
    unresolved_claim_count=0
    claims_without_evidence=0
    source_ids=set()
    information_gaps=[]
    for event in events:
        for document in event.get("documents",[]):
            source_id=(document.get("source") or {}).get("id")
            if source_id: source_ids.add(source_id)
        assessment=event.get("assessment") or {}
        for gap in assessment.get("information_gaps") or []:
            cleaned=str(gap).strip()
            if cleaned and cleaned not in information_gaps: information_gaps.append(cleaned)
        for claim in event["claims"]:
            claim_count+=1
            if claim.get("status") in {"unverified", "contested"}:
                unresolved_claim_count+=1
            if not claim.get("evidence"):
                claims_without_evidence+=1
            for evidence in claim["evidence"]:
                evidence["index"]=evidence_index; evidence_index+=1
    if unresolved_claim_count:
        information_gaps.append(f"{unresolved_claim_count} 条主张仍处于待核实或证据冲突状态。")
    if claims_without_evidence:
        information_gaps.append(f"{claims_without_evidence} 条主张尚未连接固定原文证据。")
    # Keep the frozen report's headline aligned with the live outcome page:
    # prefer the newest known event time, then the most recently linked event.
    latest_event=max(
        enumerate(events),
        key=lambda pair: (
            bool(pair[1].get("start_at")),
            pair[1].get("start_at") or "",
            pair[0],
        ),
    )[1]
    latest_assessment=latest_event.get("assessment") or {}
    current_answer=latest_assessment.get("judgement") or latest_event.get("summary") or "尚未填写专题结论。"
    html=env.get_template("report.html").render(
        title=report_title,
        generated_at=generated_at.isoformat().replace("+00:00","Z"),
        events=events,
        demo_notice=demo_notice,
        current_answer=current_answer,
        event_count=len(events),
        claim_count=claim_count,
        evidence_count=evidence_index-1,
        source_count=len(source_ids),
        information_gaps=information_gaps,
        claim_status_labels={"confirmed":"已证实","supported":"有证据支持","contested":"证据存在冲突","unverified":"待核实","refuted":"已反驳"},
        stance_labels={"supports":"支持","contradicts":"冲突","context":"背景"},
    )
    stamp=generated_at.strftime("%Y%m%dT%H%M%SZ"); filename=f"{safe_slug(report_title)}-{stamp}.html"; REPORT_DIR.mkdir(parents=True,exist_ok=True); (REPORT_DIR/filename).write_text(html,encoding="utf-8")
    return {"title":report_title,"filename":filename,"url":f"/reports/{filename}","generated_at":generated_at.isoformat().replace("+00:00","Z"),"event_count":len(events),"evidence_count":evidence_index-1}
