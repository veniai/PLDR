from __future__ import annotations

import asyncio
import json
import os
import weakref
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
Keep the result concise and within the limits stated in output_contract; never add extra candidates
when the strongest supported candidates are enough.
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
        "relevance": "relevant, uncertain, or not_relevant",
        "relevance_reason": "one concise Simplified Chinese sentence",
        "event": {
            "title": "string or null",
            "summary": "string or null",
            "event_time": "exact occurrence date/time wording copied from the source (a partial month/day is allowed), or null; never a publication date and never a normalized value absent from the source",
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
                        "paragraph_id": "the [Pnnn] label containing the snippet, or null",
                        "stance": "supports, contradicts, or context",
                        "strength": "number from 0 to 1",
                    }
                ],
            }
        ],
    },
    "synthesize_investigation": {
        "current_answer": "a concise Simplified Chinese topic-level answer",
        "groups": [
            {
                "title": "concise Simplified Chinese real-world event title",
                "summary": "one concise Simplified Chinese event summary",
                "event_time": "one exact event_time supplied by a source event, or null",
                "location_name": "one exact location_name supplied by a source event, or null",
                "source_event_ids": ["one or more supplied source_event_id values"],
                "findings": [
                    {
                        "text": "concise Simplified Chinese proposition, not a copied quote",
                        "evidence_ids": ["one or more supplied evidence_id values"],
                    }
                ],
            }
        ],
        "information_gaps": ["concise Simplified Chinese unanswered question"],
    },
}


_LOOP_LIMITERS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, tuple[int, asyncio.Semaphore]]" = weakref.WeakKeyDictionary()


def _model_limiter() -> asyncio.Semaphore:
    limit = max(1, int(os.getenv("LLM_MAX_CONCURRENCY", "1")))
    loop = asyncio.get_running_loop()
    current = _LOOP_LIMITERS.get(loop)
    if current is None or current[0] != limit:
        current = (limit, asyncio.Semaphore(limit))
        _LOOP_LIMITERS[loop] = current
    return current[1]


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
        _copy_alias(event, "event_time", "occurred_at", "start_at")
        # Provider aliases are normalized to one reviewed candidate field.
        # `published_at` is deliberately not an event-time alias: it describes
        # the source document and cannot establish when the event occurred.
        for alias in ("occurred_at", "start_at", "published_at"):
            event.pop(alias, None)
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


def parse_model_response(task: str, data: Any) -> dict[str, Any]:
    """Validate the provider envelope before trusting structured model output."""
    try:
        choice = data["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("Model response is missing the first message choice") from exc
    finish_reason = choice.get("finish_reason")
    if finish_reason == "length":
        raise ValueError(
            "Model output was truncated before the JSON result completed; "
            "the task will be retried"
        )
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Model response content is empty")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("Model response content is not valid JSON") from exc
    return normalize_model_result(task, parsed)


def model_http_error(response: httpx.Response) -> RuntimeError:
    """Return a bounded diagnostic without exposing request headers or input."""
    detail = ""
    try:
        payload = response.json()
    except (ValueError, TypeError):
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            code = error.get("code") or error.get("type")
            if isinstance(message, str):
                detail = message.strip()
            if code:
                detail = f"{code}: {detail}" if detail else str(code)
        elif isinstance(error, str):
            detail = error.strip()
        elif isinstance(payload.get("message"), str):
            detail = payload["message"].strip()
    suffix = f": {detail[:500]}" if detail else ""
    return RuntimeError(f"Model API returned HTTP {response.status_code}{suffix}")


async def run_model_task(task: str, payload: dict[str, Any]) -> dict[str, Any]:
    config = ModelConfig.from_env()
    if config is None:
        return {"mode":"fallback","task":task,"result":deterministic_fallback(task,payload),"warning":"No model API configured; deterministic fallback was used."}
    endpoint = f"{config.base_url}/chat/completions"
    body={"model":config.model,"temperature":0.1,"response_format":{"type":"json_object"},"messages":[{"role":"system","content":SYSTEM_PROMPT},{"role":"user","content":json.dumps(model_request_payload(task,payload),ensure_ascii=False)}]}
    if task == "extract_intake_candidates":
        body["max_tokens"] = int(os.getenv("LLM_EXTRACTION_MAX_TOKENS", "1400"))
    if task == "synthesize_investigation":
        body["max_tokens"] = int(os.getenv("LLM_SYNTHESIS_MAX_TOKENS", "2048"))
    # GLM 5.3 always reasons. A low budget is materially faster while retaining
    # exact-quote validation; providers that do not support it simply omit it.
    if "glm-5.3" in config.model.lower() or os.getenv("LLM_REASONING_EFFORT"):
        body["reasoning_effort"] = os.getenv("LLM_REASONING_EFFORT", "low")
    headers={"Authorization":f"Bearer {config.api_key}","Content-Type":"application/json"}
    try:
        async with _model_limiter():
            async with asyncio.timeout(config.timeout_seconds):
                async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
                    response=await client.post(endpoint,headers=headers,json=body)
                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError as exc:
                        raise model_http_error(response) from exc
                    data=response.json()
    except TimeoutError as exc:
        raise httpx.ReadTimeout(
            f"Model request exceeded {config.timeout_seconds:g} second total deadline"
        ) from exc
    return {"mode":"api","task":task,"model":config.model,"result":parse_model_response(task,data)}


def deterministic_fallback(task: str, payload: dict[str, Any]) -> dict[str, Any]:
    if task == "normalize_event_title": return {"title":str(payload.get("title","Untitled event")).strip()[:160]}
    if task == "summarize_event":
        joined=" ".join(str(item) for item in payload.get("texts",[])); return {"summary":joined[:500]}
    if task == "extract_entities_locations": return {"entities":payload.get("seed_entities",[]),"locations":payload.get("seed_locations",[])}
    if task == "extract_claims_evidence": return {"claims":[],"warning":"Fallback does not invent claims."}
    if task == "draft_report": return {"title":payload.get("title","PLDR brief"),"sections":payload.get("sections",[])}
    return {"warning":f"Unknown task: {task}"}
