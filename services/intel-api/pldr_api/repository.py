from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .models import Assessment, Claim, Document, Event, EventDocument, EventEntity, Evidence, Snapshot, Source


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def event_query():
    return select(Event).options(
        selectinload(Event.document_links)
        .selectinload(EventDocument.document)
        .selectinload(Document.source),
        selectinload(Event.document_links).selectinload(EventDocument.document).selectinload(Document.snapshots),
        selectinload(Event.entity_links).selectinload(EventEntity.entity),
        selectinload(Event.claims)
        .selectinload(Claim.evidence_items)
        .selectinload(Evidence.document)
        .selectinload(Document.source),
        selectinload(Event.claims).selectinload(Claim.evidence_items).selectinload(Evidence.snapshot),
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


def _snapshot_url(snapshot_id: str, event_id: str | None = None) -> str:
    return f"/snapshots/{snapshot_id}" + (f"?event_id={event_id}" if event_id else "")


def _latest_document_snapshot(document: Document) -> Snapshot | None:
    metadata = document.metadata_json or {}
    latest_id = metadata.get("latest_snapshot_id")
    if isinstance(latest_id, str):
        latest = next((snapshot for snapshot in document.snapshots if snapshot.id == latest_id), None)
        if latest is not None:
            return latest
    return max(
        document.snapshots,
        key=lambda snapshot: (iso(snapshot.captured_at) or "", snapshot.id),
        default=None,
    )


def serialize_document(
    document: Document,
    event_id: str | None = None,
    *,
    selected_snapshot: Snapshot | None = None,
) -> dict[str, Any]:
    document_metadata = document.metadata_json or {}
    latest_snapshot = _latest_document_snapshot(document)
    snapshot = selected_snapshot or latest_snapshot
    snapshot_role = "evidence-fixed" if selected_snapshot is not None else "document-latest"
    snapshot_metadata = (snapshot.metadata_json or {}) if snapshot is not None else {}
    if selected_snapshot is not None:
        selected_is_head = latest_snapshot is not None and selected_snapshot.id == latest_snapshot.id
        has_version_metadata = "title_known" in snapshot_metadata
        if has_version_metadata or not selected_is_head:
            title = snapshot_metadata.get("title")
            title_known = snapshot_metadata.get("title_known") is True and bool(title)
            published_at = snapshot_metadata.get("published_at")
            published_at_known = snapshot_metadata.get("published_at_known") is True and bool(
                published_at
            )
        else:
            # Legacy one-snapshot Documents predate per-Snapshot metadata. Falling
            # back to the Document is safe only while that Snapshot is still its head.
            title = document.title
            title_known = document_metadata.get("title_known", True) is not False
            published_at = iso(document.published_at)
            published_at_known = document_metadata.get("published_at_known", True) is not False
        fetched_at = iso(snapshot.captured_at)
        language = snapshot_metadata.get("language") or document.language
        content_hash_value = snapshot.content_hash
        raw_canonical_url = snapshot_metadata.get("canonical_url") or document.canonical_url
        metadata = snapshot_metadata
        provenance_intake_id = snapshot_metadata.get("intake_item_id")
    else:
        title = document.title
        title_known = document_metadata.get("title_known", True) is not False
        published_at = iso(document.published_at)
        published_at_known = document_metadata.get("published_at_known", True) is not False
        fetched_at = iso(document.fetched_at)
        language = document.language
        content_hash_value = document.content_hash
        raw_canonical_url = document.canonical_url
        metadata = document_metadata
        provenance_intake_id = document_metadata.get("latest_intake_item_id") or document_metadata.get(
            "intake_item_id"
        )
    canonical_url = (
        None if not raw_canonical_url or raw_canonical_url.startswith("pldr:") else raw_canonical_url
    )
    return {
        "id": document.id,
        "title": title if title_known else None,
        "title_known": title_known,
        "source": {
            "id": document.source.id,
            "name": document.source.name,
            "type": document.source.source_type,
            "country": document.source.country,
            "reliability_tier": document.source.reliability_tier,
            "independence_group": document.source.independence_group,
        },
        "published_at": published_at if published_at_known else None,
        "fetched_at": fetched_at,
        "language": language,
        "content_hash": content_hash_value,
        "upstream_story_id": document.upstream_story_id,
        "is_cached": document.is_cached,
        "canonical_url": canonical_url,
        "canonical_url_known": canonical_url is not None,
        "snapshot_id": snapshot.id if snapshot else None,
        "snapshot_url": _snapshot_url(snapshot.id if snapshot else document.id, event_id),
        "snapshot_role": snapshot_role,
        "latest_snapshot_id": latest_snapshot.id if latest_snapshot else None,
        "latest_snapshot_url": _snapshot_url(
            latest_snapshot.id if latest_snapshot else document.id,
            event_id,
        ),
        "document_head": {
            "title": None
            if document_metadata.get("title_known") is False
            else document.title,
            "published_at": None
            if document_metadata.get("published_at_known") is False
            else iso(document.published_at),
            "fetched_at": iso(document.fetched_at),
            "language": document.language,
            "content_hash": document.content_hash,
            "snapshot_id": latest_snapshot.id if latest_snapshot else None,
            "snapshot_url": _snapshot_url(
                latest_snapshot.id if latest_snapshot else document.id,
                event_id,
            ),
        },
        "metadata": metadata,
        "document_metadata": document_metadata,
        "provenance": {
            "intake_item_id": provenance_intake_id,
            "confirmation_stage": (
                snapshot_metadata.get("confirmation_stage")
                if selected_snapshot is not None
                else document_metadata.get("confirmation_stage")
            ),
        },
    }


def serialize_evidence(evidence: Evidence, event_id: str | None = None) -> dict[str, Any]:
    pinned_snapshot = evidence.snapshot
    snapshot = pinned_snapshot or _latest_document_snapshot(evidence.document)
    latest_snapshot = _latest_document_snapshot(evidence.document)
    snapshot_url = _snapshot_url(snapshot.id if snapshot else evidence.document_id, event_id)
    return {
        "id": evidence.id,
        "snapshot_id": snapshot.id if snapshot else None,
        "snapshot_url": snapshot_url,
        "snapshot_role": "evidence-fixed" if pinned_snapshot is not None else "document-latest-fallback",
        "document_latest_snapshot_id": latest_snapshot.id if latest_snapshot else None,
        "document_latest_snapshot_url": _snapshot_url(
            latest_snapshot.id if latest_snapshot else evidence.document_id,
            event_id,
        ),
        "snippet": evidence.snippet,
        "start_offset": evidence.start_offset,
        "end_offset": evidence.end_offset,
        "stance": evidence.stance,
        "strength": evidence.strength,
        "note": evidence.note,
        # Preserve the historical `evidence.document.snapshot_url` access path, but
        # pin it to this Evidence's immutable Snapshot. The Document's advancing head
        # remains explicit via `latest_snapshot_*`.
        "document": serialize_document(
            evidence.document,
            event_id,
            selected_snapshot=pinned_snapshot,
        ),
    }


def derive_claim_source_status(evidence_items: list[dict[str, Any]]) -> dict[str, Any]:
    """Describe corroboration from provenance instead of model confidence.

    A single saved page is useful evidence, but it is not multi-source
    corroboration.  The public status is therefore computed from independent
    source groups and evidence stances every time it is displayed.
    """
    supporting_groups: set[str] = set()
    contradicting_groups: set[str] = set()
    all_groups: set[str] = set()
    for evidence in evidence_items:
        source = ((evidence.get("document") or {}).get("source") or {})
        group = str(source.get("independence_group") or source.get("id") or "").strip()
        if not group:
            continue
        all_groups.add(group)
        if evidence.get("stance") == "supports":
            supporting_groups.add(group)
        elif evidence.get("stance") == "contradicts":
            contradicting_groups.add(group)
    if contradicting_groups:
        status = "contested"
    elif len(supporting_groups) >= 2:
        status = "supported"
    elif all_groups:
        status = "single_source"
    else:
        status = "unverified"
    return {
        "status": status,
        "independent_source_count": len(all_groups),
        "supporting_source_count": len(supporting_groups),
        "contradicting_source_count": len(contradicting_groups),
    }


def serialize_claim(claim: Claim) -> dict[str, Any]:
    evidence = [serialize_evidence(x, claim.event_id) for x in claim.evidence_items]
    return {
        "id": claim.id,
        "text": claim.text,
        "status": claim.status,
        "source_verification": derive_claim_source_status(evidence),
        "confidence": claim.confidence,
        "origin": claim.origin,
        "temporal_scope": claim.temporal_scope,
        "evidence": evidence,
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
