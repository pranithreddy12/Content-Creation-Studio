from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.api.deps import CurrentUser, DBSession, current_user
from app.models.brand import Brand
from app.models.content import ContentAsset
from app.models.publishing import Schedule
from app.services.provisioning import get_or_create_account

router = APIRouter()


@router.get("")
async def calendar(
    db: DBSession,
    user: Annotated[CurrentUser, Depends(current_user)],
    brand_id: UUID = Query(...),
    from_: datetime = Query(..., alias="from"),
    to: datetime = Query(...),
) -> list[dict]:
    acct = await get_or_create_account(db, user)
    brand = (await db.execute(
        select(Brand).where(Brand.id == brand_id, Brand.account_id == acct.id)
    )).scalar_one_or_none()
    if not brand:
        return []
    rows = (await db.execute(
        select(Schedule, ContentAsset.format, ContentAsset.title)
        .join(ContentAsset, ContentAsset.id == Schedule.asset_id)
        .where(Schedule.brand_id == brand_id,
               Schedule.scheduled_at >= from_,
               Schedule.scheduled_at <= to)
        .order_by(Schedule.scheduled_at.asc())
    )).all()
    return [{
        "id": str(s.id), "brand_id": str(s.brand_id), "asset_id": str(s.asset_id),
        "channel_id": str(s.channel_id),
        "scheduled_at": s.scheduled_at.isoformat(),
        "status": s.status,
        "external_url": s.external_url,
        "format": fmt, "title": title,
    } for s, fmt, title in rows]
