"""Billing dry-run: mock stripe, exercise checkout/portal endpoints and webhook handler."""
from __future__ import annotations

import json
import os
import uuid

import pytest

os.environ.setdefault("SECRET_KEY", "testtesttesttesttesttesttesttest")
os.environ.setdefault("STRIPE_SECRET_KEY", "sk_test_unit_dummy")
os.environ.setdefault("STRIPE_PRICE_PRO", "price_pro_dummy")
os.environ.setdefault("STRIPE_PRICE_AGENCY", "price_agency_dummy")
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "whsec_dummy")

from sqlalchemy import text  # noqa: E402

from app.api.deps.auth import CurrentUser  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.services.billing import (  # noqa: E402
    create_checkout_session,
    create_portal_session,
    handle_webhook_event,
)
from app.services.provisioning import get_or_create_account  # noqa: E402

TAG = f"test_billing_{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
def patch_stripe_settings(monkeypatch):
    """The settings module is loaded at container start with empty Stripe env vars.
    Monkeypatch the live settings instance so each billing test sees test-mode values."""
    from app.core import config as cfg
    monkeypatch.setattr(cfg.settings, "stripe_secret_key", "sk_test_unit_dummy")
    monkeypatch.setattr(cfg.settings, "stripe_price_pro", "price_pro_dummy")
    monkeypatch.setattr(cfg.settings, "stripe_price_agency", "price_agency_dummy")
    monkeypatch.setattr(cfg.settings, "stripe_webhook_secret", "whsec_dummy")
    yield


@pytest.fixture()
async def acct():
    user = CurrentUser(clerk_user_id=f"{TAG}_u", clerk_org_id=f"{TAG}_o",
                       email="b@test.local", role="owner", raw={})
    async with SessionLocal() as db:
        a = await get_or_create_account(db, user)
    yield a
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM processed_stripe_events WHERE event_id LIKE :p"),
                         {"p": f"evt_{TAG}%"})
        await db.execute(text("DELETE FROM accounts WHERE clerk_org_id = :o"),
                         {"o": f"{TAG}_o"})
        await db.commit()


@pytest.mark.asyncio
async def test_checkout_session_creates_customer_then_session(monkeypatch, acct):
    from app.services.billing import stripe_client

    calls: dict[str, list] = {"customer": [], "session": []}

    class FakeCustomer:
        @staticmethod
        def create(**kw):
            calls["customer"].append(kw)
            return {"id": "cus_NEW_123"}

    class FakeSession:
        @staticmethod
        def create(**kw):
            calls["session"].append(kw)
            return {"url": "https://stripe.com/pay/cs_test_123"}

    class FakeCheckout:
        Session = FakeSession

    class FakeStripe:
        api_key = None
        Customer = FakeCustomer
        checkout = FakeCheckout

    monkeypatch.setattr(stripe_client, "_client", lambda: FakeStripe)
    monkeypatch.setattr(stripe_client, "stripe", FakeStripe)

    async with SessionLocal() as db:
        url = await create_checkout_session(
            db, acct, "pro", "b@test.local",
            success_url="https://x/ok", cancel_url="https://x/cancel",
        )

    assert url == "https://stripe.com/pay/cs_test_123"
    assert len(calls["customer"]) == 1, "should create a Stripe customer on first call"
    assert calls["customer"][0]["email"] == "b@test.local"
    assert calls["customer"][0]["metadata"]["account_id"] == str(acct.id)
    sess = calls["session"][0]
    assert sess["mode"] == "subscription"
    assert sess["customer"] == "cus_NEW_123"
    assert sess["line_items"][0]["price"] == "price_pro_dummy"
    assert sess["metadata"]["plan"] == "pro"


