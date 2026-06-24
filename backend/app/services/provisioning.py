"""Auto-provision Account + default Workspace on first authed request.

Why this exists: Clerk owns auth but doesn't know about our domain tables. The
first time a user hits any /v1 endpoint we materialize their Account (keyed by
clerk_org_id when in an org, otherwise a synthetic `user_<clerk_user_id>`) and a
default Workspace. Subsequent requests look it up.
"""
from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.auth import CurrentUser
from app.models.account import Account, Workspace


def _effective_org_id(user: CurrentUser) -> str:
    if user.clerk_org_id:
        return user.clerk_org_id
    return f"user_{user.clerk_user_id}"


async def get_or_create_account(db: AsyncSession, user: CurrentUser) -> Account:
    org_id = _effective_org_id(user)
    acct = (await db.execute(
        select(Account).where(Account.clerk_org_id == org_id)
    )).scalar_one_or_none()
    if acct:
        # A tombstoned account is mid-purge (hard delete pending) — reject every
        # request immediately rather than serving stale data or re-provisioning.
        if acct.deleted_at is not None:
            raise HTTPException(status.HTTP_410_GONE, "account is being deleted")
        return acct

    acct = Account(
        clerk_org_id=org_id,
        name=user.email or user.clerk_user_id or "Untitled Account",
        plan="free",
    )
    db.add(acct)
    await db.flush()
    db.add(Workspace(account_id=acct.id, name="Default"))
    await db.commit()
    await db.refresh(acct)
    return acct


async def default_workspace(db: AsyncSession, account: Account) -> Workspace:
    ws = (await db.execute(
        select(Workspace).where(Workspace.account_id == account.id).order_by(Workspace.created_at.asc())
    )).scalars().first()
    if ws:
        return ws
    ws = Workspace(account_id=account.id, name="Default")
    db.add(ws)
    await db.commit()
    await db.refresh(ws)
    return ws
