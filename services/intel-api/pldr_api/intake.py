from __future__ import annotations

import base64
import html as html_lib
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Callable
from xml.etree import ElementTree

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .extraction import canonicalize_url, content_hash, extract_page, normalize_text
from .importers import fetch_public_text
from .llm import run_model_task
from .models import (
    Claim,
    Document,
    Entity,
    Event,
    EventDocument,
    EventEntity,
    Evidence,
    IntakeCandidate,
    IntakeItem,
    Snapshot,
    Source,
)
from .schemas import IntakeConfirmationRequest
from .security import validate_public_http_url


MAX_FILE_BYTES = 5 * 1024 * 1024
SUPPORTED_FILE_SUFFIXES = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".html": "text/html",
    ".htm": "text/html",
    ".pdf": "application/pdf",
}
UNKNOWN_TITLE = "[unknown title]"
UNKNOWN_DATETIME = datetime(1970, 1, 1, tzinfo=timezone.utc)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Invalid ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def new_intake_id(input_type: str) -> str:
    return f"int_{input_type[:4]}_{uuid.uuid4().hex[:16]}"


def _base_item(input_type: str, **values: Any) -> IntakeItem:
    now = utcnow()
    return IntakeItem(
        id=new_intake_id(input_type),
        input_type=input_type,
        status="parsed",
        created_at=now,
        updated_at=now,
        **values,
    )


