"""External webhooks + push token registration."""
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import CurrentUser, DBSession, current_user
from app.models.notification import PushToken
from app.services.billing import handle_webhook_event

router = APIRouter()


class PushTokenIn(BaseModel):
    token: str
    platform: str  # "expo" | "ios" | "android"


@router.post("/stripe", include_in_schema=False)
async def stripe_webhook(
    request: Request,
    db: DBSession,
    stripe_signature: str | None = Header(default=None, alias="stripe-signature"),
):
    if not stripe_signature:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "missing signature")
    payload = await request.body()
    try:
        return await handle_webhook_event(db, payload, stripe_signature)
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"webhook verification failed: {exc}")
