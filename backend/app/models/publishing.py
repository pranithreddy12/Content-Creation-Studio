from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPKMixin


class PublishChannel(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "publish_channels"
    __table_args__ = (UniqueConstraint("brand_id", "platform", "display_name", name="uq_channel"),)
    account_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"))
    brand_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("brands.id", ondelete="CASCADE"))
    platform: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    oauth_blob: Mapped[dict] = mapped_column(JSONB, nullable=False)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String, default="connected", nullable=False)


class Schedule(Base, UUIDPKMixin):
    __tablename__ = "schedules"
    account_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"))
    brand_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("brands.id", ondelete="CASCADE"))
    asset_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("content_assets.id", ondelete="CASCADE"))
    channel_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("publish_channels.id", ondelete="CASCADE"))
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String, default="pending", nullable=False, index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    external_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    external_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
