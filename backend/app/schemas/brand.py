from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class BrandBase(BaseModel):
    name: str
    slug: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9-]+$")
    description: Optional[str] = None
    website_url: Optional[str] = None
    product_url: Optional[str] = None
    competitor_urls: list[str] = []
    primary_topic: Optional[str] = None
    audience: Optional[str] = None
    tone: Optional[str] = None
    style_guide: dict = {}
    messaging: dict = {}
    daily_quota: int = 1
    timezone: str = "UTC"
    publish_window: dict = {"start": "09:00", "end": "18:00"}


class BrandCreate(BrandBase):
    workspace_id: Optional[UUID] = None


class BrandUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    website_url: Optional[str] = None
    product_url: Optional[str] = None
    competitor_urls: Optional[list[str]] = None
    primary_topic: Optional[str] = None
    audience: Optional[str] = None
    tone: Optional[str] = None
    style_guide: Optional[dict] = None
    messaging: Optional[dict] = None
    daily_quota: Optional[int] = None
    timezone: Optional[str] = None
    publish_window: Optional[dict] = None
    status: Optional[str] = None


class BrandOut(BrandBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    account_id: UUID
    workspace_id: UUID
    status: str
    created_at: datetime
    updated_at: datetime
