from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    country: Mapped[str] = mapped_column(String(80), default="")
    language: Mapped[str] = mapped_column(String(20), default="en")
    source_type: Mapped[str] = mapped_column(String(40), default="media")
    reliability_tier: Mapped[int] = mapped_column(Integer, default=3)
    independence_group: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="healthy")
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    documents: Mapped[list["Document"]] = relationship(back_populates="source")


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("canonical_url", name="uq_document_canonical_url"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    canonical_url: Mapped[str] = mapped_column(String(900), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    language: Mapped[str] = mapped_column(String(20), default="en")
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    upstream_story_id: Mapped[str] = mapped_column(String(120), default="")
    is_cached: Mapped[bool] = mapped_column(Boolean, default=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source: Mapped[Source] = relationship(back_populates="documents")
    snapshots: Mapped[list["Snapshot"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    event_links: Mapped[list["EventDocument"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    evidence_items: Mapped[list["Evidence"]] = relationship(back_populates="document")


class Snapshot(Base):
    __tablename__ = "snapshots"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    storage_path: Mapped[str] = mapped_column(String(500), default="inline")
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=dict)
    document: Mapped[Document] = relationship(back_populates="snapshots")


class Event(Base):
    __tablename__ = "events"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), default="incident")
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    location_name: Mapped[str] = mapped_column(String(200), default="")
    importance: Mapped[str] = mapped_column(String(20), default="medium")
    status: Mapped[str] = mapped_column(String(30), default="confirmed")
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    document_links: Mapped[list["EventDocument"]] = relationship(back_populates="event", cascade="all, delete-orphan")
    entity_links: Mapped[list["EventEntity"]] = relationship(back_populates="event", cascade="all, delete-orphan")
    claims: Mapped[list["Claim"]] = relationship(back_populates="event", cascade="all, delete-orphan")
    assessments: Mapped[list["Assessment"]] = relationship(back_populates="event", cascade="all, delete-orphan")


class Entity(Base):
    __tablename__ = "entities"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(60), default="organization")
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    event_links: Mapped[list["EventEntity"]] = relationship(back_populates="entity", cascade="all, delete-orphan")


class EventDocument(Base):
    __tablename__ = "event_documents"
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), primary_key=True)
    relevance: Mapped[float] = mapped_column(Float, default=1.0)
    event: Mapped[Event] = relationship(back_populates="document_links")
    document: Mapped[Document] = relationship(back_populates="event_links")


class EventEntity(Base):
    __tablename__ = "event_entities"
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), primary_key=True)
    entity_id: Mapped[str] = mapped_column(ForeignKey("entities.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(120), default="related")
    event: Mapped[Event] = relationship(back_populates="entity_links")
    entity: Mapped[Entity] = relationship(back_populates="event_links")


class Claim(Base):
    __tablename__ = "claims"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="unverified")
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    origin: Mapped[str] = mapped_column(String(30), default="machine")
    temporal_scope: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    event: Mapped[Event] = relationship(back_populates="claims")
    evidence_items: Mapped[list["Evidence"]] = relationship(back_populates="claim", cascade="all, delete-orphan")


class Evidence(Base):
    __tablename__ = "evidence"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("snapshots.id"), index=True)
    snippet: Mapped[str] = mapped_column(Text, nullable=False)
    start_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    end_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    stance: Mapped[str] = mapped_column(String(30), default="supports")
    strength: Mapped[float] = mapped_column(Float, default=0.7)
    note: Mapped[str] = mapped_column(Text, default="")
    claim: Mapped[Claim] = relationship(back_populates="evidence_items")
    document: Mapped[Document] = relationship(back_populates="evidence_items")
    snapshot: Mapped[Snapshot | None] = relationship()


class Assessment(Base):
    __tablename__ = "assessments"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    judgement: Mapped[str] = mapped_column(Text, nullable=False)
    assumptions: Mapped[list[str]] = mapped_column(JSON, default=list)
    alternatives: Mapped[list[str]] = mapped_column(JSON, default=list)
    information_gaps: Mapped[list[str]] = mapped_column(JSON, default=list)
    falsifiers: Mapped[list[str]] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    generated_by: Mapped[str] = mapped_column(String(80), default="deterministic-fallback")
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    event: Mapped[Event] = relationship(back_populates="assessments")


