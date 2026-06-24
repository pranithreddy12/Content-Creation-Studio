"""Real Stripe webhook HMAC signature verification.

Previous billing tests stubbed `stripe.Webhook.construct_event` directly. This
file exercises the *real* `stripe` library: we construct a signed payload using
the same HMAC-SHA256-of-`<timestamp>.<payload>` scheme Stripe uses, then prove:

  * a correctly-signed payload reaches our handler and updates account.plan
  * a payload signed with the WRONG secret is rejected (400 / RuntimeError)
  * a tampered payload (right secret, wrong body) is rejected
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid

import pytest
import stripe
from sqlalchemy import select, text

os.environ.setdefault("SECRET_KEY", "testtesttesttesttesttesttesttest")

from app.api.deps.auth import CurrentUser  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.account import Account  # noqa: E402
from app.services.billing import handle_webhook_event  # noqa: E402
from app.services.provisioning import get_or_create_account  # noqa: E402

TAG = f"test_whook_{uuid.uuid4().hex[:8]}"
WEBHOOK_SECRET = "whsec_test_unit_" + uuid.uuid4().hex


def _stripe_signed_header(payload: bytes, secret: str, timestamp: int | None = None) -> str:
    """Build a `Stripe-Signature` header value of the form `t=<ts>,v1=<hex>`."""
    ts = timestamp or int(time.time())
    signed_payload = f"{ts}.".encode() + payload
    mac = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


@pytest.fixture(autouse=True)
def patch_settings(monkeypatch):
    """Settings module was loaded at container start with empty Stripe env vars."""
    from app.core import config as cfg
    monkeypatch.setattr(cfg.settings, "stripe_secret_key", "sk_test_dummy")
    monkeypatch.setattr(cfg.settings, "stripe_price_pro", "price_pro_dummy")
    monkeypatch.setattr(cfg.settings, "stripe_price_agency", "price_agency_dummy")
    monkeypatch.setattr(cfg.settings, "stripe_webhook_secret", WEBHOOK_SECRET)
    yield


@pytest.fixture()
async def acct_with_stripe_customer():
    """Create an Account with a known stripe_customer id so the webhook can find it."""
    user = CurrentUser(clerk_user_id=f"{TAG}_u", clerk_org_id=f"{TAG}_o_{uuid.uuid4().hex[:6]}",
                       email="w@test.local", role="owner", raw={})
    cus_id = f"cus_test_{uuid.uuid4().hex[:8]}"
    async with SessionLocal() as db:
        acct = await get_or_create_account(db, user)
        acct.stripe_customer = cus_id
        await db.commit()
    yield acct, cus_id
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM processed_stripe_events WHERE event_id LIKE :p"),
                         {"p": f"evt_{TAG}%"})
        await db.execute(text("DELETE FROM accounts WHERE id = :a"), {"a": acct.id})
        await db.commit()


@pytest.mark.asyncio
async def test_valid_signature_processes_subscription_event(acct_with_stripe_customer):
    acct, cus_id = acct_with_stripe_customer
    payload_dict = {
        "id": f"evt_{TAG}_signed",
        "object": "event",
        "type": "customer.subscription.created",
        "data": {"object": {
            "id": "sub_xxx",
            "customer": cus_id,
            "items": {"data": [{"price": {"id": "price_pro_dummy"}}]},
        }},
    }
    payload = json.dumps(payload_dict).encode()
    sig = _stripe_signed_header(payload, WEBHOOK_SECRET)

    async with SessionLocal() as db:
        result = await handle_webhook_event(db, payload, sig)
    assert result["ok"] is True
    assert result["type"] == "customer.subscription.created"

    async with SessionLocal() as db:
        row = (await db.execute(select(Account).where(Account.id == acct.id))).scalar_one()
        assert row.plan == "pro"


@pytest.mark.asyncio
async def test_wrong_secret_is_rejected(acct_with_stripe_customer):
    acct, cus_id = acct_with_stripe_customer
    payload = json.dumps({
        "id": "evt_x", "object": "event",
        "type": "customer.subscription.created",
        "data": {"object": {"customer": cus_id, "items": {"data": []}}},
    }).encode()
    bogus_sig = _stripe_signed_header(payload, "whsec_attacker_guess")

    async with SessionLocal() as db:
        with pytest.raises(stripe.SignatureVerificationError):
            await handle_webhook_event(db, payload, bogus_sig)


@pytest.mark.asyncio
async def test_tampered_payload_is_rejected(acct_with_stripe_customer):
    acct, cus_id = acct_with_stripe_customer
    original = json.dumps({
        "id": "evt_y", "object": "event",
        "type": "customer.subscription.created",
        "data": {"object": {"customer": cus_id, "items": {"data": []}}},
    }).encode()
    sig = _stripe_signed_header(original, WEBHOOK_SECRET)

    # Attacker tampers with the body but reuses the signature.
    tampered = original.replace(b"customer.subscription.created",
                                b"customer.subscription.deleted")
    async with SessionLocal() as db:
        with pytest.raises(stripe.SignatureVerificationError):
            await handle_webhook_event(db, tampered, sig)


@pytest.mark.asyncio
async def test_stale_timestamp_is_rejected(acct_with_stripe_customer):
    """Stripe rejects signatures whose timestamp is too far in the past (>5 min by default)."""
    acct, cus_id = acct_with_stripe_customer
    payload = json.dumps({
        "id": "evt_z", "object": "event",
        "type": "customer.subscription.created",
        "data": {"object": {"customer": cus_id, "items": {"data": []}}},
    }).encode()
    old_ts = int(time.time()) - (60 * 60)  # 1 hour ago
    sig = _stripe_signed_header(payload, WEBHOOK_SECRET, timestamp=old_ts)
    async with SessionLocal() as db:
        with pytest.raises(stripe.SignatureVerificationError):
            await handle_webhook_event(db, payload, sig)
