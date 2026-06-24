from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict

SourceKind = Literal["topic", "url", "blog", "product", "youtube", "pdf", "voice", "competitor"]


class SourceCreate(BaseModel):
    brand_id: UUID
    kind: SourceKind
    title: Optional[str] = None
    url: Optional[str] = None
    raw_text: Optional[str] = None
    storage_key: Optional[str] = None
    meta: dict = {}


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    brand_id: UUID
    kind: str
    title: Optional[str]
    url: Optional[str]
    storage_key: Optional[str]
    status: str
    error: Optional[str]
    meta: dict
    created_at: datetime
    updated_at: datetime


class SourceUploadInit(BaseModel):
    brand_id: UUID
    kind: Literal["pdf", "voice"]
    filename: str
    content_type: str


class SourceUploadInitOut(BaseModel):
    storage_key: str
    upload_url: str
    expires_in: int
