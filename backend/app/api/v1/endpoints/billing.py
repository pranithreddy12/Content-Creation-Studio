from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import CurrentUser, DBSession, current_user
from app.models.account import Account
from app.services.billing import (
    create_checkout_session,
    create_portal_session,
    current_usage,
)
from app.services.provisioning import get_or_create_account

router = APIRouter()


class CheckoutRequest(BaseModel):
    plan: Literal["pro", "agency"]
    success_url: str
    cancel_url: str


class PortalRequest(BaseModel):
    return_url: str


async def _account(db, user: CurrentUser) -> Account:
    return await get_or_create_account(db, user)


@router.post("/checkout")
async def checkout(
    payload: CheckoutRequest,
    db: DBSession,
    user: Annotated[CurrentUser, Depends(current_user)],
):
    acct = await _account(db, user)
    try:
        url = await create_checkout_session(db, acct, payload.plan, user.email, payload.success_url, payload.cancel_url)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    return {"url": url}


@router.post("/portal")
async def portal(
    payload: PortalRequest,
    db: DBSession,
    user: Annotated[CurrentUser, Depends(current_user)],
):
    acct = await _account(db, user)
    try:
        url = await create_portal_session(db, acct, user.email, payload.return_url)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    return {"url": url}


@router.get("/usage")
async def usage(db: DBSession, user: Annotated[CurrentUser, Depends(current_user)]):
    acct = await _account(db, user)
    month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    rows = await current_usage(db, acct.id, since=month_start)
    return {"plan": acct.plan, "since": month_start.isoformat(), "totals": rows}