class IntakeItem(Base):
    """A persisted material submission that remains outside the formal dossier until review."""

    __tablename__ = "intake_items"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    input_type: Mapped[str] = mapped_column(String(20), index=True)
    status: Mapped[str] = mapped_column(String(30), default="parsed", index=True)
    error: Mapped[str | None] = mapped_column(Text)
    source_description: Mapped[str] = mapped_column(String(500), default="")
    source_url: Mapped[str | None] = mapped_column(String(900))
    canonical_url: Mapped[str | None] = mapped_column(String(900))
    title: Mapped[str | None] = mapped_column(String(500))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    language: Mapped[str] = mapped_column(String(20), default="en")
    original_filename: Mapped[str | None] = mapped_column(String(300))
    media_type: Mapped[str | None] = mapped_column(String(120))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    raw_snapshot: Mapped[str] = mapped_column(Text, default="")
    raw_hash: Mapped[str] = mapped_column(String(64), default="")
    extracted_snapshot: Mapped[str] = mapped_column(Text, default="")
    extracted_hash: Mapped[str] = mapped_column(String(64), default="")
    candidate_mode: Mapped[str | None] = mapped_column(String(30))
    candidate_model: Mapped[str | None] = mapped_column(String(160))
    candidate_error: Mapped[str | None] = mapped_column(Text)
    candidate_relations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    review: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    confirmation_result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    confirmation_fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    disposition: Mapped[str | None] = mapped_column(String(30))
    reviewed_by: Mapped[str | None] = mapped_column(String(160))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    final_event_id: Mapped[str | None] = mapped_column(String(64), index=True)
    final_document_id: Mapped[str | None] = mapped_column(String(64), index=True)
    final_snapshot_id: Mapped[str | None] = mapped_column(String(64))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    archived_by: Mapped[str | None] = mapped_column(String(160))
    archive_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    candidates: Mapped[list["IntakeCandidate"]] = relationship(
        back_populates="item", cascade="all, delete-orphan", order_by="IntakeCandidate.object_type, IntakeCandidate.candidate_key"
    )


class IntakeCandidate(Base):
    """Immutable machine proposal plus analyst disposition; never a formal PLDR object."""

    __tablename__ = "intake_candidates"
    __table_args__ = (UniqueConstraint("item_id", "candidate_key", name="uq_intake_candidate_key"),)
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("intake_items.id"), index=True)
    candidate_key: Mapped[str] = mapped_column(String(80))
    object_type: Mapped[str] = mapped_column(String(20), index=True)
    source_mode: Mapped[str] = mapped_column(String(30))
    machine_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    human_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    validation_error: Mapped[str | None] = mapped_column(Text)
    disposition: Mapped[str | None] = mapped_column(String(30))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    final_object_id: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    item: Mapped[IntakeItem] = relationship(back_populates="candidates")


class CollectionTarget(Base):
    """A public source monitored by the reliable-collection worker."""

    __tablename__ = "collection_targets"
    __table_args__ = (UniqueConstraint("url", name="uq_collection_target_url"),)

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    target_type: Mapped[str] = mapped_column(
        String(30), default="web_page", nullable=False, index=True
    )
    url: Mapped[str] = mapped_column(String(900), nullable=False)
    language: Mapped[str] = mapped_column(String(20), default="en")
    interval_seconds: Mapped[int] = mapped_column(Integer, default=3600)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    health: Mapped[str] = mapped_column(String(30), default="new", index=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    runs: Mapped[list["CollectionRun"]] = relationship(
        back_populates="target",
        cascade="all, delete-orphan",
        order_by="CollectionRun.queued_at.desc()",
    )


class CollectionRun(Base):
    """One durable collection attempt; only changed versions point at a new intake."""

    __tablename__ = "collection_runs"
    __table_args__ = (
        Index("uq_collection_run_active_key", "active_key", unique=True),
    )

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    target_id: Mapped[str] = mapped_column(ForeignKey("collection_targets.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    # Equals target_id while queued/running and becomes NULL on completion. The
    # unique constraint closes the API race between two simultaneous manual queues.
    active_key: Mapped[str | None] = mapped_column(String(80))
    outcome: Mapped[str | None] = mapped_column(String(30), index=True)
    trigger: Mapped[str] = mapped_column(String(30), default="scheduled", index=True)
    retry_of_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("collection_runs.id"), index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer, default=1)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(160), index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    lease_recoveries: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_class: Mapped[str | None] = mapped_column(String(80), index=True)
    error_message: Mapped[str | None] = mapped_column(Text)
    resolved_url: Mapped[str | None] = mapped_column(String(900))
    http_status: Mapped[int | None] = mapped_column(Integer)
    media_type: Mapped[str | None] = mapped_column(String(160))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    raw_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    body_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    version_number: Mapped[int | None] = mapped_column(Integer, index=True)
    previous_intake_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("intake_items.id"), index=True
    )
    current_intake_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("intake_items.id"), index=True
    )
    discovered_count: Mapped[int] = mapped_column(Integer, default=0)
    new_item_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_item_count: Mapped[int] = mapped_column(Integer, default=0)
    invalid_item_count: Mapped[int] = mapped_column(Integer, default=0)
    target: Mapped[CollectionTarget] = relationship(back_populates="runs")
    previous_intake_item: Mapped[IntakeItem | None] = relationship(
        foreign_keys=[previous_intake_item_id]
    )
    current_intake_item: Mapped[IntakeItem | None] = relationship(
        foreign_keys=[current_intake_item_id]
    )


