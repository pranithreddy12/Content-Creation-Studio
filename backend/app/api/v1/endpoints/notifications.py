"""Notifications — push-token registration + in-app notification list."""
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.api.deps import CurrentUser, DBSession, current_user
from app.models.notification import Notification, PushToken
from app.models.user import User
from app.services.provisioning import get_or_create_account

router = APIRouter()


class PushTokenIn(BaseModel):
    token: str
    platform: str  # "expo" | "ios" | "android"


async def _ensure_user(db, user: CurrentUser) -> User:
    # Also materialize the Account/Workspace so other endpoints see a consistent tenant.
    await get_or_create_account(db, user)
    u = (await db.execute(select(User).where(User.clerk_user_id == user.clerk_user_id))).scalar_one_or_none()
    if u:
        return u
    u = User(clerk_user_id=user.clerk_user_id, email=user.email or "", name=None)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@router.post("/register")
async def register_push(
    payload: PushTokenIn,
    db: DBSession,
    user: Annotated[CurrentUser, Depends(current_user)],
) -> dict:
    u = await _ensure_user(db, user)
    existing = (await db.execute(select(PushToken).where(PushToken.token == payload.token))).scalar_one_or_none()
    if existing:
        existing.platform = payload.platform
        existing.user_id = u.id
        await db.commit()
        return {"ok": True, "id": str(existing.id), "updated": True}
    pt = PushToken(
        user_id=u.id, platform=payload.platform, token=payload.token,
        created_at=datetime.now(timezone.utc),
    )
    db.add(pt)
    await db.commit()
    await db.refresh(pt)
    return {"ok": True, "id": str(pt.id), "updated": False}


@router.get("")
async def list_notifications(
    db: DBSession,
    user: Annotated[CurrentUser, Depends(current_user)],
    limit: int = 50,
) -> list[dict]:
    u = await _ensure_user(db, user)
    rows = (await db.execute(
        select(Notification).where(Notification.user_id == u.id)
        .order_by(Notification.created_at.desc()).limit(min(limit, 200))
    )).scalars().all()
    return [{
        "id": str(n.id), "kind": n.kind, "title": n.title, "body": n.body,
        "data": n.data, "read_at": n.read_at.isoformat() if n.read_at else None,
        "created_at": n.created_at.isoformat() if n.created_at else None,
    } for n in rows]
