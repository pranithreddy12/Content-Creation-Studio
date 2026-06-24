"""Content assets — list, get, approve/reject/schedule."""
from datetime import datetime, timezone
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, DBSession, current_user
from app.models.brand import Brand
from app.models.content import ContentAsset
from app.services.provisioning import get_or_create_account

router = APIRouter()


async def _owned_brand_ids(db: AsyncSession, user: CurrentUser, brand_id: Optional[UUID] = None) -> list[UUID]:
    acct = await get_or_create_account(db, user)
    q = select(Brand.id).where(Brand.account_id == acct.id)
    if brand_id:
        q = q.where(Brand.id == brand_id)
    return [r[0] for r in (await db.execute(q)).all()]


async def _owned_asset(db: AsyncSession, user: CurrentUser, asset_id: UUID) -> ContentAsset:
    acct = await get_or_create_account(db, user)
    asset = (await db.execute(
        select(ContentAsset)
        .join(Brand, Brand.id == ContentAsset.brand_id)
        .where(ContentAsset.id == asset_id, Brand.account_id == acct.id)
    )).scalar_one_or_none()
    if not asset:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")
    return asset


def _serialize(a: ContentAsset) -> dict:
    return {
        "id": str(a.id),
        "brand_id": str(a.brand_id),
        "idea_id": str(a.idea_id),
        "format": a.format,
        "title": a.title,
        "body": a.body,
        "body_json": a.body_json,
        "word_count": a.word_count,
        "seo": a.seo,
        "status": a.status,
        "approval_state": a.approval_state,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "updated_at": a.updated_at.isoformat() if a.updated_at else None,
    }


@router.get("")
async def list_assets(
    db: DBSession,
    user: Annotated[CurrentUser, Depends(current_user)],
    brand_id: Optional[UUID] = None,
    status: Optional[str] = Query(None),
    format: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    brand_ids = await _owned_brand_ids(db, user, brand_id)
    if not brand_ids:
        return []
    q = select(ContentAsset).where(ContentAsset.brand_id.in_(brand_ids))
    if status:
        q = q.where(ContentAsset.status == status)
    if format:
        q = q.where(ContentAsset.format == format)
    # Tiebreaker on `id` so paginated lists are deterministic when many rows share created_at
    # (happens when a single batch dispatch inserts many assets inside one transaction).
    q = q.order_by(ContentAsset.created_at.desc(), ContentAsset.id.desc()).limit(min(limit, 200))
    rows = (await db.execute(q)).scalars().all()
    return [_serialize(a) for a in rows]


@router.get("/{asset_id}")
async def get_asset(
    asset_id: UUID,
    db: DBSession,
    user: Annotated[CurrentUser, Depends(current_user)],
) -> dict:
    a = await _owned_asset(db, user, asset_id)
    return _serialize(a)


@router.post("/{asset_id}/approve")
async def approve(
    asset_id: UUID,
    db: DBSession,
    user: Annotated[CurrentUser, Depends(current_user)],
) -> dict:
    a = await _owned_asset(db, user, asset_id)
    a.status = "approved"
    a.approval_state = {**(a.approval_state or {}),
                        "approver": user.clerk_user_id,
                        "approved_at": datetime.now(timezone.utc).isoformat()}
    a.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return _serialize(a)


@router.post("/{asset_id}/reject")
async def reject(
    asset_id: UUID,
    db: DBSession,
    user: Annotated[CurrentUser, Depends(current_user)],
) -> dict:
    a = await _owned_asset(db, user, asset_id)
    a.status = "draft"
    a.approval_state = {**(a.approval_state or {}),
                        "rejected_by": user.clerk_user_id,
                        "rejected_at": datetime.now(timezone.utc).isoformat()}
    a.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return _serialize(a)


@router.post("/{asset_id}/schedule")
async def schedule_asset(
    asset_id: UUID,
    db: DBSession,
    user: Annotated[CurrentUser, Depends(current_user)],
) -> dict:
    a = await _owned_asset(db, user, asset_id)
    a.status = "scheduled"
    a.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return _serialize(a)
