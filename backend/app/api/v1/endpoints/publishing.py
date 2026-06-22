"""Publishing endpoints — OAuth init/callback + channel CRUD + webhooks."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import CurrentUser, DBSession, current_user
from app.integrations.oauth import build_auth_url, exchange_code, PLATFORM_OAUTH
from app.models.account import Account
from app.models.brand import Brand
from app.models.publishing import PublishChannel
from app.core.security import encrypt
from app.services.provisioning import get_or_create_account
import json

router = APIRouter()


class WordPressCreate(BaseModel):
    brand_id: UUID
    site: str
    username: str
    app_password: str
    display_name: str | None = None


class RedditChannelCreate(BaseModel):
    brand_id: UUID
    subreddit: str


class EmailChannelCreate(BaseModel):
    brand_id: UUID
    api_key: str
    sender_name: str
    sender_email: str
    list_ids: list[int] = []


@router.get("/oauth/start")
async def oauth_start(
    platform: str,
    brand_id: UUID,
    redirect_uri: str,
    _: Annotated[CurrentUser, Depends(current_user)],
) -> dict:
    if platform not in PLATFORM_OAUTH:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unsupported platform: {platform}")
    try:
        return await build_auth_url(platform, str(brand_id), redirect_uri)
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


class OAuthStartRequest(BaseModel):
    platform: str
    brand_id: UUID
    redirect_uri: str
    client_id: str | None = None
    client_secret: str | None = None


@router.post("/oauth/start")
async def oauth_start_byo(
    payload: OAuthStartRequest,
    _: Annotated[CurrentUser, Depends(current_user)],
) -> dict:
    """Same as GET /oauth/start, but accepts a BYO `client_id`/`client_secret`
    so users can connect using their own OAuth app instead of an admin-configured one."""
    if payload.platform not in PLATFORM_OAUTH:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unsupported platform: {payload.platform}")
    try:
        return await build_auth_url(
            payload.platform,
            str(payload.brand_id),
            payload.redirect_uri,
            client_id=payload.client_id,
            client_secret=payload.client_secret,
        )
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


@router.get("/oauth/callback")
async def oauth_callback(
    db: DBSession,
    user: Annotated[CurrentUser, Depends(current_user)],
    platform: str = Query(...),
    code: str = Query(...),
    state: str = Query(...),
    redirect_uri: str = Query(...),
):
    try:
        result = await exchange_code(platform, code, state, redirect_uri)
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    acct = await get_or_create_account(db, user)
    brand = (await db.execute(
        select(Brand).where(Brand.id == UUID(result["brand_id"]), Brand.account_id == acct.id)
    )).scalar_one_or_none()
    if not brand:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "brand not found")
    ch = PublishChannel(
        account_id=acct.id,
        brand_id=brand.id,
        platform=platform,
        display_name=f"{platform}@{brand.slug}",
        oauth_blob={"ct": result["oauth_blob"]},
        status="connected",
    )
    db.add(ch)
    await db.commit()
    await db.refresh(ch)
    return {"id": str(ch.id), "platform": platform, "status": "connected"}


@router.post("/wordpress")
async def create_wordpress_channel(
    payload: WordPressCreate,
    db: DBSession,
    user: Annotated[CurrentUser, Depends(current_user)],
):
    acct = await get_or_create_account(db, user)
    blob = encrypt(json.dumps({
        "site": payload.site,
        "username": payload.username,
        "app_password": payload.app_password,
    }))
    ch = PublishChannel(
        account_id=acct.id,
        brand_id=payload.brand_id,
        platform="wordpress",
        display_name=payload.display_name or payload.site,
        oauth_blob={"ct": blob},
        status="connected",
    )
    db.add(ch)
    await db.commit()
    await db.refresh(ch)
    return {"id": str(ch.id)}


@router.post("/email")
async def create_email_channel(
    payload: EmailChannelCreate,
    db: DBSession,
    user: Annotated[CurrentUser, Depends(current_user)],
):
    acct = await get_or_create_account(db, user)
    blob = encrypt(json.dumps({
        "api_key": payload.api_key,
        "sender": {"name": payload.sender_name, "email": payload.sender_email},
        "list_ids": payload.list_ids,
    }))
    ch = PublishChannel(
        account_id=acct.id,
        brand_id=payload.brand_id,
        platform="email",
        display_name=payload.sender_email,
        oauth_blob={"ct": blob},
        status="connected",
    )
    db.add(ch)
    await db.commit()
    await db.refresh(ch)
    return {"id": str(ch.id)}


@router.get("/channels/{brand_id}")
async def list_channels(
    brand_id: UUID,
    db: DBSession,
    _: Annotated[CurrentUser, Depends(current_user)],
):
    rows = (await db.execute(
        select(PublishChannel).where(PublishChannel.brand_id == brand_id)
    )).scalars().all()
    return [{"id": str(r.id), "platform": r.platform, "display_name": r.display_name,
             "status": r.status, "meta": r.meta} for r in rows]


@router.delete("/channels/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_channel(
    channel_id: UUID,
    db: DBSession,
    _: Annotated[CurrentUser, Depends(current_user)],
):
    ch = (await db.execute(select(PublishChannel).where(PublishChannel.id == channel_id))).scalar_one()
    ch.status = "disconnected"
    await db.commit()
