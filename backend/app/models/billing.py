from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PlanLimit(Base):
    __tablename__ = "plan_limits"
    plan: Mapped[str] = mapped_column(String, primary_key=True)
    max_brands: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_workspaces: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    monthly_assets: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    monthly_video_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    monthly_publish_ops: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    monthly_llm_usd: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)


class UsageEvent(Base):
    __tablename__ = "usage_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"))
    brand_id: Mapped[Optional[UUID]] = mapped_column(PGUUID(as_uuid=True), ForeignKey("brands.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProcessedStripeEvent(Base):
    """Idempotency ledger for Stripe webhooks. event_id is Stripe's evt_… id;
    its presence means we've already applied that event's side effects."""
    __tablename__ = "processed_stripe_events"
    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    type: Mapped[str] = mapped_column(String, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
