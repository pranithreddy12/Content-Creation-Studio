from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPKMixin


class ContentIdea(Base, UUIDPKMixin):
    __tablename__ = "content_ideas"
    account_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"))
    brand_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    research_id: Mapped[Optional[UUID]] = mapped_column(PGUUID(as_uuid=True), ForeignKey("research_runs.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    angle: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    keywords: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    audience: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    format_hints: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    search_volume: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    trend_velocity: Mapped[Optional[float]] = mapped_column(Numeric(6, 3), nullable=True)
    competition: Mapped[Optional[float]] = mapped_column(Numeric(6, 3), nullable=True)
    engagement_est: Mapped[Optional[float]] = mapped_column(Numeric(6, 3), nullable=True)
    composite_score: Mapped[Optional[float]] = mapped_column(Numeric(6, 3), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String, default="new", nullable=False, index=True)
    selected_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ContentAsset(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "content_assets"
    account_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"))
    brand_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    idea_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("content_ideas.id", ondelete="CASCADE"), index=True)
    format: Mapped[str] = mapped_column(String, nullable=False, index=True)
    title: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    body_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    word_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    seo: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String, default="draft", nullable=False, index=True)
    approval_state: Mapped[dict] = mapped_column(JSONB, default=dict)
    generated_by: Mapped[Optional[UUID]] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    parent_asset_id: Mapped[Optional[UUID]] = mapped_column(PGUUID(as_uuid=True), ForeignKey("content_assets.id", ondelete="SET NULL"), nullable=True)


class MediaAsset(Base, UUIDPKMixin):
    __tablename__ = "media_assets"
    account_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"))
    brand_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("brands.id", ondelete="CASCADE"))
    asset_id: Mapped[Optional[UUID]] = mapped_column(PGUUID(as_uuid=True), ForeignKey("content_assets.id", ondelete="CASCADE"), nullable=True)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    storage_key: Mapped[str] = mapped_column(String, nullable=False)
    mime_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    duration_sec: Mapped[Optional[float]] = mapped_column(Numeric(8, 2), nullable=True)
    prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    provider: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class VideoRender(Base, UUIDPKMixin):
    __tablename__ = "video_renders"
    asset_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("content_assets.id", ondelete="CASCADE"))
    brand_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("brands.id", ondelete="CASCADE"))
    format: Mapped[str] = mapped_column(String, nullable=False)
    script_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    storyboard: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    storage_key: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    duration_sec: Mapped[Optional[float]] = mapped_column(Numeric(8, 2), nullable=True)
    status: Mapped[str] = mapped_column(String, default="queued", nullable=False)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cost_usd: Mapped[Optional[float]] = mapped_column(Numeric(10, 4), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
