from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator


class InvestigationCreate(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    question: str = Field(default="", max_length=4000)
    # ``objective`` is accepted as a human-friendly synonym. Responses expose
    # both keys, while persistence keeps one canonical question/goal value.
    objective: str | None = Field(default=None, max_length=4000)
    description: str = Field(default="", max_length=20_000)
    status: Literal["active", "paused", "closed", "archived"] = "active"
    actor: str = Field(default="analyst", min_length=1, max_length=160)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("title must contain visible characters")
        return cleaned


class InvestigationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    question: str | None = Field(default=None, max_length=4000)
    objective: str | None = Field(default=None, max_length=4000)
    description: str | None = Field(default=None, max_length=20_000)
    status: Literal["active", "paused", "closed", "archived"] | None = None
    actor: str = Field(default="analyst", min_length=1, max_length=160)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("title must contain visible characters")
        return cleaned


class InvestigationLinkRequest(BaseModel):
    object_type: Literal["search_query", "intake", "collection_target", "event"]
    object_id: str = Field(min_length=1, max_length=128)
    role: str = Field(default="member", min_length=1, max_length=40)
    actor: str = Field(default="analyst", min_length=1, max_length=160)


class ReviewTaskRetryRequest(BaseModel):
    actor: str = Field(default="analyst", min_length=1, max_length=160)

class ReportRequest(BaseModel):
    event_ids:list[str]=Field(default_factory=list,max_length=20)
    investigation_id:str|None=Field(default=None,max_length=80)
    title:str|None=Field(default=None,max_length=200)

    @model_validator(mode="after")
    def require_scope(self):
        if not self.event_ids and not self.investigation_id:
            raise ValueError("event_ids or investigation_id is required")
        return self

class ImportUrlRequest(BaseModel):
    url:HttpUrl
    source_name:str|None=Field(default=None,max_length=160)
    title:str|None=Field(default=None,max_length=500)
    html:str|None=None
    language:str=Field(default="en",max_length=20)

class ImportRssRequest(BaseModel):
    url:HttpUrl|None=None
    xml:str|None=None
    source_name:str=Field(default="Imported RSS",max_length=160)
    language:str=Field(default="en",max_length=20)

class IntakeTextRequest(BaseModel):
    text:str=Field(default="",max_length=200_000)
    source_description:str=Field(min_length=3,max_length=500)
    title:str|None=Field(default=None,max_length=500)
    published_at:str|None=Field(default=None,max_length=40)
    language:str=Field(default="en",max_length=20)

class IntakeEventDecision(BaseModel):
    title:str=Field(default="",max_length=500)
    summary:str=Field(default="",max_length=4000)
    event_type:str=Field(default="incident",max_length=80)
    start_at:str|None=Field(default=None,max_length=40)
    location_name:str=Field(default="Unknown",max_length=200)
    importance:str=Field(default="medium",max_length=20)

class IntakeEntityDecision(BaseModel):
    candidate_key:str=Field(min_length=1,max_length=80)
    action:Literal["create","merge","exclude"]="create"
    name:str=Field(default="",max_length=200)
    entity_type:str=Field(default="organization",max_length=60)
    aliases:list[str]=Field(default_factory=list,max_length=20)
    role:str=Field(default="related",max_length=120)
    merge_entity_id:str|None=Field(default=None,max_length=64)

class IntakeClaimDecision(BaseModel):
    candidate_key:str=Field(min_length=1,max_length=80)
    action:Literal["create","merge","exclude"]="create"
    text:str=Field(default="",max_length=4000)
    status:str=Field(default="unverified",max_length=30)
    confidence:float=Field(default=0.5,ge=0,le=1)
    temporal_scope:str=Field(default="",max_length=120)
    merge_claim_id:str|None=Field(default=None,max_length=64)

class IntakeEvidenceDecision(BaseModel):
    candidate_key:str=Field(min_length=1,max_length=80)
    action:Literal["include","exclude"]="include"
    snippet:str=Field(default="",max_length=4000)
    stance:Literal["supports","contradicts","context"]="supports"
    strength:float=Field(default=0.7,ge=0,le=1)
    note:str=Field(default="",max_length=1000)

class IntakeConfirmationRequest(BaseModel):
    disposition:Literal["create","merge","modify"]
    analyst:str=Field(default="analyst",min_length=1,max_length=160)
    merge_event_id:str|None=Field(default=None,max_length=64)
    event:IntakeEventDecision=Field(default_factory=IntakeEventDecision)
    entities:list[IntakeEntityDecision]=Field(default_factory=list)
    claims:list[IntakeClaimDecision]=Field(default_factory=list)
    evidence:list[IntakeEvidenceDecision]=Field(default_factory=list)

class IntakeRejectRequest(BaseModel):
    analyst:str=Field(default="analyst",min_length=1,max_length=160)
    reason:str=Field(min_length=3,max_length=2000)

class IntakeCancelRequest(BaseModel):
    analyst:str=Field(default="analyst",min_length=1,max_length=160)
    reason:str=Field(default="Cancelled before confirmation",min_length=3,max_length=2000)

class ExternalSearchRequest(BaseModel):
    keyword:str=Field(min_length=2,max_length=400)
    scope:Literal["news","web"]="web"
    limit:int=Field(default=10,ge=5,le=20)
    language:str=Field(default="en",min_length=2,max_length=20)
    investigation_id:str|None=Field(default=None,max_length=80)
    new_investigation:InvestigationCreate|None=None

    @model_validator(mode="after")
    def one_investigation_context(self):
        if self.investigation_id and self.new_investigation:
            raise ValueError("Use investigation_id or new_investigation, not both")
        return self

class ExternalSearchSelectionRequest(BaseModel):
    result_ids:list[str]=Field(min_length=1,max_length=20)
    request_id:str|None=Field(default=None,min_length=1,max_length=128)
    investigation_id:str|None=Field(default=None,max_length=80)
    new_investigation:InvestigationCreate|None=None
    actor:str=Field(default="analyst",min_length=1,max_length=160)

    @model_validator(mode="after")
    def one_investigation_context(self):
        if self.investigation_id and self.new_investigation:
            raise ValueError("Use investigation_id or new_investigation, not both")
        return self


class CollectionTargetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    url: HttpUrl
    language: str = Field(default="en", min_length=2, max_length=20)
    interval_seconds: int = Field(default=3600, ge=60, le=2_592_000)
    enabled: bool = True
    run_immediately: bool = False
    investigation_id: str | None = Field(default=None, max_length=80)
    new_investigation: InvestigationCreate | None = None
    actor: str = Field(default="analyst", min_length=1, max_length=160)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("name must contain visible characters")
        return cleaned

    @model_validator(mode="after")
    def one_investigation_context(self):
        if self.investigation_id and self.new_investigation:
            raise ValueError("Use investigation_id or new_investigation, not both")
        return self

class ModelTaskRequest(BaseModel):
    task:Literal["normalize_event_title","summarize_event","extract_entities_locations","extract_claims_evidence","draft_report","extract_intake_candidates"]
    payload:dict