class CollectionDiscoveredItem(Base):
    """One durable feed item fingerprint and its optional review material."""

    __tablename__ = "collection_discovered_items"
    __table_args__ = (
        UniqueConstraint("target_id", "item_key", name="uq_collection_discovered_item_key"),
    )

    id: Mapped[str] = mapped_column(String(112), primary_key=True)
    target_id: Mapped[str] = mapped_column(
        ForeignKey("collection_targets.id"), index=True
    )
    item_key: Mapped[str] = mapped_column(String(64), index=True)
    source_url: Mapped[str] = mapped_column(String(900), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    intake_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("intake_items.id"), unique=True
    )
    first_seen_run_id: Mapped[str] = mapped_column(String(96))
    last_seen_run_id: Mapped[str] = mapped_column(String(96))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    intake_item: Mapped[IntakeItem | None] = relationship()


class SearchQueryRun(Base):
    """A call to an external search backend; deliberately outside the formal dossier."""

    __tablename__ = "external_search_query_runs"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    keyword: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_keyword: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(20), index=True)
    provider: Mapped[str] = mapped_column(String(60), nullable=False)
    channel: Mapped[str] = mapped_column(String(100), nullable=False)
    language: Mapped[str] = mapped_column(String(20), default="en")
    status: Mapped[str] = mapped_column(String(20), default="ok", index=True)
    error: Mapped[str | None] = mapped_column(Text)
    # ``error`` stays as the legacy plain-text field. New clients use this
    # serializable envelope to explain cause, impact, and recovery without
    # scraping an exception string.
    error_detail: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    current_page: Mapped[int] = mapped_column(Integer, default=1)
    page_size: Mapped[int] = mapped_column(Integer, default=10)
    returned_count: Mapped[int] = mapped_column(Integer, default=0)
    has_more: Mapped[bool] = mapped_column(Boolean, default=False)
    total_known: Mapped[bool] = mapped_column(Boolean, default=False)
    total_count: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    archived_by: Mapped[str | None] = mapped_column(String(160))
    archive_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, index=True
    )
    results: Mapped[list["SearchResult"]] = relationship(
        back_populates="query_run", cascade="all, delete-orphan", order_by="SearchResult.rank"
    )


