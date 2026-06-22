from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPKMixin


class ViralPost(Base, UUIDPKMixin):
    __tablename__ = "viral_posts"
    __table_args__ = (UniqueConstraint("platform", "external_id", name="uq_viral_external"),)
    platform: Mapped[str] = mapped_column(String, nullable=False)
    external_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    author: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    raw: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metrics: Mapped[dict] = mapped_column(JSONB, default=dict)
    posted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    crawled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    hash: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class ViralPattern(Base, UUIDPKMixin):
    __tablename__ = "viral_patterns"
    viral_post_id: Mapped[Optional[UUID]] = mapped_column(PGUUID(as_uuid=True), ForeignKey("viral_posts.id", ondelete="SET NULL"), nullable=True)
    platform: Mapped[str] = mapped_column(String, nullable=False)
    hook: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    structure: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cta: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    emotion: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    embedding_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
