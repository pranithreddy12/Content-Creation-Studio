from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPKMixin


class AssetMetric(Base):
    __tablename__ = "asset_metrics"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    asset_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("content_assets.id", ondelete="CASCADE"))
    brand_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("brands.id", ondelete="CASCADE"))
    platform: Mapped[str] = mapped_column(String, nullable=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    views: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    clicks: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    shares: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    saves: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    comments: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    likes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    watch_time_s: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    ctr: Mapped[Optional[float]] = mapped_column(Numeric(8, 5), nullable=True)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)


class PatternScore(Base, UUIDPKMixin):
    __tablename__ = "pattern_scores"
    __table_args__ = (UniqueConstraint("brand_id", "pattern_key", "pattern_val", name="uq_pattern"),)
    brand_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("brands.id", ondelete="CASCADE"))
    pattern_key: Mapped[str] = mapped_column(String, nullable=False)
    pattern_val: Mapped[str] = mapped_column(String, nullable=False)
    ema_score: Mapped[float] = mapped_column(Numeric(8, 4), default=0, nullable=False)
    sample_n: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