class SearchResult(Base):
    """Normalized search metadata. It is not Source, Document, or Evidence."""

    __tablename__ = "external_search_results"
    __table_args__ = (UniqueConstraint("query_run_id", "result_fingerprint", name="uq_search_result_in_run"),)
    id: Mapped[str] = mapped_column(String(112), primary_key=True)
    query_run_id: Mapped[str] = mapped_column(ForeignKey("external_search_query_runs.id"), index=True)
    result_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    provider: Mapped[str] = mapped_column(String(60), nullable=False)
    channel: Mapped[str] = mapped_column(String(100), nullable=False)
    original_url: Mapped[str] = mapped_column(String(900), nullable=False)
    canonical_url: Mapped[str] = mapped_column(String(900), nullable=False)
    site_name: Mapped[str] = mapped_column(String(200), default="")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    snippet: Mapped[str] = mapped_column(Text, default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    source_page: Mapped[int] = mapped_column(Integer, default=1, index=True)
    engine: Mapped[str] = mapped_column(String(120), default="")
    raw_result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    query_run: Mapped[SearchQueryRun] = relationship(back_populates="results")
    selection: Mapped["SearchSelection | None"] = relationship(
        back_populates="result", uselist=False
    )


class SearchSelection(Base):
    """Durable one-to-one link from an identified result URL to an intake item."""

    __tablename__ = "external_search_selections"
    id: Mapped[str] = mapped_column(String(112), primary_key=True)
    result_id: Mapped[str] = mapped_column(ForeignKey("external_search_results.id"), index=True)
    result_fingerprint: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    intake_item_id: Mapped[str] = mapped_column(ForeignKey("intake_items.id"), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="parsed", index=True)
    outcome: Mapped[str] = mapped_column(String(30), default="added")
    attempt_count: Mapped[int] = mapped_column(Integer, default=1)
    last_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    result: Mapped[SearchResult] = relationship(back_populates="selection")
    intake_item: Mapped[IntakeItem] = relationship()
    events: Mapped[list["SearchSelectionEvent"]] = relationship(
        back_populates="selection",
        cascade="all, delete-orphan",
        order_by="SearchSelectionEvent.created_at",
    )


class SearchSelectionEvent(Base):
    """One analyst submission of an identified result, even when intake is reused."""

    __tablename__ = "external_search_selection_events"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    selection_id: Mapped[str] = mapped_column(
        ForeignKey("external_search_selections.id"), index=True
    )
    query_run_id: Mapped[str] = mapped_column(
        ForeignKey("external_search_query_runs.id"), index=True
    )
    result_id: Mapped[str] = mapped_column(ForeignKey("external_search_results.id"), index=True)
    outcome: Mapped[str] = mapped_column(String(30), default="added")
    trace_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    selection: Mapped[SearchSelection] = relationship(back_populates="events")


class Investigation(Base):
    """A durable analyst topic that groups work without changing formal evidence."""

    __tablename__ = "investigations"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    question: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class InvestigationLink(Base):
    """Many-to-many topic membership for durable PLDR object identifiers."""

    __tablename__ = "investigation_links"
    __table_args__ = (
        UniqueConstraint(
            "investigation_id",
            "object_type",
            "object_id",
            name="uq_investigation_object_link",
        ),
        Index("ix_investigation_link_object", "object_type", "object_id"),
    )

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    investigation_id: Mapped[str] = mapped_column(
        ForeignKey("investigations.id"), index=True
    )
    object_type: Mapped[str] = mapped_column(String(40), index=True)
    object_id: Mapped[str] = mapped_column(String(128), index=True)
    role: Mapped[str] = mapped_column(String(40), default="member")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ProcessingBatch(Base):
    """One quick-return selection request whose entries are independent tasks."""

    __tablename__ = "investigation_processing_batches"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    request_id: Mapped[str | None] = mapped_column(String(128), unique=True, index=True)
    investigation_id: Mapped[str] = mapped_column(
        ForeignKey("investigations.id"), index=True
    )
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    requested_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ReviewTask(Base):
    """One leased fetch/generation unit; failures never abort sibling entries."""

    __tablename__ = "investigation_review_tasks"
    __table_args__ = (
        UniqueConstraint(
            "investigation_id",
            "intake_item_id",
            name="uq_investigation_review_task_intake",
        ),
        Index("uq_investigation_task_active_key", "active_key", unique=True),
    )

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    investigation_id: Mapped[str] = mapped_column(
        ForeignKey("investigations.id"), index=True
    )
    batch_id: Mapped[str | None] = mapped_column(
        ForeignKey("investigation_processing_batches.id"), index=True
    )
    task_type: Mapped[str] = mapped_column(String(40), default="search_result_intake", index=True)
    subject_type: Mapped[str] = mapped_column(String(40), default="search_result", index=True)
    subject_id: Mapped[str] = mapped_column(String(128), index=True)
    # Non-NULL only while the task is actionable. SQLite permits multiple NULLs,
    # giving us one active task per investigation/result without losing history.
    active_key: Mapped[str | None] = mapped_column(String(240))
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, default=1)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(160), index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    lease_recoveries: Mapped[int] = mapped_column(Integer, default=0)
    error_class: Mapped[str | None] = mapped_column(String(80), index=True)
    error_message: Mapped[str | None] = mapped_column(Text)
    intake_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("intake_items.id"), index=True
    )
    selection_id: Mapped[str | None] = mapped_column(
        ForeignKey("external_search_selections.id"), index=True
    )
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ProcessingBatchEntry(Base):
    """Stable membership of requested results in a batch, including deduplicated tasks."""

    __tablename__ = "investigation_processing_batch_entries"
    __table_args__ = (
        UniqueConstraint("batch_id", "result_id", name="uq_processing_batch_result"),
    )

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("investigation_processing_batches.id"), index=True
    )
    task_id: Mapped[str] = mapped_column(
        ForeignKey("investigation_review_tasks.id"), index=True
    )
    result_id: Mapped[str] = mapped_column(String(128), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class DecisionLog(Base):
    """Append-only analyst/system action trail scoped to an investigation."""

    __tablename__ = "investigation_decision_logs"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    investigation_id: Mapped[str] = mapped_column(
        ForeignKey("investigations.id"), index=True
    )
    action: Mapped[str] = mapped_column(String(80), index=True)
    actor: Mapped[str] = mapped_column(String(160), default="system")
    object_type: Mapped[str | None] = mapped_column(String(40), index=True)
    object_id: Mapped[str | None] = mapped_column(String(128), index=True)
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("investigation_review_tasks.id"), index=True
    )
    detail_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


# Stable vocabulary aliases for integrations that call these processing tasks or
# action logs rather than using the UI-facing class names.
ProcessingTask = ReviewTask
InvestigationTask = ReviewTask
DecisionActionLog = DecisionLog
InvestigationActivity = DecisionLog