@pytest.mark.asyncio
async def test_checkout_reuses_existing_stripe_customer(monkeypatch, acct):
    from app.services.billing import stripe_client

    # Prefill the account's stripe_customer so create-customer must NOT be called.
    async with SessionLocal() as db:
        from sqlalchemy import select
        from app.models.account import Account
        row = (await db.execute(select(Account).where(Account.id == acct.id))).scalar_one()
        row.stripe_customer = "cus_EXISTING_456"
        await db.commit()

    customer_create_called: list[dict] = []
    session_calls: list[dict] = []

    class FakeCustomer:
        @staticmethod
        def create(**kw):
            customer_create_called.append(kw)
            return {"id": "cus_SHOULD_NOT_HAPPEN"}

    class FakeSession:
        @staticmethod
        def create(**kw):
            session_calls.append(kw)
            return {"url": "https://stripe.com/pay/cs_test_777"}

    class FakeStripe:
        api_key = None
        Customer = FakeCustomer
        class checkout:
            Session = FakeSession

    monkeypatch.setattr(stripe_client, "_client", lambda: FakeStripe)
    monkeypatch.setattr(stripe_client, "stripe", FakeStripe)

    async with SessionLocal() as db:
        from sqlalchemy import select
        from app.models.account import Account
        fresh = (await db.execute(select(Account).where(Account.id == acct.id))).scalar_one()
        url = await create_checkout_session(
            db, fresh, "agency", "b@test.local",
            success_url="https://x", cancel_url="https://x",
        )

    assert url == "https://stripe.com/pay/cs_test_777"
    assert customer_create_called == [], "must not create new Stripe customer when one exists"
    assert session_calls[0]["customer"] == "cus_EXISTING_456"
    assert session_calls[0]["line_items"][0]["price"] == "price_agency_dummy"


@pytest.mark.asyncio
async def test_unknown_plan_raises_value_error(monkeypatch, acct):
    """checkout_session must reject plans that aren't in PLAN_TO_PRICE."""
    from app.services.billing import stripe_client

    class FakeStripe:
        api_key = None
        class Customer:
            @staticmethod
            def create(**kw): return {"id": "cus_x"}

    monkeypatch.setattr(stripe_client, "_client", lambda: FakeStripe)
    monkeypatch.setattr(stripe_client, "stripe", FakeStripe)

    async with SessionLocal() as db:
        with pytest.raises(ValueError, match="unknown plan"):
            await create_checkout_session(db, acct, "bogus_tier", "x@y", "u", "u")


@pytest.mark.asyncio
async def test_webhook_subscription_updates_account_plan(monkeypatch, acct):
    """A `customer.subscription.created` event must flip account.plan."""
    from app.services.billing import stripe_client

    # Seed the account's stripe_customer so the webhook can find it.
    async with SessionLocal() as db:
        from sqlalchemy import select
        from app.models.account import Account
        row = (await db.execute(select(Account).where(Account.id == acct.id))).scalar_one()
        row.stripe_customer = "cus_HOOK_999"
        await db.commit()

    fake_event = {
        "id": f"evt_{TAG}_subcreate",
        "type": "customer.subscription.created",
        "data": {"object": {
            "customer": "cus_HOOK_999",
            "items": {"data": [{"price": {"id": "price_agency_dummy"}}]},
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
    assert result["type"] == "customer.subscription.created"

    async with SessionLocal() as db:
        from sqlalchemy import select
        from app.models.account import Account
        row = (await db.execute(select(Account).where(Account.id == acct.id))).scalar_one()
        assert row.plan == "agency"


@pytest.mark.asyncio
async def test_webhook_subscription_deleted_downgrades_to_free(monkeypatch, acct):
    from app.services.billing import stripe_client

    async with SessionLocal() as db:
        from sqlalchemy import select
        from app.models.account import Account
        row = (await db.execute(select(Account).where(Account.id == acct.id))).scalar_one()
        row.stripe_customer = "cus_GONE"
        row.plan = "pro"
        await db.commit()

    fake_event = {"id": f"evt_{TAG}_subdel",
                  "type": "customer.subscription.deleted",
                  "data": {"object": {"customer": "cus_GONE"}}}

    class FakeStripe:
        api_key = None
        class Webhook:
            @staticmethod
            def construct_event(payload, sig, secret): return fake_event

    monkeypatch.setattr(stripe_client, "_client", lambda: FakeStripe)
    monkeypatch.setattr(stripe_client, "stripe", FakeStripe)

    async with SessionLocal() as db:
        await handle_webhook_event(db, b"{}", "fake_sig")

    async with SessionLocal() as db:
        from sqlalchemy import select
        from app.models.account import Account
        row = (await db.execute(select(Account).where(Account.id == acct.id))).scalar_one()
        assert row.plan == "free"
