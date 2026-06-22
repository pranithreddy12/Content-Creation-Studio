from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.auth import CurrentUser
from app.models.account import Account
from app.models.brand import Brand
from app.models.source import Source
from app.schemas.source import SourceCreate, SourceUploadInit
from app.services.provisioning import get_or_create_account
from app.utils.storage import s3
from app.core.config import settings


async def _account(db: AsyncSession, user: CurrentUser) -> Account:
    return await get_or_create_account(db, user)


async def _own_brand(db: AsyncSession, acct: Account, brand_id: UUID) -> Brand:
    res = await db.execute(
        select(Brand).where(Brand.id == brand_id, Brand.account_id == acct.id)
    )
    brand = res.scalar_one_or_none()
    if not brand:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "brand not found")
    return brand


async def create_source(db: AsyncSession, user: CurrentUser, payload: SourceCreate) -> Source:
    acct = await _account(db, user)
    await _own_brand(db, acct, payload.brand_id)
    src = Source(
        account_id=acct.id,
        brand_id=payload.brand_id,
        kind=payload.kind,
        title=payload.title,
        url=payload.url,
        raw_text=payload.raw_text,
        storage_key=payload.storage_key,
        meta=payload.meta,
        status="pending",
    )
    db.add(src)
    await db.commit()
    await db.refresh(src)
    return src


def make_upload_intent(payload: SourceUploadInit) -> dict:
    key = f"sources/{payload.brand_id}/{uuid4()}-{payload.filename}"
    url = s3().generate_presigned_url(
        "put_object",
        Params={
            "Bucket": settings.s3_bucket,
            "Key": key,
            "ContentType": payload.content_type,
        },
        ExpiresIn=3600,
    )
    return {"storage_key": key, "upload_url": url, "expires_in": 3600}


async def list_for_brand(db: AsyncSession, user: CurrentUser, brand_id: UUID) -> list[Source]:
    acct = await _account(db, user)
    await _own_brand(db, acct, brand_id)
    res = await db.execute(
        select(Source).where(Source.brand_id == brand_id).order_by(Source.created_at.desc())
    )
    return list(res.scalars().all())
