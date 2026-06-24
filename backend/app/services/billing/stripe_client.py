"""Stripe wrappers — Checkout, Portal, webhook handler."""
from __future__ import annotations

from datetime import datetime, timezone

import stripe
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import log
from app.models.account import Account
from app.models.billing import ProcessedStripeEvent


def _client() -> stripe.StripeClient:
    if not settings.stripe_secret_key:
        raise RuntimeError("STRIPE_SECRET_KEY missing")
    stripe.api_key = settings.stripe_secret_key
    return stripe


PLAN_TO_PRICE = {
    "pro":    lambda: settings.stripe_price_pro,
    "agency": lambda: settings.stripe_price_agency,
}


async def ensure_customer(db: AsyncSession, account: Account, email: str | None) -> str:
    if account.stripe_customer:
        return account.stripe_customer
    s = _client()
    cust = s.Customer.create(email=email, metadata={"account_id": str(account.id)})
    account.stripe_customer = cust["id"]
    await db.commit()
    return cust["id"]


async def create_checkout_session(
    db: AsyncSession, account: Account, plan: str, email: str | None,
    success_url: str, cancel_url: str,
) -> str:
    if plan not in PLAN_TO_PRICE:
        raise ValueError(f"unknown plan: {plan}")
    price_id = PLAN_TO_PRICE[plan]()
    if not price_id:
        raise RuntimeError(f"price id not configured for plan {plan}")
    customer = await ensure_customer(db, account, email)
    s = _client()
    sess = s.checkout.Session.create(
        mode="subscription",
        customer=customer,
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        allow_promotion_codes=True,
        metadata={"account_id": str(account.id), "plan": plan},
    )
    return sess["url"]


async def create_portal_session(
    db: AsyncSession, account: Account, email: str | None, return_url: str,
) -> str:
    customer = await ensure_customer(db, account, email)
    s = _client()
    sess = s.billing_portal.Session.create(customer=customer, return_url=return_url)
    return sess["url"]


async def handle_webhook_event(db: AsyncSession, payload: bytes, sig: str) -> dict:
    """Verify + dispatch Stripe webhook events. Updates account.plan when subs change.

    Idempotent: Stripe redelivers events (at-least-once). We dedup INSERT-FIRST —
    the ProcessedStripeEvent PK is the arbiter, not an application-level read — so
    two workers racing the same event_id can't both apply it: the loser hits a
    unique violation and acks as a duplicate. The insert and the side effect share
    one transaction, so a crash mid-processing leaves the event un-recorded and the
    redelivery re-applies it (never recorded-but-unapplied).
    """
    if not settings.stripe_webhook_secret:
        raise RuntimeError("STRIPE_WEBHOOK_SECRET missing")
    s = _client()
    # construct_event raises on bad/missing signature — BEFORE any DB write.
    event = s.Webhook.construct_event(payload, sig, settings.stripe_webhook_secret)
    et = event["type"]
    obj = event["data"]["object"]
    event_id = event.get("id")
    if event_id is None:
        # Signature already verified; a verified event without an id is an anomaly,
        # not a normal path — refuse it rather than process unprotected by dedup.
        raise ValueError("verified stripe event missing id")

    # Insert-first dedup inside a SAVEPOINT: a unique violation means another
    # delivery already claimed this event_id → ack as duplicate, skip side effect.
    try:
        async with db.begin_nested():
            db.add(ProcessedStripeEvent(
                event_id=event_id, type=et, received_at=datetime.now(timezone.utc),
            ))
            await db.flush()
    except IntegrityError:
        log.info("stripe_webhook_duplicate", type=et, event_id=event_id)
        return {"ok": True, "type": et, "deduped": True}

    # Side effects run in the SAME outer transaction as the dedup insert (helpers
    # no longer commit on their own) — we commit exactly once below.
    if et in ("customer.subscription.created", "customer.subscription.updated"):
        plan = _plan_from_subscription(obj)
        await _update_account_plan(db, obj["customer"], plan)
    elif et == "customer.subscription.deleted":
        await _update_account_plan(db, obj["customer"], "free")
    elif et == "checkout.session.completed":
        meta = obj.get("metadata") or {}
        if meta.get("account_id") and meta.get("plan"):
            await _update_account_plan_by_id(db, meta["account_id"], meta["plan"])

    await db.commit()
    log.info("stripe_webhook", type=et)
    return {"ok": True, "type": et}


def _plan_from_subscription(sub: dict) -> str:
    items = (sub.get("items") or {}).get("data") or []
    price_ids = {(it.get("price") or {}).get("id") for it in items}
    if settings.stripe_price_agency and settings.stripe_price_agency in price_ids:
        return "agency"
    if settings.stripe_price_pro and settings.stripe_price_pro in price_ids:
        return "pro"
    return "free"


async def _update_account_plan(db: AsyncSession, customer_id: str, plan: str) -> None:
    # No commit here — handle_webhook_event commits once so the plan change and the
    # idempotency-ledger insert land atomically.
    acct = (await db.execute(
        select(Account).where(Account.stripe_customer == customer_id)
    )).scalar_one_or_none()
    if not acct:
        return
    acct.plan = plan


async def _update_account_plan_by_id(db: AsyncSession, account_id: str, plan: str) -> None:
    from uuid import UUID
    try:
        acct_uuid = UUID(account_id)
    except (ValueError, TypeError):
        log.warning("stripe_webhook_bad_account_id", account_id=account_id)
        return
    acct = (await db.execute(select(Account).where(Account.id == acct_uuid))).scalar_one_or_none()
    if not acct:
        return
    acct.plan = plan
