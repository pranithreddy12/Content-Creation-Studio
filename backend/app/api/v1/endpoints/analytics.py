"""Analytics rollups for the dashboard."""
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, DBSession, current_user
from app.models.analytics import AssetMetric
from app.models.brand import Brand
from app.models.content import ContentAsset
from app.models.publishing import Schedule, ScheduleStatus
from app.services.provisioning import get_or_create_account

router = APIRouter()


WINDOW_TO_HOURS = {"24h": 24, "7d": 24 * 7, "30d": 24 * 30, "90d": 24 * 90}


async def _account_brands(db: AsyncSession, user: CurrentUser, brand_id: Optional[UUID]) -> list[UUID]:
    acct = await get_or_create_account(db, user)
    q = select(Brand.id).where(Brand.account_id == acct.id)
    if brand_id:
        q = q.where(Brand.id == brand_id)
    rows = (await db.execute(q)).all()
    return [r[0] for r in rows]


@router.get("/overview")
async def overview(
    db: DBSession,
    user: Annotated[CurrentUser, Depends(current_user)],
    brand_id: Optional[UUID] = None,
) -> dict:
    brand_ids = await _account_brands(db, user, brand_id)
    if not brand_ids:
        return {"generated": 0, "scheduled": 0, "published": 0,
                "avg_viral_score": 0, "avg_seo_score": 0, "revenue_attributed": 0}
    generated = (await db.execute(
        select(func.count()).select_from(ContentAsset).where(ContentAsset.brand_id.in_(brand_ids))
    )).scalar() or 0
    scheduled = (await db.execute(
        select(func.count()).select_from(Schedule).where(
            Schedule.brand_id.in_(brand_ids),
            Schedule.status.in_((ScheduleStatus.PENDING, ScheduleStatus.PUBLISHING)),
        )
    )).scalar() or 0
    published = (await db.execute(
        select(func.count()).select_from(Schedule).where(
            Schedule.brand_id.in_(brand_ids), Schedule.status == ScheduleStatus.PUBLISHED
        )
    )).scalar() or 0
    return {
        "generated": generated,
        "scheduled": scheduled,
        "published": published,
        "avg_viral_score": 0.0,
        "avg_seo_score": 0.0,
        "revenue_attributed": 0.0,
    }


@router.get("/timeseries")
async def timeseries(
    db: DBSession,
    user: Annotated[CurrentUser, Depends(current_user)],
    brand_id: Optional[UUID] = None,
    window: Literal["24h", "7d", "30d", "90d"] = "30d",
) -> list[dict]:
    brand_ids = await _account_brands(db, user, brand_id)
    if not brand_ids:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=WINDOW_TO_HOURS[window])
    rows = (await db.execute(
        select(
            AssetMetric.platform,
            func.coalesce(func.sum(AssetMetric.views), 0).label("views"),
            func.coalesce(func.sum(AssetMetric.clicks), 0).label("clicks"),
            func.coalesce(func.sum(AssetMetric.shares), 0).label("shares"),
            func.coalesce(func.sum(AssetMetric.likes), 0).label("likes"),
            func.coalesce(func.sum(AssetMetric.comments), 0).label("comments"),
            func.coalesce(func.avg(AssetMetric.ctr), 0).label("ctr"),
            func.max(AssetMetric.collected_at).label("collected_at"),
        ).where(AssetMetric.brand_id.in_(brand_ids), AssetMetric.collected_at > cutoff)
        .group_by(AssetMetric.platform)
        .order_by(AssetMetric.platform)
    )).all()
    return [{
        "platform": r.platform,
        "views": int(r.views), "clicks": int(r.clicks), "shares": int(r.shares),
        "likes": int(r.likes), "comments": int(r.comments), "ctr": float(r.ctr or 0),
        "collected_at": r.collected_at.isoformat() if r.collected_at else None,
    } for r in rows]
