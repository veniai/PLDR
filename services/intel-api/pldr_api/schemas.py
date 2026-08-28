from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field, HttpUrl

class ReportRequest(BaseModel):
    event_ids:list[str]=Field(min_length=1,max_length=20)
    title:str|None=Field(default=None,max_length=200)

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

class ExternalSearchSelectionRequest(BaseModel):
    result_ids:list[str]=Field(min_length=1,max_length=20)

class ModelTaskRequest(BaseModel):
    task:Literal["normalize_event_title","summarize_event","extract_entities_locations","extract_claims_evidence","draft_report","extract_intake_candidates"]
    payload:dict
