from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPKMixin


class ScheduleStatus:
    """Valid values for Schedule.status (string constants, not a PG enum — no
    migration needed). Keeps 'needs_review' from drifting to 'needs-review' etc.

      pending      → awaiting its scheduled_at / queued for a publish attempt
      publishing   → claimed by a worker, provider call in flight
      published    → provider accepted; external_id/url recorded (terminal, success)
      failed       → never posted (adapter raised pre-post); safe to re-schedule (terminal)
      needs_review → abandoned mid-publish; the post MAY be live — verify before
                     re-publishing (terminal, distinct from failed on purpose)
    """
    PENDING = "pending"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


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
    status: Mapped[str] = mapped_column(String, default=ScheduleStatus.PENDING, nullable=False, index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # When the row was last claimed (pending→publishing). The reaper measures
    # abandonment from THIS, not scheduled_at, so a row legitimately on retry
    # isn't reaped just because its scheduled_at is old.
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    external_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    external_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
