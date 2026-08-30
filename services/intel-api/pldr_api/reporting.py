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
    for event in events:
        for claim in event["claims"]:
            for evidence in claim["evidence"]:
                evidence["index"]=evidence_index; evidence_index+=1
    html=env.get_template("report.html").render(title=report_title,generated_at=generated_at.isoformat().replace("+00:00","Z"),events=events,demo_notice=demo_notice)
    stamp=generated_at.strftime("%Y%m%dT%H%M%SZ"); filename=f"{safe_slug(report_title)}-{stamp}.html"; REPORT_DIR.mkdir(parents=True,exist_ok=True); (REPORT_DIR/filename).write_text(html,encoding="utf-8")
    return {"title":report_title,"filename":filename,"url":f"/reports/{filename}","generated_at":generated_at.isoformat().replace("+00:00","Z"),"event_count":len(events),"evidence_count":evidence_index-1}
