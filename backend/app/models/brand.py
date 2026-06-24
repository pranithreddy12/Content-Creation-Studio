from typing import Optional
from uuid import UUID

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPKMixin


class Brand(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "brands"
    __table_args__ = (UniqueConstraint("account_id", "slug", name="uq_brand_account_slug"),)

    account_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    website_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    product_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    competitor_urls: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    primary_topic: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    audience: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    tone: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    style_guide: Mapped[dict] = mapped_column(JSONB, default=dict)
    messaging: Mapped[dict] = mapped_column(JSONB, default=dict)
    daily_quota: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    timezone: Mapped[str] = mapped_column(String, default="UTC", nullable=False)
    publish_window: Mapped[dict] = mapped_column(JSONB, default=lambda: {"start": "09:00", "end": "18:00"})
    status: Mapped[str] = mapped_column(String, default="active", nullable=False)
