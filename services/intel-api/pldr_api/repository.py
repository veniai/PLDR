from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .models import Assessment, Claim, Document, Event, EventDocument, EventEntity, Evidence, Source


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def event_query():
    return select(Event).options(
        selectinload(Event.document_links).selectinload(EventDocument.document).selectinload(Document.source),
        selectinload(Event.entity_links).selectinload(EventEntity.entity),
        selectinload(Event.claims)
        .selectinload(Claim.evidence_items)
        .selectinload(Evidence.document)
        .selectinload(Document.source),
        selectinload(Event.assessments),
    )


def get_events(session: Session) -> list[Event]:
    return list(session.scalars(event_query().order_by(Event.start_at.asc())).unique())


def get_event(session: Session, event_id: str) -> Event | None:
    return session.scalars(event_query().where(Event.id == event_id)).unique().one_or_none()


def serialize_event_card(event: Event) -> dict[str, Any]:
    documents = [link.document for link in event.document_links]
    event_metadata = event.metadata_json or {}
    groups = {d.source.independence_group for d in documents}
    languages = sorted({d.language for d in documents})
    source_types = Counter(d.source.source_type for d in documents)
    entities = [
        {"id": link.entity.id, "name": link.entity.name, "type": link.entity.entity_type, "role": link.role}
        for link in event.entity_links
    ]
    claim_counts = Counter(c.status for c in event.claims)
    return {
        "id": event.id,
        "title": event.title,
        "summary": event.summary,
        "event_type": event.event_type,
        "start_at": None if event_metadata.get("start_at_known") is False else iso(event.start_at),
        "end_at": iso(event.end_at),
        "location": {
            "name": event.location_name,
            "latitude": event.latitude,
            "longitude": event.longitude,
        },
        "importance": event.importance,
        "status": event.status,
        "confidence": event.confidence,
        "document_count": len(documents),
        "independent_source_count": len(groups),
        "source_types": dict(source_types),
        "languages": languages,
        "entities": entities,
        "claim_counts": dict(claim_counts),
        "has_contested_claim": any(c.status in {"contested", "unverified"} for c in event.claims),
        "provenance": {
            "intake_item_id": event_metadata.get("intake_item_id"),
            "confirmation_stage": event_metadata.get("confirmation_stage"),
        },
    }


def serialize_document(document: Document, event_id: str | None = None) -> dict[str, Any]:
    metadata = document.metadata_json or {}
    canonical_url = None if document.canonical_url.startswith("pldr:") else document.canonical_url
    return {
        "id": document.id,
        "title": None if metadata.get("title_known") is False else document.title,
        "title_known": metadata.get("title_known", True),
        "source": {
            "id": document.source.id,
            "name": document.source.name,
            "type": document.source.source_type,
            "country": document.source.country,
            "reliability_tier": document.source.reliability_tier,
            "independence_group": document.source.independence_group,
        },
        "published_at": None if metadata.get("published_at_known") is False else iso(document.published_at),
        "fetched_at": iso(document.fetched_at),
        "language": document.language,
        "content_hash": document.content_hash,
        "upstream_story_id": document.upstream_story_id,
        "is_cached": document.is_cached,
        "canonical_url": canonical_url,
        "canonical_url_known": canonical_url is not None,
        "snapshot_url": f"/snapshots/{document.id}" + (f"?event_id={event_id}" if event_id else ""),
        "metadata": metadata,
        "provenance": {
            "intake_item_id": metadata.get("intake_item_id"),
            "confirmation_stage": metadata.get("confirmation_stage"),
        },
    }


def serialize_evidence(evidence: Evidence, event_id: str | None = None) -> dict[str, Any]:
    return {
        "id": evidence.id,
        "snippet": evidence.snippet,
        "start_offset": evidence.start_offset,
        "end_offset": evidence.end_offset,
        "stance": evidence.stance,
        "strength": evidence.strength,
        "note": evidence.note,
        "document": serialize_document(evidence.document, event_id),
    }


def serialize_claim(claim: Claim) -> dict[str, Any]:
    return {
        "id": claim.id,
        "text": claim.text,
        "status": claim.status,
        "confidence": claim.confidence,
        "origin": claim.origin,
        "temporal_scope": claim.temporal_scope,
        "evidence": [serialize_evidence(x, claim.event_id) for x in claim.evidence_items],
    }


def serialize_assessment(a: Assessment | None) -> dict[str, Any] | None:
    if a is None:
        return None
    return {
        "id": a.id,
        "judgement": a.judgement,
        "assumptions": a.assumptions,
        "alternatives": a.alternatives,
        "information_gaps": a.information_gaps,
        "falsifiers": a.falsifiers,
        "confidence": a.confidence,
        "generated_by": a.generated_by,
        "generated_at": iso(a.generated_at),
    }


def serialize_event_detail(event: Event) -> dict[str, Any]:
    data = serialize_event_card(event)
    documents = sorted((link.document for link in event.document_links), key=lambda x: x.published_at)
    latest_assessment = max(event.assessments, key=lambda x: x.generated_at) if event.assessments else None
    data.update(
        {
            "documents": [serialize_document(d, event.id) for d in documents],
            "claims": [serialize_claim(c) for c in sorted(event.claims, key=lambda x: x.confidence, reverse=True)],
            "assessment": serialize_assessment(latest_assessment),
        }
    )
    return data


def serialize_source(source: Source) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    last = source.last_success_at
    if last and last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    age = (now - last).total_seconds() / 3600 if last else None
    return {
        "id": source.id,
        "name": source.name,
        "base_url": None if source.base_url.startswith("pldr:") else source.base_url,
        "base_url_known": not source.base_url.startswith("pldr:"),
        "country": source.country,
        "language": source.language,
        "type": source.source_type,
        "reliability_tier": source.reliability_tier,
        "independence_group": source.independence_group,
        "status": source.status,
        "last_success_at": iso(source.last_success_at),
        "last_error": source.last_error,
        "age_hours": round(age, 2) if age is not None else None,
        "document_count": len(source.documents),
    }
