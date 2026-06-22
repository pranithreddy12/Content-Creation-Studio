"""Usage metering + plan enforcement.

Writes UsageEvent rows on every billable action; aggregates them lazily for
enforcement. Plan limits live in `plan_limits`. The default plan_limits seed
data lives in migrations.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.billing import PlanLimit, UsageEvent


UsageKind = Literal["asset_generated", "video_minute", "publish_op", "llm_usd"]


async def meter(
    db: AsyncSession,
    *,
    account_id: UUID,
    brand_id: UUID | None,
    kind: UsageKind,
    amount: float,
    meta: dict | None = None,
) -> None:
    db.add(UsageEvent(
        account_id=account_id,
        brand_id=brand_id,
        kind=kind,
        amount=amount,
        meta=meta or {},
        occurred_at=datetime.now(timezone.utc),
    ))
    await db.commit()


async def current_usage(
    db: AsyncSession, account_id: UUID, *, since: datetime
) -> dict[str, float]:
    rows = (await db.execute(
        select(UsageEvent.kind, func.sum(UsageEvent.amount))
        .where(UsageEvent.account_id == account_id, UsageEvent.occurred_at >= since)
        .group_by(UsageEvent.kind)
    )).all()
    return {kind: float(total or 0) for kind, total in rows}


async def enforce(
    db: AsyncSession,
    account_id: UUID,
    kind: UsageKind,
    amount: float,
) -> None:
    acct = (await db.execute(select(Account).where(Account.id == account_id))).scalar_one()
    limits = (await db.execute(select(PlanLimit).where(PlanLimit.plan == acct.plan))).scalar_one_or_none()
    if limits is None:
        return
    month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    usage = await current_usage(db, account_id, since=month_start)
    cap_map: dict[str, int | float | None] = {
        "asset_generated": limits.monthly_assets,
        "video_minute":    limits.monthly_video_minutes,
        "publish_op":      limits.monthly_publish_ops,
        "llm_usd":         float(limits.monthly_llm_usd) if limits.monthly_llm_usd is not None else None,
    }
    cap = cap_map.get(kind)
    if cap is None:
        return
    used = usage.get(kind, 0)
    if used + amount > cap:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            f"monthly cap reached for {kind} on plan {acct.plan} ({used:.2f}/{cap})",
        )
