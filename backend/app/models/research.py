from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPKMixin


class ResearchRun(Base, UUIDPKMixin):
    __tablename__ = "research_runs"
    brand_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("brands.id", ondelete="CASCADE"))
    account_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String, default="running", nullable=False)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)


class ResearchItem(Base, UUIDPKMixin):
    __tablename__ = "research_items"
    research_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("research_runs.id", ondelete="CASCADE"))
    brand_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("brands.id", ondelete="CASCADE"))
    channel: Mapped[str] = mapped_column(String, nullable=False)
    external_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    title: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    excerpt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    posted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    engagement: Mapped[dict] = mapped_column(JSONB, default=dict)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)


class Opportunity(Base, UUIDPKMixin):
    __tablename__ = "opportunities"
    brand_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("brands.id", ondelete="CASCADE"))
    research_id: Mapped[Optional[UUID]] = mapped_column(PGUUID(as_uuid=True), ForeignKey("research_runs.id", ondelete="SET NULL"), nullable=True)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[float] = mapped_column(Numeric(6, 3), default=0)
    attrs: Mapped[dict] = mapped_column(JSONB, default=dict)
