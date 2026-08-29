from __future__ import annotations

import asyncio
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
Every evidence snippet must be an exact substring of the supplied document text.
Follow required_output exactly: keep its field names, do not substitute synonyms, and do not add
wrapper fields such as answer, task, or result. Its values describe expected types and constraints;
fill them from the payload instead of copying the descriptive strings literally."""


TASK_OUTPUT_CONTRACTS: dict[str, dict[str, Any]] = {
    "normalize_event_title": {"title": "string"},
    "summarize_event": {"summary": "string"},
    "extract_entities_locations": {
        "entities": [
            {
                "name": "string",
                "entity_type": "string",
                "aliases": ["string"],
                "role": "string",
            }
        ],
        "locations": [{"name": "string", "role": "string"}],
    },
    "extract_claims_evidence": {
        "claims": [
            {
                "text": "string",
                "uncertainty": "string or null",
                "temporal_scope": "string or null",
                "evidence": [
                    {
                        "snippet": "exact substring of supplied document text",
                        "stance": "supports, contradicts, or context",
                        "strength": "number from 0 to 1",
                    }
                ],
            }
        ]
    },
    "draft_report": {"title": "string", "sections": [{"heading": "string", "body": "string"}]},
    "extract_intake_candidates": {
        "event": {
            "title": "string or null",
            "summary": "string or null",
            "event_time": "ISO date/time string, exact source text, or null",
            "location_name": "string or null",
        },
        "entities": [
            {
                "name": "exact substring of snapshot",
                "entity_type": "string",
                "aliases": ["string"],
                "role": "string",
            }
        ],
        "claims": [
            {
                "text": "string",
                "uncertainty": "string or null",
                "temporal_scope": "string or null",
                "evidence": [
                    {
                        "snippet": "exact substring of snapshot",
                        "stance": "supports, contradicts, or context",
                        "strength": "number from 0 to 1",
                    }
                ],
            }
        ],
    },
}


def model_request_payload(task: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "task": task,
        "payload": payload,
        "required_output": TASK_OUTPUT_CONTRACTS.get(task, {"warning": "string"}),
    }


def _copy_alias(target: dict[str, Any], canonical: str, *aliases: str) -> None:
    if target.get(canonical) is not None:
        return
    for alias in aliases:
        value = target.get(alias)
        if value is not None:
            target[canonical] = value
            return


def normalize_model_result(task: str, result: Any) -> dict[str, Any]:
    """Normalize common provider aliases without inventing any model content."""
    if not isinstance(result, dict):
        raise ValueError("Model response must be a JSON object")
    normalized = dict(result)
    if task == "normalize_event_title":
        _copy_alias(normalized, "title", "answer", "normalized_title")
        return normalized
    if task == "summarize_event":
        _copy_alias(normalized, "summary", "answer", "description")
        return normalized
    if task not in {"extract_intake_candidates", "extract_claims_evidence", "extract_entities_locations"}:
        return normalized

    event = normalized.get("event")
    if isinstance(event, dict):
        event = dict(event)
        _copy_alias(event, "summary", "description")
        _copy_alias(event, "event_time", "occurred_at", "published_at")
        _copy_alias(event, "location_name", "location")
        if isinstance(event.get("location_name"), dict):
            event["location_name"] = event["location_name"].get("name")
        normalized["event"] = event

    entities = normalized.get("entities")
    if isinstance(entities, list):
        normalized["entities"] = [
            _normalize_entity(item) if isinstance(item, dict) else item for item in entities
        ]

    claims = normalized.get("claims")
    if isinstance(claims, list):
        normalized["claims"] = [
            _normalize_claim(item) if isinstance(item, dict) else item for item in claims
        ]
    return normalized


def _normalize_entity(entity: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(entity)
    _copy_alias(normalized, "name", "entity")
    _copy_alias(normalized, "entity_type", "type")
    return normalized


def _normalize_claim(claim: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(claim)
    _copy_alias(normalized, "text", "claim", "statement")
    evidence = normalized.get("evidence")
    if isinstance(evidence, list):
        normalized["evidence"] = [
            _normalize_evidence(item) if isinstance(item, dict) else item for item in evidence
        ]
    return normalized


def _normalize_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(evidence)
    _copy_alias(normalized, "snippet", "quote", "text")
    return normalized


async def run_model_task(task: str, payload: dict[str, Any]) -> dict[str, Any]:
    config = ModelConfig.from_env()
    if config is None:
        return {"mode":"fallback","task":task,"result":deterministic_fallback(task,payload),"warning":"No model API configured; deterministic fallback was used."}
    endpoint = f"{config.base_url}/chat/completions"
    body={"model":config.model,"temperature":0.1,"response_format":{"type":"json_object"},"messages":[{"role":"system","content":SYSTEM_PROMPT},{"role":"user","content":json.dumps(model_request_payload(task,payload),ensure_ascii=False)}]}
    headers={"Authorization":f"Bearer {config.api_key}","Content-Type":"application/json"}
    try:
        async with asyncio.timeout(config.timeout_seconds):
            async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
                response=await client.post(endpoint,headers=headers,json=body); response.raise_for_status(); data=response.json()
    except TimeoutError as exc:
        raise httpx.ReadTimeout(
            f"Model request exceeded {config.timeout_seconds:g} second total deadline"
        ) from exc
    content=data["choices"][0]["message"]["content"]
    return {"mode":"api","task":task,"model":config.model,"result":normalize_model_result(task,json.loads(content))}


def deterministic_fallback(task: str, payload: dict[str, Any]) -> dict[str, Any]:
    if task == "normalize_event_title": return {"title":str(payload.get("title","Untitled event")).strip()[:160]}
    if task == "summarize_event":
        joined=" ".join(str(item) for item in payload.get("texts",[])); return {"summary":joined[:500]}
    if task == "extract_entities_locations": return {"entities":payload.get("seed_entities",[]),"locations":payload.get("seed_locations",[])}
    if task == "extract_claims_evidence": return {"claims":[],"warning":"Fallback does not invent claims."}
    if task == "draft_report": return {"title":payload.get("title","PLDR brief"),"sections":payload.get("sections",[])}
    return {"warning":f"Unknown task: {task}"}
