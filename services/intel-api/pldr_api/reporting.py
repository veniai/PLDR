from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.orm import Session

from .database import REPO_ROOT
from .repository import get_event, serialize_event_detail

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
REPORT_DIR = Path(
    os.getenv("PLDR_REPORT_DIR", str(REPO_ROOT / "reports"))
).expanduser().resolve()
REPORT_DIR.mkdir(parents=True, exist_ok=True)


def public_date(value: Any, unknown: str = "未知") -> str:
    if value is None or str(value).strip() == "":
        return unknown
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value).strip()
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return raw
    return parsed.strftime("%Y-%m-%d")


def public_datetime(value: Any, unknown: str = "未知") -> str:
    if value is None or str(value).strip() == "":
        return unknown
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value).strip()
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return raw
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


env = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    autoescape=select_autoescape(["html", "xml"]),
    trim_blocks=True,
    lstrip_blocks=True,
)
env.filters["public_date"] = public_date
env.filters["public_datetime"] = public_datetime


def compose_current_answer(
    findings: list[dict[str, Any]],
    *,
    assessment: str | None = None,
    fallback_summary: str | None = None,
) -> tuple[str, str]:
    """Create a useful answer without overstating single-source material."""
    judgement = (assessment or "").strip()
    if judgement:
        return judgement, "formal_assessment"

    def texts(*statuses: str) -> list[str]:
        allowed = set(statuses)
        return [
            str(item.get("text") or "").strip()
            for item in findings
            if item.get("status") in allowed and str(item.get("text") or "").strip()
        ]

    def joined(items: list[str]) -> str:
        return "；".join(
            item.rstrip(" \t\r\n。；;")
            for item in items[:3]
            if item.rstrip(" \t\r\n。；;")
        )

    supported = texts("confirmed", "supported")
    if supported:
        return f"{joined(supported)}。", "supported_claims"
    single_source = texts("single_source")
    if single_source:
        return (
            f"据当前仅有一个独立来源支持的材料：{joined(single_source)}。"
            "这些信息尚待更多独立来源印证。",
            "single_source_claims",
        )
    contested = texts("contested")
    if contested:
        return (
            f"现有来源说法存在冲突：{joined(contested)}。",
            "contested_claims",
        )
    grounded = [
        str(item.get("text") or "").strip()
        for item in findings
        if item.get("evidence_count") and str(item.get("text") or "").strip()
    ]
    if grounded:
        return (
            f"现有材料记录了：{joined(grounded)}。仍需继续核实。",
            "grounded_unverified_claims",
        )
    summary = (fallback_summary or "").strip()
    if summary:
        return f"已确认事件记录：{summary}", "confirmed_event_summary"
    return "现有材料还不足以形成专题结论，请先补充来源或处理冲突。", "insufficient_evidence"

def safe_slug(value:str)->str:
    value=re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+","-",value).strip("-"); return value[:50] or "pldr-brief"

def build_report(
    session: Session,
    event_ids: list[str],
    title: str | None = None,
    *,
    demo_notice: bool = True,
    current_answer_override: str | None = None,
    information_gaps_override: list[str] | None = None,
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
    single_source_claim_count=0
    claims_without_evidence=0
    source_ids=set()
    source_documents=[]
    seen_documents=set()
    information_gaps=[]
    key_findings=[]
    for event in events:
        for document in event.get("documents",[]):
            source_id=(document.get("source") or {}).get("id")
            if source_id: source_ids.add(source_id)
            document_key=document.get("id") or (document.get("source") or {}).get("name"), document.get("title")
            if document_key not in seen_documents:
                seen_documents.add(document_key)
                source_documents.append(document)
        assessment=event.get("assessment") or {}
        for gap in assessment.get("information_gaps") or []:
            cleaned=str(gap).strip()
            if cleaned and cleaned not in information_gaps: information_gaps.append(cleaned)
        for claim in event["claims"]:
            claim_count+=1
            public_status=(claim.get("source_verification") or {}).get("status") or claim.get("status") or "unverified"
            claim["raw_status"]=claim.get("status")
            claim["status"]=public_status
            if public_status in {"unverified", "contested"}:
                unresolved_claim_count+=1
            if public_status == "single_source":
                single_source_claim_count+=1
            if not claim.get("evidence"):
                claims_without_evidence+=1
            for evidence in claim["evidence"]:
                evidence["index"]=evidence_index; evidence_index+=1
            if claim.get("text") and len(key_findings) < 8:
                key_findings.append({
                    "text": claim["text"],
                    "status": public_status,
                    "origin": claim.get("origin") or "unknown",
                    "event_title": event.get("title") or "未知事件",
                    "evidence_count": len(claim.get("evidence") or []),
                    "independent_source_count": (claim.get("source_verification") or {}).get("independent_source_count", 0),
                    "evidence": claim.get("evidence") or [],
                })
    if unresolved_claim_count:
        information_gaps.append(f"{unresolved_claim_count} 条关键信息需要补充来源或处理冲突。")
    if single_source_claim_count:
        information_gaps.append(f"{single_source_claim_count} 条关键信息目前只有一个独立来源。")
    if claims_without_evidence:
        information_gaps.append(f"{claims_without_evidence} 条关键信息尚未连接可定位的原文依据。")
    for gap in information_gaps_override or []:
        cleaned=str(gap).strip()
        if cleaned and cleaned not in information_gaps: information_gaps.append(cleaned)
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
    current_answer, _ = compose_current_answer(
        key_findings,
        assessment=(current_answer_override or "").strip() or latest_assessment.get("judgement"),
        fallback_summary=latest_event.get("summary"),
    )
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
        key_findings=key_findings,
        source_documents=source_documents,
        claim_status_labels={"confirmed":"人工确认","supported":"多源印证","single_source":"单一来源","contested":"存在冲突","unverified":"缺少依据","refuted":"已有反证"},
        stance_labels={"supports":"支持","contradicts":"冲突","context":"背景"},
    )
    stamp=generated_at.strftime("%Y%m%dT%H%M%SZ"); filename=f"{safe_slug(report_title)}-{stamp}.html"; REPORT_DIR.mkdir(parents=True,exist_ok=True); (REPORT_DIR/filename).write_text(html,encoding="utf-8")
    return {"title":report_title,"filename":filename,"url":f"/reports/{filename}","generated_at":generated_at.isoformat().replace("+00:00","Z"),"event_count":len(events),"evidence_count":evidence_index-1}
