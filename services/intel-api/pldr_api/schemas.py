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

class ModelTaskRequest(BaseModel):
    task:Literal["normalize_event_title","summarize_event","extract_entities_locations","extract_claims_evidence","draft_report"]
    payload:dict
