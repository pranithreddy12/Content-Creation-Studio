"""End-to-end propagation: Checkout Session.metadata → Webhook → account.plan.

Verifies the two-hop guarantee that lets a fresh Stripe customer be promoted to
a paid plan without the system having stored that mapping anywhere beforehand:

  1. POST /v1/billing/checkout creates a Checkout Session with metadata.account_id
     and metadata.plan baked in by the server.
  2. When Stripe later fires `checkout.session.completed` with that same metadata,
     the webhook handler resolves account_id → account row and sets plan accordingly.

Also covers the production-bug fix where a malformed metadata.account_id (spoofed
or corrupted) previously raised ValueError in UUID() and crashed the webhook with
a 500. Now: log and skip, return ok.
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import select, text

os.environ.setdefault("SECRET_KEY", "testtesttesttesttesttesttesttest")
os.environ.setdefault("STRIPE_SECRET_KEY", "sk_test_unit_dummy")
os.environ.setdefault("STRIPE_PRICE_PRO", "price_pro_dummy")
os.environ.setdefault("STRIPE_PRICE_AGENCY", "price_agency_dummy")
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "whsec_dummy")

from app.api.deps.auth import CurrentUser  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.account import Account  # noqa: E402
from app.services.billing import (  # noqa: E402
    create_checkout_session,
    handle_webhook_event,
)
from app.services.provisioning import get_or_create_account  # noqa: E402

TAG = f"test_smeta_{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
def patch_stripe_settings(monkeypatch):
    from app.core import config as cfg
    monkeypatch.setattr(cfg.settings, "stripe_secret_key", "sk_test_unit_dummy")
    monkeypatch.setattr(cfg.settings, "stripe_price_pro", "price_pro_dummy")
    monkeypatch.setattr(cfg.settings, "stripe_price_agency", "price_agency_dummy")
    monkeypatch.setattr(cfg.settings, "stripe_webhook_secret", "whsec_dummy")
    yield


@pytest.fixture()
async def acct():
    user = CurrentUser(clerk_user_id=f"{TAG}_u", clerk_org_id=f"{TAG}_o",
                       email="m@test.local", role="owner", raw={})
    async with SessionLocal() as db:
        a = await get_or_create_account(db, user)
    yield a
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM processed_stripe_events WHERE event_id LIKE :p"),
                         {"p": f"evt_{TAG}%"})
        await db.execute(text("DELETE FROM accounts WHERE clerk_org_id LIKE :p"),
                         {"p": f"{TAG}%"})
        await db.commit()


@pytest.mark.asyncio
async def test_checkout_metadata_round_trips_through_webhook_to_update_plan(monkeypatch, acct):
    """Full propagation: checkout creates metadata, webhook consumes it, plan flips."""
    from app.services.billing import stripe_client

    captured_metadata: dict = {}

    class FakeCustomer:
        @staticmethod
        def create(**kw):
            return {"id": "cus_PROP_123"}

    class FakeSession:
        @staticmethod
        def create(**kw):
            captured_metadata.update(kw["metadata"])
            return {"url": "https://stripe/checkout/cs_propagation"}

    class FakeStripe:
        api_key = None
        Customer = FakeCustomer
        class checkout:
            Session = FakeSession

    monkeypatch.setattr(stripe_client, "_client", lambda: FakeStripe)
    monkeypatch.setattr(stripe_client, "stripe", FakeStripe)

    # ── Step 1: create checkout session, capture metadata ──
    async with SessionLocal() as db:
        url = await create_checkout_session(
            db, acct, "pro", "m@test.local",
            success_url="https://x/ok", cancel_url="https://x/cancel",
        )
    assert url == "https://stripe/checkout/cs_propagation"
    assert captured_metadata.get("account_id") == str(acct.id), \
        "checkout session must embed account_id in metadata"
    assert captured_metadata.get("plan") == "pro", \
        "checkout session must embed selected plan in metadata"

    # ── Step 2: emit checkout.session.completed with that same metadata ──
    fake_event = {
        "id": f"evt_{TAG}_checkout",
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": "cs_propagation",
            "customer": "cus_PROP_123",
            "metadata": captured_metadata,  # round-trip the SAME metadata back
        }},
    }

    class FakeStripeWebhook:
        api_key = None
        class Webhook:
            @staticmethod
            def construct_event(payload, sig, secret): return fake_event

    monkeypatch.setattr(stripe_client, "_client", lambda: FakeStripeWebhook)
    monkeypatch.setattr(stripe_client, "stripe", FakeStripeWebhook)

    async with SessionLocal() as db:
        result = await handle_webhook_event(db, b"{}", "fake_sig")
    assert result["ok"] is True
    assert result["type"] == "checkout.session.completed"

    # ── Step 3: account.plan now reflects metadata.plan ──
    async with SessionLocal() as db:
        row = (await db.execute(select(Account).where(Account.id == acct.id))).scalar_one()
        assert row.plan == "pro", f"plan didn't update from metadata propagation: got {row.plan}"


@pytest.mark.asyncio
async def test_checkout_completed_with_missing_metadata_is_noop(monkeypatch, acct):
    """When metadata is absent (rare but possible), webhook returns OK without crashing."""
    from app.services.billing import stripe_client

    fake_event = {
        "id": f"evt_{TAG}_checkout",
        "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_x", "customer": "cus_x"}},  # no metadata key at all
    }

    class FakeStripe:
        api_key = None
        class Webhook:
            @staticmethod
            def construct_event(payload, sig, secret): return fake_event

    monkeypatch.setattr(stripe_client, "_client", lambda: FakeStripe)
    monkeypatch.setattr(stripe_client, "stripe", FakeStripe)

    async with SessionLocal() as db:
        result = await handle_webhook_event(db, b"{}", "fake_sig")
    assert result["ok"] is True

    # Plan unchanged
    async with SessionLocal() as db:
        row = (await db.execute(select(Account).where(Account.id == acct.id))).scalar_one()
        assert row.plan == "free"


@pytest.mark.asyncio
async def test_checkout_completed_with_partial_metadata_is_noop(monkeypatch, acct):
    """plan present but account_id missing — webhook should NOT pick a random account."""
    from app.services.billing import stripe_client

    fake_event = {
        "id": f"evt_{TAG}_checkout",
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": "cs_x", "customer": "cus_x",
            "metadata": {"plan": "agency"},  # no account_id
        }},
    }

    class FakeStripe:
        api_key = None
        class Webhook:
            @staticmethod
            def construct_event(payload, sig, secret): return fake_event

    monkeypatch.setattr(stripe_client, "_client", lambda: FakeStripe)
    monkeypatch.setattr(stripe_client, "stripe", FakeStripe)

    async with SessionLocal() as db:
        result = await handle_webhook_event(db, b"{}", "fake_sig")
    assert result["ok"] is True

    async with SessionLocal() as db:
        row = (await db.execute(select(Account).where(Account.id == acct.id))).scalar_one()
        assert row.plan == "free", "partial metadata must not silently upgrade plans"


@pytest.mark.asyncio
async def test_webhook_tolerates_malformed_account_id_metadata(monkeypatch, acct):
    """Production bug coverage: malformed UUID in metadata previously raised
    ValueError from UUID() → uncaught → 500. Now it logs + returns OK."""
    from app.services.billing import stripe_client

    fake_event = {
        "id": f"evt_{TAG}_checkout",
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": "cs_x", "customer": "cus_x",
            "metadata": {"account_id": "not-a-uuid", "plan": "agency"},
        }},
    }

    class FakeStripe:
        api_key = None
        class Webhook:
            @staticmethod
            def construct_event(payload, sig, secret): return fake_event

    monkeypatch.setattr(stripe_client, "_client", lambda: FakeStripe)
    monkeypatch.setattr(stripe_client, "stripe", FakeStripe)

    async with SessionLocal() as db:
        result = await handle_webhook_event(db, b"{}", "fake_sig")
    assert result["ok"] is True
    assert result["type"] == "checkout.session.completed"

    async with SessionLocal() as db:
        row = (await db.execute(select(Account).where(Account.id == acct.id))).scalar_one()
        assert row.plan == "free", "malformed account_id must not change any plan"


@pytest.mark.asyncio
async def test_webhook_ignores_unknown_account_id_in_metadata(monkeypatch, acct):
    """A well-formed UUID that doesn't match any account → no-op, not error."""
    from app.services.billing import stripe_client

    bogus = str(uuid.uuid4())
    fake_event = {
        "id": f"evt_{TAG}_checkout",
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": "cs_x", "customer": "cus_x",
            "metadata": {"account_id": bogus, "plan": "agency"},
        }},
    }

    class FakeStripe:
        api_key = None
        class Webhook:
            @staticmethod
            def construct_event(payload, sig, secret): return fake_event

    monkeypatch.setattr(stripe_client, "_client", lambda: FakeStripe)
    monkeypatch.setattr(stripe_client, "stripe", FakeStripe)

    async with SessionLocal() as db:
        result = await handle_webhook_event(db, b"{}", "fake_sig")
    assert result["ok"] is True

    async with SessionLocal() as db:
        row = (await db.execute(select(Account).where(Account.id == acct.id))).scalar_one()
        assert row.plan == "free", "stranger account_id must not change THIS account's plan"
