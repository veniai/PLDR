from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class ModelConfig:
    api_key: str
    base_url: str
    model: str
    timeout_seconds: float

    @classmethod
    def from_env(cls) -> "ModelConfig | None":
        api_key = os.getenv("LLM_API_KEY", "").strip()
        base_url = os.getenv("LLM_BASE_URL", "").strip().rstrip("/")
        model = os.getenv("LLM_MODEL_NAME", "").strip()
        if not api_key or not base_url or not model:
            return None
        return cls(api_key=api_key,base_url=base_url,model=model,timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "60")))


SYSTEM_PROMPT = """You are the structured extraction component of an evidence-centered OSINT system.
Return valid JSON only. Separate observed facts from inference. Never invent citations or evidence.
Every evidence snippet must be an exact substring of the supplied document text."""


async def run_model_task(task: str, payload: dict[str, Any]) -> dict[str, Any]:
    config = ModelConfig.from_env()
    if config is None:
        return {"mode":"fallback","task":task,"result":deterministic_fallback(task,payload),"warning":"No model API configured; deterministic fallback was used."}
    endpoint = f"{config.base_url}/chat/completions"
    body={"model":config.model,"temperature":0.1,"response_format":{"type":"json_object"},"messages":[{"role":"system","content":SYSTEM_PROMPT},{"role":"user","content":json.dumps({"task":task,"payload":payload},ensure_ascii=False)}]}
    headers={"Authorization":f"Bearer {config.api_key}","Content-Type":"application/json"}
    async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
        response=await client.post(endpoint,headers=headers,json=body); response.raise_for_status(); data=response.json()
    content=data["choices"][0]["message"]["content"]
    return {"mode":"api","task":task,"model":config.model,"result":json.loads(content)}


def deterministic_fallback(task: str, payload: dict[str, Any]) -> dict[str, Any]:
    if task == "normalize_event_title": return {"title":str(payload.get("title","Untitled event")).strip()[:160]}
    if task == "summarize_event":
        joined=" ".join(str(item) for item in payload.get("texts",[])); return {"summary":joined[:500]}
    if task == "extract_entities_locations": return {"entities":payload.get("seed_entities",[]),"locations":payload.get("seed_locations",[])}
    if task == "extract_claims_evidence": return {"claims":[],"warning":"Fallback does not invent claims."}
    if task == "draft_report": return {"title":payload.get("title","PLDR brief"),"sections":payload.get("sections",[])}
    return {"warning":f"Unknown task: {task}"}
