from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
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