def _failed_item(
    session: Session,
    input_type: str,
    error: Exception | str,
    **values: Any,
) -> IntakeItem:
    session.rollback()
    item = _base_item(input_type, **values)
    item.status = "failed"
    item.error = str(error)
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def _clean_known(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value


async def generate_candidates(session: Session, item: IntakeItem) -> IntakeItem:
    """Generate and persist candidate objects without touching formal tables."""
    for candidate in list(item.candidates):
        session.delete(candidate)
    session.flush()
    item.candidate_error = None
    item.candidate_relations = []
    payload = {
        "intake_item_id": item.id,
        "input_type": item.input_type,
        "known_fields": {
            "title": _clean_known(item.title),
            "source_description": _clean_known(item.source_description),
            "source_url": _clean_known(item.source_url),
            "published_at": iso(item.published_at),
        },
        "snapshot": item.extracted_snapshot,
        "output_contract": {
            "event": "one object; use null for unknown fields",
            "entities": "list; use [] when unknown",
            "claims": "list with evidence arrays; every evidence.snippet must be an exact snapshot substring",
        },
    }
    try:
        response = await run_model_task("extract_intake_candidates", payload)
        if response.get("mode") == "fallback":
            result = deterministic_candidate_result(item)
            mode = "fallback"
            model_name = None
        else:
            result = response.get("result")
            if not isinstance(result, dict):
                raise ValueError("Model response result must be a JSON object")
            mode = "api"
            model_name = response.get("model") or os.getenv("LLM_MODEL_NAME", "configured-model")
        _store_candidates(session, item, result, mode, model_name)
        item.status = "candidate_ready"
    except Exception as exc:
        item.status = "generation_failed"
        item.candidate_mode = "failed"
        item.candidate_error = str(exc)
    session.commit()
    session.refresh(item)
    return item


def deterministic_candidate_result(item: IntakeItem) -> dict[str, Any]:
    snapshot = item.extracted_snapshot
    sentences = [part.strip() for part in re.split(r"(?<=[.!?。！？])\s+", snapshot) if part.strip()]
    quote = next((part for part in sentences if len(part) >= 30), snapshot[:240].strip())
    return {
        "event": {
            "title": _clean_known(item.title),
            "summary": snapshot[:500],
            "event_time": iso(item.published_at),
            "location_name": None,
        },
        "entities": [],
        "claims": [
            {
                "text": quote,
                "uncertainty": "Deterministic fallback quotes the snapshot; an analyst must interpret it.",
                "evidence": [{"snippet": quote, "stance": "context", "strength": 0.5}],
            }
        ],
    }


def _candidate_machine_data(data: dict[str, Any], source_mode: str) -> dict[str, Any]:
    return {"fields": data, "source_mode": source_mode, "status": "machine-candidate"}


def _store_candidates(
    session: Session,
    item: IntakeItem,
    result: dict[str, Any],
    mode: str,
    model_name: str | None,
) -> None:
    item.candidate_mode = mode
    item.candidate_model = model_name
    relations: list[dict[str, str]] = []
    event = result.get("event")
    if not isinstance(event, dict):
        event = {}
    else:
        event = dict(event)
        event_time = event.get("event_time")
        if (
            event_time is not None
            and event_time != iso(item.published_at)
            and event_time not in item.extracted_snapshot
        ):
            event["event_time"] = None
    event_key = "event"
    session.add(
        IntakeCandidate(
            id=f"{item.id}:event",
            item_id=item.id,
            candidate_key=event_key,
            object_type="event",
            source_mode=mode,
            machine_data=_candidate_machine_data(event, mode),
        )
    )
    raw_entities = result.get("entities") if isinstance(result.get("entities"), list) else []
    for idx, entity in enumerate(raw_entities):
        if not isinstance(entity, dict):
            continue
        entity = dict(entity)
        name = entity.get("name")
        if not isinstance(name, str) or not name.strip() or name not in item.extracted_snapshot:
            continue
        key = f"entity:{idx + 1}"
        session.add(
            IntakeCandidate(
                id=f"{item.id}:{key}",
                item_id=item.id,
                candidate_key=key,
                object_type="entity",
                source_mode=mode,
                machine_data=_candidate_machine_data(entity, mode),
            )
        )
        relations.append({"type": "event_entity", "from": key, "to": event_key})

    claims = result.get("claims")
    if not isinstance(claims, list):
        claims = []
    evidence_index = 0
    for claim_idx, claim in enumerate(claims):
        if not isinstance(claim, dict):
            continue
        claim_key = f"claim:{claim_idx + 1}"
        claim_fields = {
            "text": claim.get("text"),
            "uncertainty": claim.get("uncertainty"),
            "temporal_scope": claim.get("temporal_scope"),
        }
        session.add(
            IntakeCandidate(
                id=f"{item.id}:{claim_key}",
                item_id=item.id,
                candidate_key=claim_key,
                object_type="claim",
                source_mode=mode,
                machine_data=_candidate_machine_data(claim_fields, mode),
            )
        )
        relations.append({"type": "event_claim", "from": claim_key, "to": event_key})
        evidence_items = claim.get("evidence")
        if not isinstance(evidence_items, list):
            evidence_items = []
        for evidence in evidence_items:
            if not isinstance(evidence, dict):
                continue
            evidence_index += 1
            key = f"evidence:{evidence_index}"
            snippet = evidence.get("snippet")
            validation_error = None
            start_offset = end_offset = -1
            if not isinstance(snippet, str) or not snippet:
                validation_error = "Evidence snippet is missing"
            else:
                start = item.extracted_snapshot.find(snippet)
                if start < 0:
                    validation_error = "Evidence snippet is not an exact substring of the complete snapshot"
                else:
                    start_offset, end_offset = start, start + len(snippet)
            fields = {
                "snippet": snippet if isinstance(snippet, str) else "",
                "start_offset": start_offset,
                "end_offset": end_offset,
                "stance": evidence.get("stance") or "context",
                "strength": evidence.get("strength", 0.5),
            }
            session.add(
                IntakeCandidate(
                    id=f"{item.id}:{key}",
                    item_id=item.id,
                    candidate_key=key,
                    object_type="evidence",
                    source_mode=mode,
                    machine_data=_candidate_machine_data(fields, mode),
                    validation_error=validation_error,
                )
            )
            relations.append(
                {
                    "type": "claim_evidence",
                    "from": key,
                    "to": claim_key,
                    "valid": validation_error is None,
                }
            )
    item.candidate_relations = relations
    session.flush()


async def submit_web_intake(
    session: Session,
    url: str,
    source_name: str | None,
    title: str | None,
    html: str | None,
    language: str,
    input_type: str = "web",
) -> IntakeItem:
    requested_url = str(url)
    common = {
        "source_url": requested_url,
        "language": language,
        "source_description": (source_name or "").strip(),
    }
    try:
        canonical_url = canonicalize_url(requested_url)
        validate_public_http_url(canonical_url, resolve=html is None)
        resolved_url = canonical_url
        fetched_at = utcnow()
        if html is None:
            resolved_url, html = await fetch_public_text(canonical_url)
            resolved_url = canonicalize_url(resolved_url)
            canonical_url = resolved_url
        if not html or not html.strip():
            raise ValueError("Fetched page is empty")
        page = extract_page(html, fallback_title=title or "")
        if len(page.body) < 40:
            raise ValueError("Extracted page body is too short")
        known_title = (title or page.title or "").strip() or None
        item = _base_item(
            input_type,
            source_description=(source_name or canonical_url.split("//", 1)[-1].split("/", 1)[0]).strip(),
            source_url=requested_url,
            canonical_url=canonical_url,
            title=known_title,
            language=language,
            raw_snapshot=html,
            raw_hash=sha256_text(html),
            extracted_snapshot=page.body,
            extracted_hash=content_hash(page.body),
            review={"material": {"resolved_url": resolved_url, "fetched_at": iso(fetched_at)}},
        )
        session.add(item)
        session.commit()
        session.refresh(item)
        return await generate_candidates(session, item)
    except Exception as exc:
        failure_html = html or ""
        failure_text = normalize_text(failure_html)
        return _failed_item(
            session,
            input_type,
            exc,
            raw_snapshot=failure_html,
            raw_hash=sha256_text(failure_html) if failure_html else "",
            extracted_snapshot=failure_text,
            extracted_hash=content_hash(failure_text) if failure_html else "",
            **common,
        )


async def submit_text_intake(session: Session, request: Any) -> IntakeItem:
    common = {
        "source_description": request.source_description.strip(),
        "title": _clean_known(request.title),
        "language": request.language,
    }
    try:
        text = normalize_text(request.text)
        if len(text) < 10:
            raise ValueError("Pasted text is empty or too short")
        if len(request.source_description.strip()) < 3:
            raise ValueError("A source description of at least 3 characters is required")
        published_at = parse_datetime(request.published_at)
        item = _base_item(
            "text",
            published_at=published_at,
            raw_snapshot=request.text,
            raw_hash=sha256_text(request.text),
            extracted_snapshot=text,
            extracted_hash=content_hash(text),
            review={"material": {"input_method": "browser-paste"}},
            **common,
        )
        session.add(item)
        session.commit()
        session.refresh(item)
        return await generate_candidates(session, item)
    except Exception as exc:
        normalized_failure = normalize_text(request.text)
        return _failed_item(
            session,
            "text",
            exc,
            raw_snapshot=request.text,
            raw_hash=sha256_text(request.text),
            extracted_snapshot=normalized_failure,
            extracted_hash=content_hash(normalized_failure),
            **common,
        )


def _extract_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - only reached in a degraded installation
        raise ValueError("PDF extraction dependency is unavailable") from exc
    try:
        reader = PdfReader(BytesIO(data), strict=True)
        if getattr(reader, "is_encrypted", False):
            raise ValueError("Encrypted PDFs are not supported")
        text = normalize_text(" ".join(page.extract_text() or "" for page in reader.pages))
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("PDF file is damaged or cannot be parsed") from exc
    if len(text) < 10:
        raise ValueError("PDF contains no extractable text")
    return text


async def submit_file_intake(
    session: Session,
    upload: Any,
    source_description: str,
    language: str,
) -> IntakeItem:
    filename = Path(upload.filename or "").name
    suffix = Path(filename).suffix.lower()
    common = {"source_description": source_description.strip(), "language": language}
    failure_values: dict[str, Any] = {
        "original_filename": filename or None,
        "media_type": (getattr(upload, "content_type", "") or "application/octet-stream")[:120],
        "size_bytes": None,
        "raw_snapshot": "",
        "raw_hash": "",
        "review": {"material": {"raw_encoding": "none"}},
    }
    try:
        data = await upload.read(MAX_FILE_BYTES + 1)
        failure_values.update(
            size_bytes=len(data),
            raw_snapshot=base64.b64encode(data).decode("ascii"),
            raw_hash=sha256_bytes(data),
            review={"material": {"raw_encoding": "base64"}},
        )
        if not filename or suffix not in SUPPORTED_FILE_SUFFIXES:
            raise ValueError(f"Unsupported file type; allowed: {', '.join(sorted(SUPPORTED_FILE_SUFFIXES))}")
        if not source_description or len(source_description.strip()) < 3:
            raise ValueError("A source description of at least 3 characters is required")
        if len(data) > MAX_FILE_BYTES:
            raise ValueError(f"File exceeds the {MAX_FILE_BYTES // (1024 * 1024)} MiB limit")
        if not data:
            raise ValueError("File is empty")
        media_type = SUPPORTED_FILE_SUFFIXES[suffix]
        failure_values["media_type"] = media_type
        if suffix == ".pdf":
            extracted = _extract_pdf(data)
            raw_snapshot = failure_values["raw_snapshot"]
            raw_hash = failure_values["raw_hash"]
            raw_encoding = "base64"
        else:
            try:
                raw_text = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("Text file is not valid UTF-8") from exc
            raw_snapshot = raw_text
            raw_hash = sha256_text(raw_text)
            raw_encoding = "utf-8"
            failure_values.update(
                raw_snapshot=raw_snapshot,
                raw_hash=raw_hash,
                review={"material": {"raw_encoding": raw_encoding}},
            )
            if suffix in {".html", ".htm"}:
                page = extract_page(raw_text)
                extracted = page.body
            else:
                extracted = normalize_text(raw_text)
        if len(extracted) < 10:
            raise ValueError("File contains no extractable text")
        item = _base_item(
            "file",
            original_filename=filename,
            media_type=media_type,
            size_bytes=len(data),
            raw_snapshot=raw_snapshot,
            raw_hash=raw_hash,
            extracted_snapshot=extracted,
            extracted_hash=content_hash(extracted),
            review={"material": {"raw_encoding": raw_encoding}},
            **common,
        )
        session.add(item)
        session.commit()
        session.refresh(item)
        return await generate_candidates(session, item)
    except Exception as exc:
        return _failed_item(
            session,
            "file",
            exc,
            **failure_values,
            **common,
        )


def _rss_value(node: ElementTree.Element | None, default: str = "") -> str:
    if node is None:
        return default
    return normalize_text("".join(node.itertext()))


def _rss_find(node: ElementTree.Element, *paths: str) -> ElementTree.Element | None:
    for path in paths:
        found = node.find(path)
        if found is not None:
            return found
    return None


async def submit_rss_intake(
    session: Session,
    url: str | None,
    xml: str | None,
    source_name: str,
    language: str,
) -> list[IntakeItem]:
    try:
        if not xml:
            if not url:
                raise ValueError("RSS url or xml is required")
            _, xml = await fetch_public_text(url)
        try:
            root = ElementTree.fromstring(xml)
        except ElementTree.ParseError as exc:
            raise ValueError("RSS XML is malformed") from exc
        items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
        if not items:
            raise ValueError("RSS feed contains no items")
        results: list[IntakeItem] = []
        for node in items[:50]:
            title_node = _rss_find(node, "title", "{http://www.w3.org/2005/Atom}title")
            link_node = _rss_find(node, "link", "{http://www.w3.org/2005/Atom}link")
            description_node = _rss_find(
                node,
                "description",
                "summary",
                "{http://www.w3.org/2005/Atom}summary",
            )
            title = _rss_value(title_node, "Untitled RSS item")
            link = _rss_value(link_node) or (link_node.attrib.get("href", "") if link_node is not None else "")
            description = _rss_value(description_node, title)
            if not link:
                digest = hashlib.sha1(title.encode("utf-8")).hexdigest()[:12]
                link = f"https://example.org/.well-known/pldr-rss/{digest}"
            synthetic_html = (
                "<html><head><title>"
                + html_lib.escape(title)
                + "</title></head><body><article><p>"
                + html_lib.escape(description)
                + "</p></article></body></html>"
            )
            results.append(
                await submit_web_intake(
                    session,
                    link,
                    source_name,
                    title,
                    synthetic_html,
                    language,
                    input_type="rss",
                )
            )
        return results
    except Exception as exc:
        failure_xml = xml or ""
        failure_text = normalize_text(failure_xml)
        return [
            _failed_item(
                session,
                "rss",
                exc,
                source_url=url,
                source_description=source_name,
                language=language,
                raw_snapshot=failure_xml,
                raw_hash=sha256_text(failure_xml) if failure_xml else "",
                extracted_snapshot=failure_text,
                extracted_hash=content_hash(failure_text) if failure_xml else "",
            )
        ]


def get_intake_item(session: Session, item_id: str) -> IntakeItem | None:
    return session.scalar(
        select(IntakeItem)
        .where(IntakeItem.id == item_id)
        .options(selectinload(IntakeItem.candidates))
    )


def serialize_candidate(candidate: IntakeCandidate) -> dict[str, Any]:
    return {
        "id": candidate.id,
        "candidate_key": candidate.candidate_key,
        "object_type": candidate.object_type,
        "source_mode": candidate.source_mode,
        "machine": candidate.machine_data,
        "human": candidate.human_data,
        "validation_error": candidate.validation_error,
        "disposition": candidate.disposition,
        "reviewed_at": iso(candidate.reviewed_at),
        "final_object_id": candidate.final_object_id,
    }


def serialize_intake(item: IntakeItem) -> dict[str, Any]:
    candidates = [serialize_candidate(candidate) for candidate in item.candidates]
    return {
        "id": item.id,
        "input_type": item.input_type,
        "status": item.status,
        "error": item.error,
        "source": {
            "description": item.source_description or None,
            "url": item.source_url,
            "canonical_url": item.canonical_url,
            "known": bool(item.source_description or item.canonical_url),
        },
        "title": _clean_known(item.title),
        "published_at": iso(item.published_at),
        "language": item.language,
        "file": {
            "name": item.original_filename,
            "media_type": item.media_type,
            "size_bytes": item.size_bytes,
        },
        "created_at": iso(item.created_at),
        "updated_at": iso(item.updated_at),
        "material": {
            "raw_hash": item.raw_hash or None,
            "extracted_hash": item.extracted_hash or None,
            "raw_snapshot": item.raw_snapshot,
            "extracted_snapshot": item.extracted_snapshot,
            **item.review.get("material", {}),
        },
        "candidate_generation": {
            "mode": item.candidate_mode,
            "model": item.candidate_model,
            "error": item.candidate_error,
            "relations": item.candidate_relations,
        },
        "candidates": candidates,
        "review": item.review,
        "confirmation_result": item.confirmation_result or None,
        "disposition": item.disposition,
        "reviewed_by": item.reviewed_by,
        "reviewed_at": iso(item.reviewed_at),
        "rejection_reason": item.rejection_reason,
        "final_object_ids": {
            "event": item.final_event_id,
            "document": item.final_document_id,
            "snapshot": item.final_snapshot_id,
        },
    }


def _candidate_map(item: IntakeItem) -> dict[str, IntakeCandidate]:
    return {candidate.candidate_key: candidate for candidate in item.candidates}


def _confirmation_fingerprint(request: IntakeConfirmationRequest) -> str:
    payload = json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _machine_event(item: IntakeItem) -> dict[str, Any]:
    candidate = _candidate_map(item).get("event")
    return candidate.machine_data.get("fields", {}) if candidate else {}


def _difference(machine: dict[str, Any], human: dict[str, Any]) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    for key, value in human.items():
        if machine.get(key) != value:
            changes[key] = {"machine": machine.get(key), "human": value}
    return changes


def validate_confirmation(
    session: Session,
    item: IntakeItem,
    request: IntakeConfirmationRequest,
) -> list[str]:
    errors: list[str] = []
    if item.status != "candidate_ready":
        errors.append(f"Item is not candidate_ready (current status: {item.status})")
    if request.disposition == "merge":
        if not request.merge_event_id:
            errors.append("merge disposition requires merge_event_id")
        elif session.get(Event, request.merge_event_id) is None:
            errors.append("Selected merge event does not exist")
    if request.disposition != "merge" and not request.event.title.strip():
        errors.append("A known event title is required to create or modify a new formal event")
    if request.disposition == "modify":
        machine_event = _machine_event(item)
        if not _difference(machine_event, request.event.model_dump(mode="json")):
            errors.append("Modify disposition must contain an explicit human change")

    candidates = _candidate_map(item)
    selected_claims = {decision.candidate_key for decision in request.claims if decision.action != "exclude"}
    selected_evidence = {decision.candidate_key for decision in request.evidence if decision.action == "include"}
    final_event_id = request.merge_event_id if request.disposition == "merge" else None
    if not selected_claims:
        errors.append("At least one claim candidate must be selected")
    if not selected_evidence:
        errors.append("At least one evidence candidate must be selected")

    for decision in request.entities:
        candidate = candidates.get(decision.candidate_key)
        if candidate is None or candidate.object_type != "entity":
            errors.append(f"Unknown entity candidate: {decision.candidate_key}")
            continue
        if decision.action == "merge":
            if not decision.merge_entity_id or session.get(Entity, decision.merge_entity_id) is None:
                errors.append(f"Invalid entity merge target for {decision.candidate_key}")
        elif decision.action == "create" and not decision.name.strip():
            errors.append(f"Entity name is required for {decision.candidate_key}")

    for decision in request.claims:
        candidate = candidates.get(decision.candidate_key)
        if candidate is None or candidate.object_type != "claim":
            errors.append(f"Unknown claim candidate: {decision.candidate_key}")
            continue
        if decision.action == "exclude":
            continue
        if not decision.text.strip():
            errors.append(f"Claim text is required for {decision.candidate_key}")
        if decision.action == "merge":
            claim_target = session.get(Claim, decision.merge_claim_id) if decision.merge_claim_id else None
            if claim_target is None:
                errors.append(f"Invalid claim merge target for {decision.candidate_key}")
            elif final_event_id is None or claim_target.event_id != final_event_id:
                errors.append(f"Claim merge target for {decision.candidate_key} must belong to the selected final event")

    relation_claim = {
        relation["from"]: relation["to"]
        for relation in item.candidate_relations
        if relation["type"] == "claim_evidence"
    }
    for decision in request.evidence:
        candidate = candidates.get(decision.candidate_key)
        if candidate is None or candidate.object_type != "evidence":
            errors.append(f"Unknown evidence candidate: {decision.candidate_key}")
            continue
        if decision.action == "exclude":
            continue
        if candidate.validation_error:
            errors.append(f"{decision.candidate_key}: {candidate.validation_error}")
            continue
        if relation_claim.get(decision.candidate_key) not in selected_claims:
            errors.append(f"{decision.candidate_key} is selected without its parent claim")
        start = item.extracted_snapshot.find(decision.snippet)
        end = start + len(decision.snippet)
        if start < 0 or item.extracted_snapshot[start:end] != decision.snippet:
            errors.append(f"{decision.candidate_key} cannot be precisely located in the complete snapshot")
    return errors


def build_confirmation_preview(
    session: Session,
    item: IntakeItem,
    request: IntakeConfirmationRequest,
) -> dict[str, Any]:
    errors = validate_confirmation(session, item, request)
    merge_event = session.get(Event, request.merge_event_id) if request.merge_event_id else None
    return {
        "confirmable": not errors,
        "errors": errors,
        "disposition": request.disposition,
        "formal": {
            "source": {
                "name": item.source_description or item.canonical_url or "Unknown source",
                "url": item.canonical_url or item.source_url,
                "known": bool(item.source_description or item.canonical_url),
            },
            "document": {
                "title": item.title or UNKNOWN_TITLE,
                "title_known": bool(item.title),
                "published_at": iso(item.published_at),
                "content_hash": item.extracted_hash,
            },
            "snapshot": {"content_hash": item.extracted_hash, "length": len(item.extracted_snapshot)},
            "event": {
                "id": merge_event.id if merge_event else None,
                "title": merge_event.title if merge_event else request.event.title,
                "summary": merge_event.summary if merge_event else request.event.summary,
                "action": "merge" if merge_event else ("create" if request.disposition == "create" else "create-modified"),
            },
            "entities": [
                {
                    "action": decision.action,
                    "name": decision.name,
                    "merge_entity_id": decision.merge_entity_id,
                }
                for decision in request.entities
                if decision.action != "exclude"
            ],
            "claims": [
                {
                    "action": decision.action,
                    "text": decision.text,
                    "merge_claim_id": decision.merge_claim_id,
                }
                for decision in request.claims
                if decision.action != "exclude"
            ],
            "evidence": [
                {
                    "snippet": decision.snippet,
                    "stance": decision.stance,
                    "snapshot_trace": {
                        "start_offset": item.extracted_snapshot.find(decision.snippet),
                        "end_offset": item.extracted_snapshot.find(decision.snippet) + len(decision.snippet),
                    },
                }
                for decision in request.evidence
                if decision.action == "include"
            ],
        },
        "trace": {
            "intake_item_id": item.id,
            "machine_candidate_source": item.candidate_mode,
            "human_disposition": request.disposition,
            "analyst": request.analyst,
        },
    }


def _origin(item: IntakeItem) -> tuple[str, str]:
    if item.canonical_url or item.source_url:
        url = item.canonical_url or item.source_url or ""
        host = url.split("//", 1)[-1].split("/", 1)[0] or "unknown"
        return url, host
    identity = item.source_description.strip() or item.raw_hash or item.id
    return f"pldr:unknown-source:{hashlib.sha1(identity.encode('utf-8')).hexdigest()[:20]}", hashlib.sha1(identity.encode("utf-8")).hexdigest()[:20]


def _get_or_create_intake_source(session: Session, item: IntakeItem) -> Source:
    url, group = _origin(item)
    name = item.source_description.strip() or (group if item.canonical_url or item.source_url else "Unknown source")
    source_id = "src_intake_" + hashlib.sha1(f"{name}:{group}".encode("utf-8")).hexdigest()[:16]
    source = session.get(Source, source_id)
    if source is None:
        base_url = url if url.startswith("pldr:") else "/".join(url.split("/", 3)[:3])
        source = Source(
            id=source_id,
            name=name,
            base_url=base_url,
            country="",
            language=item.language,
            source_type=f"intake-{item.input_type}",
            reliability_tier=4,
            independence_group=f"intake:{group}",
            status="healthy",
            last_success_at=utcnow(),
        )
        session.add(source)
        session.flush()
    return source


def _create_formal_document(session: Session, item: IntakeItem) -> Document:
    canonical_url = item.canonical_url or item.source_url or f"pldr:intake/{item.id}"
    existing = session.scalar(select(Document).where(Document.canonical_url == canonical_url))
    if existing is not None:
        if existing.content_hash != item.extracted_hash:
            raise ValueError("Canonical URL already has a different formal snapshot; resolve the conflict before confirmation")
        if existing.body != item.extracted_snapshot:
            raise ValueError("Canonical URL snapshot body differs from the submitted extracted text")
        # A repeated submission of the same canonical URL and snapshot must associate the
        # existing formal provenance rather than create an orphan Source. The intake item
        # remains the durable trace from this review decision back to that Document.
        snapshot = session.scalar(
            select(Snapshot)
            .where(Snapshot.document_id == existing.id)
            .order_by(Snapshot.captured_at.desc())
            .limit(1)
        )
        if snapshot is None or snapshot.excerpt != item.extracted_snapshot:
            snapshot_id = "snap_intake_" + hashlib.sha1(f"{existing.id}:{item.id}".encode("utf-8")).hexdigest()[:16]
            snapshot = Snapshot(
                id=snapshot_id,
                document_id=existing.id,
                captured_at=item.created_at,
                content_hash=item.extracted_hash,
                excerpt=item.extracted_snapshot,
                storage_path="inline-intake-reused",
            )
            session.add(snapshot)
            session.flush()
        reuse_metadata = dict(existing.metadata_json or {})
        intake_ids = list(reuse_metadata.get("intake_item_ids", []))
        original_intake_id = reuse_metadata.get("intake_item_id")
        if original_intake_id is not None and original_intake_id not in intake_ids:
            intake_ids.insert(0, original_intake_id)
        if item.id not in intake_ids:
            intake_ids.append(item.id)
        reuse_metadata["intake_item_ids"] = intake_ids
        existing.metadata_json = reuse_metadata
        item.final_document_id = existing.id
        item.final_snapshot_id = snapshot.id
        return existing
    source = _get_or_create_intake_source(session, item)
    document_id = "doc_intake_" + hashlib.sha1(f"{item.id}:{item.extracted_hash}".encode("utf-8")).hexdigest()[:14]
    duplicate = session.scalar(
        select(Document)
        .where(Document.content_hash == item.extracted_hash)
        .order_by(Document.fetched_at.asc())
    )
    metadata = {
        "intake_item_id": item.id,
        "input_type": item.input_type,
        "title_known": bool(item.title),
        "published_at_known": item.published_at is not None,
        "source_description": item.source_description,
        "raw_hash": item.raw_hash,
        "confirmation_stage": "P0.3-human-confirmed",
        "intake_item_ids": [item.id],
    }
    if duplicate is not None:
        metadata["duplicate_of_document_id"] = duplicate.id
    document = Document(
        id=document_id,
        source_id=source.id,
        canonical_url=canonical_url,
        title=item.title or UNKNOWN_TITLE,
        body=item.extracted_snapshot,
        published_at=item.published_at or UNKNOWN_DATETIME,
        fetched_at=item.created_at,
        language=item.language,
        content_hash=item.extracted_hash,
        upstream_story_id="",
        is_cached=True,
        metadata_json=metadata,
    )
    session.add(document)
    session.flush()
    snapshot_id = "snap_intake_" + hashlib.sha1(document.id.encode("utf-8")).hexdigest()[:16]
    session.add(
        Snapshot(
            id=snapshot_id,
            document_id=document.id,
            captured_at=item.created_at,
            content_hash=item.extracted_hash,
            excerpt=item.extracted_snapshot,
            storage_path="inline-intake",
        )
    )
    session.flush()
    item.final_document_id = document.id
    item.final_snapshot_id = snapshot_id
    return document


def _set_candidate_result(
    item: IntakeItem,
    candidate_key: str,
    disposition: str,
    human_data: dict[str, Any],
    final_object_id: str | None,
) -> None:
    candidate = _candidate_map(item).get(candidate_key)
    if candidate is None:
        return
    candidate.disposition = disposition
    candidate.human_data = human_data
    candidate.reviewed_at = utcnow()
    candidate.final_object_id = final_object_id


def confirm_intake(
    session: Session,
    item: IntakeItem,
    request: IntakeConfirmationRequest,
    *,
    failure_hook: Callable[[], None] | None = None,
) -> tuple[IntakeItem, dict[str, Any], bool]:
    """Atomically promote a reviewed submission. The bool says whether this call created objects."""
    fingerprint = _confirmation_fingerprint(request)
    if item.status == "confirmed":
        if item.confirmation_fingerprint != fingerprint:
            raise ValueError("Item is already confirmed with a different review decision")
        return item, item.confirmation_result, False
    errors = validate_confirmation(session, item, request)
    if errors:
        raise ValueError("; ".join(errors))

    document = _create_formal_document(session, item)
    if request.merge_event_id:
        event = session.get(Event, request.merge_event_id)
        if event is None:
            raise ValueError("Selected merge event no longer exists")
    else:
        event = Event(
            id="evt_intake_" + uuid.uuid4().hex[:16],
            title=request.event.title.strip(),
            summary=request.event.summary.strip() or "Analyst-confirmed intake material.",
            event_type=request.event.event_type,
            start_at=parse_datetime(request.event.start_at) or item.published_at or UNKNOWN_DATETIME,
            end_at=None,
            latitude=None,
            longitude=None,
            location_name=request.event.location_name if request.event.location_name != "Unknown" else "",
            importance=request.event.importance,
            status="confirmed",
            confidence=0.5,
            metadata_json={
                "intake_item_id": item.id,
                "start_at_known": bool(request.event.start_at or item.published_at),
                "confirmation_stage": "P0.3-human-confirmed",
            },
        )
        session.add(event)
        session.flush()
    event.updated_at = utcnow()
    if not any(link.document_id == document.id for link in event.document_links):
        session.add(EventDocument(event_id=event.id, document_id=document.id, relevance=1.0))

    candidates = _candidate_map(item)
    entity_ids: dict[str, str] = {}
    for decision in request.entities:
        if decision.action == "exclude":
            _set_candidate_result(item, decision.candidate_key, "excluded", decision.model_dump(mode="json"), None)
            continue
        if decision.action == "merge":
            entity = session.get(Entity, decision.merge_entity_id)
            if entity is None:
                raise ValueError(f"Entity merge target missing: {decision.merge_entity_id}")
        else:
            entity = Entity(
                id="ent_intake_" + uuid.uuid4().hex[:16],
                name=decision.name.strip(),
                entity_type=decision.entity_type,
                aliases=decision.aliases,
            )
            session.add(entity)
            session.flush()
        if not any(link.entity_id == entity.id for link in event.entity_links):
            session.add(EventEntity(event_id=event.id, entity_id=entity.id, role=decision.role))
        entity_ids[decision.candidate_key] = entity.id
        _set_candidate_result(item, decision.candidate_key, decision.action, decision.model_dump(mode="json"), entity.id)

    claim_ids: dict[str, str] = {}
    for decision in request.claims:
        if decision.action == "exclude":
            _set_candidate_result(item, decision.candidate_key, "excluded", decision.model_dump(mode="json"), None)
            continue
        if decision.action == "merge":
            claim = session.get(Claim, decision.merge_claim_id)
            if claim is None:
                raise ValueError(f"Claim merge target missing: {decision.merge_claim_id}")
            if claim.event_id != event.id:
                raise ValueError("Claim merge target must belong to the selected final event")
        else:
            claim = Claim(
                id="clm_intake_" + uuid.uuid4().hex[:16],
                event_id=event.id,
                text=decision.text.strip(),
                status=decision.status,
                confidence=decision.confidence,
                origin="human-confirmed",
                temporal_scope=decision.temporal_scope,
            )
            session.add(claim)
            session.flush()
        claim_ids[decision.candidate_key] = claim.id
        _set_candidate_result(item, decision.candidate_key, decision.action, decision.model_dump(mode="json"), claim.id)

    relation_claim = {
        relation["from"]: relation["to"]
        for relation in item.candidate_relations
        if relation["type"] == "claim_evidence"
    }
    evidence_ids: dict[str, str] = {}
    for decision in request.evidence:
        if decision.action == "exclude":
            _set_candidate_result(item, decision.candidate_key, "excluded", decision.model_dump(mode="json"), None)
            continue
        start = item.extracted_snapshot.find(decision.snippet)
        end = start + len(decision.snippet)
        if start < 0 or item.extracted_snapshot[start:end] != decision.snippet:
            raise ValueError(f"Evidence {decision.candidate_key} failed complete-snapshot validation")
        claim_id = claim_ids.get(relation_claim.get(decision.candidate_key, ""))
        if claim_id is None:
            raise ValueError(f"Evidence {decision.candidate_key} has no selected parent claim")
        evidence_id = "evd_intake_" + uuid.uuid4().hex[:16]
        session.add(
            Evidence(
                id=evidence_id,
                claim_id=claim_id,
                document_id=document.id,
                snapshot_id=item.final_snapshot_id,
                snippet=decision.snippet,
                start_offset=start,
                end_offset=end,
                stance=decision.stance,
                strength=decision.strength,
                note=decision.note
                or "Human-confirmed from isolated intake candidate; machine candidate retained in intake.",
            )
        )
        evidence_ids[decision.candidate_key] = evidence_id
        _set_candidate_result(item, decision.candidate_key, "included", decision.model_dump(mode="json"), evidence_id)

    _set_candidate_result(
        item,
        "event",
        request.disposition,
        request.event.model_dump(mode="json"),
        event.id,
    )
    if failure_hook is not None:
        failure_hook()

    machine_event = _machine_event(item)
    differences = {
        "event": _difference(machine_event, request.event.model_dump(mode="json")),
    }
    result = {
        "status": "confirmed",
        "disposition": request.disposition,
        "analyst": request.analyst,
        "confirmed_at": iso(utcnow()),
        "fingerprint": fingerprint,
        "formal_object_ids": {
            "source": document.source_id,
            "document": document.id,
            "snapshot": item.final_snapshot_id,
            "event": event.id,
            "entities": sorted(entity_ids.values()),
            "claims": sorted(claim_ids.values()),
            "evidence": sorted(evidence_ids.values()),
        },
        "human_changes": differences,
    }
    item.status = "confirmed"
    item.error = None
    item.disposition = request.disposition
    item.reviewed_by = request.analyst
    item.reviewed_at = utcnow()
    item.confirmation_fingerprint = fingerprint
    item.confirmation_result = result
    item.final_event_id = event.id
    item.review["confirmation"] = {
        "request": request.model_dump(mode="json"),
        "machine_event": machine_event,
        "differences": differences,
    }
    session.commit()
    session.refresh(item)
    return item, result, True


def reject_intake(session: Session, item: IntakeItem, analyst: str, reason: str) -> IntakeItem:
    if item.status == "confirmed":
        raise ValueError("A confirmed item cannot be rejected; preserve its history")
    now = utcnow()
    item.status = "rejected"
    item.disposition = "reject"
    item.reviewed_by = analyst
    item.reviewed_at = now
    item.rejection_reason = reason
    item.review["rejection"] = {"analyst": analyst, "reason": reason, "reviewed_at": iso(now)}
    for candidate in item.candidates:
        candidate.disposition = "rejected"
        candidate.reviewed_at = now
    session.commit()
    session.refresh(item)
    return item


def cancel_intake(session: Session, item: IntakeItem, analyst: str, reason: str) -> IntakeItem:
    if item.status == "confirmed":
        raise ValueError("A confirmed item cannot be cancelled; preserve its history")
    now = utcnow()
    item.status = "cancelled"
    item.disposition = "cancel"
    item.reviewed_by = analyst
    item.reviewed_at = now
    item.error = reason
    item.review["cancellation"] = {"analyst": analyst, "reason": reason, "reviewed_at": iso(now)}
    for candidate in item.candidates:
        candidate.disposition = "cancelled"
        candidate.reviewed_at = now
    session.commit()
    session.refresh(item)
    return item
