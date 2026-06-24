from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.auth import CurrentUser
from app.models.account import Account, Workspace
from app.models.brand import Brand
from app.schemas.brand import BrandCreate, BrandUpdate
from app.services.provisioning import default_workspace, get_or_create_account


async def _resolve_account(db: AsyncSession, user: CurrentUser) -> Account:
    return await get_or_create_account(db, user)


async def list_for_user(db: AsyncSession, user: CurrentUser) -> list[Brand]:
    account = await _resolve_account(db, user)
    res = await db.execute(
        select(Brand).where(Brand.account_id == account.id).order_by(Brand.created_at.desc())
    )
    return list(res.scalars().all())


async def get(db: AsyncSession, user: CurrentUser, brand_id: UUID) -> Brand | None:
    account = await _resolve_account(db, user)
    res = await db.execute(
        select(Brand).where(Brand.id == brand_id, Brand.account_id == account.id)
    )
    return res.scalar_one_or_none()


async def create(db: AsyncSession, user: CurrentUser, payload: BrandCreate) -> Brand:
    account = await _resolve_account(db, user)
    if payload.workspace_id:
        # A supplied workspace_id must belong to the caller's account — otherwise
        # a brand could be planted inside another tenant's workspace.
        ws = (await db.execute(
            select(Workspace).where(
                Workspace.id == payload.workspace_id,
                Workspace.account_id == account.id,
            )
        )).scalar_one_or_none()
        if not ws:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "workspace not found")
        ws_id = ws.id
    else:
        ws_id = (await default_workspace(db, account)).id
    brand = Brand(
        account_id=account.id,
        workspace_id=ws_id,
        name=payload.name,
        slug=payload.slug,
        description=payload.description,
        website_url=payload.website_url,
        product_url=payload.product_url,
        competitor_urls=payload.competitor_urls,
        primary_topic=payload.primary_topic,
        audience=payload.audience,
        tone=payload.tone,
        style_guide=payload.style_guide,
        messaging=payload.messaging,
        daily_quota=payload.daily_quota,
        timezone=payload.timezone,
        publish_window=payload.publish_window,
    )
    db.add(brand)
    await db.commit()
    await db.refresh(brand)
    return brand


async def update(
    db: AsyncSession, user: CurrentUser, brand_id: UUID, payload: BrandUpdate
) -> Brand:
    brand = await get(db, user, brand_id)
    if not brand:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "brand not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(brand, k, v)
    brand.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(brand)
    return brand


async def soft_delete(db: AsyncSession, user: CurrentUser, brand_id: UUID) -> None:
    brand = await get(db, user, brand_id)
    if not brand:
        return
    brand.status = "archived"
    await db.commit()
