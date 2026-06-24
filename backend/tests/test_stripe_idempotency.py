"""Stripe webhook idempotency — dedupe on event.id so redelivery is safe.

Stripe guarantees at-least-once delivery and WILL redeliver events. Without
dedup, a redelivered `customer.subscription.updated` re-applies the plan change
every time. These tests pin:
  * the same event delivered twice applies the side effect exactly once
  * an unsigned / wrong-signature payload is rejected BEFORE any DB write
  * malformed metadata.account_id still doesn't 500 (regression guard for bug #7)
  * a crash between dedup-insert and commit leaves the event un-recorded (so the
    redelivery re-applies it) — i.e. no recorded-but-unapplied state
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select, text

os.environ.setdefault("SECRET_KEY", "testtesttesttesttesttesttesttest")
os.environ.setdefault("STRIPE_SECRET_KEY", "sk_test_unit_dummy")
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "whsec_dummy")

from app.api.deps.auth import CurrentUser  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.account import Account  # noqa: E402
from app.models.billing import ProcessedStripeEvent  # noqa: E402
from app.services.billing import handle_webhook_event  # noqa: E402
from app.services.provisioning import get_or_create_account  # noqa: E402

TAG = f"test_sidem_{uuid.uuid4().hex[:8]}"


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
                       email="s@test.local", role="owner", raw={})
    async with SessionLocal() as db:
        a = await get_or_create_account(db, user)
        a.stripe_customer = f"cus_{TAG}"
        await db.commit()
        acct_id = a.id
    yield acct_id
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM processed_stripe_events WHERE event_id LIKE :p"),
                         {"p": f"evt_{TAG}%"})
        await db.execute(text("DELETE FROM accounts WHERE clerk_org_id LIKE :p"),
                         {"p": f"{TAG}%"})
        await db.commit()


def _sub_event(event_id: str, price_id: str = "price_agency_dummy") -> dict:
    return {
        "id": event_id,
        "type": "customer.subscription.updated",
        "data": {"object": {
            "customer": f"cus_{TAG}",
            "items": {"data": [{"price": {"id": price_id}}]},
        }},
    }


def _patch_construct(monkeypatch, event: dict):
    from app.services.billing import stripe_client

    class FakeStripe:
        api_key = None
        class Webhook:
            @staticmethod
            def construct_event(payload, sig, secret):
                return event
    monkeypatch.setattr(stripe_client, "_client", lambda: FakeStripe)
    monkeypatch.setattr(stripe_client, "stripe", FakeStripe)


@pytest.mark.asyncio
async def test_duplicate_event_applies_side_effect_once(acct, monkeypatch):
    """Deliver the SAME event twice; the second is deduped and does not re-apply."""
    event_id = f"evt_{TAG}_dup"
    _patch_construct(monkeypatch, _sub_event(event_id, "price_agency_dummy"))

    async with SessionLocal() as db:
        r1 = await handle_webhook_event(db, b"{}", "sig")
    assert r1 == {"ok": True, "type": "customer.subscription.updated"} or r1.get("ok")
    assert "deduped" not in r1

    async with SessionLocal() as db:
        r2 = await handle_webhook_event(db, b"{}", "sig")
    assert r2.get("deduped") is True, "second delivery must be deduped"

    # Exactly one ledger row, and the plan is set once.
    async with SessionLocal() as db:
        n = (await db.execute(
            select(func.count()).select_from(ProcessedStripeEvent)
            .where(ProcessedStripeEvent.event_id == event_id)
        )).scalar()
        assert n == 1
        plan = (await db.execute(
            select(Account.plan).where(Account.id == acct)
        )).scalar()
        assert plan == "agency"


@pytest.mark.asyncio
async def test_duplicate_does_not_reapply_after_manual_downgrade(acct, monkeypatch):
    """Proves the second delivery is a true no-op: if we manually flip the plan
    between deliveries, the deduped redelivery must NOT flip it back."""
    event_id = f"evt_{TAG}_noreapply"
    _patch_construct(monkeypatch, _sub_event(event_id, "price_agency_dummy"))

    async with SessionLocal() as db:
        await handle_webhook_event(db, b"{}", "sig")

    # Simulate a later legitimate change (e.g. user downgraded via portal).
    async with SessionLocal() as db:
        acct_row = (await db.execute(select(Account).where(Account.id == acct))).scalar_one()
        acct_row.plan = "free"
        await db.commit()

    # Redelivery of the OLD event must be ignored, leaving plan at 'free'.
    async with SessionLocal() as db:
        r = await handle_webhook_event(db, b"{}", "sig")
    assert r.get("deduped") is True

    async with SessionLocal() as db:
        plan = (await db.execute(select(Account.plan).where(Account.id == acct))).scalar()
    assert plan == "free", "deduped redelivery must not resurrect the old plan"


@pytest.mark.asyncio
async def test_distinct_events_each_apply(acct, monkeypatch):
    """Two DIFFERENT event ids both process (dedup is per-id, not blanket)."""
    e1 = f"evt_{TAG}_a"
    e2 = f"evt_{TAG}_b"

    _patch_construct(monkeypatch, _sub_event(e1, "price_pro_dummy"))
    async with SessionLocal() as db:
        r1 = await handle_webhook_event(db, b"{}", "sig")
    assert "deduped" not in r1

    _patch_construct(monkeypatch, _sub_event(e2, "price_agency_dummy"))
    async with SessionLocal() as db:
        r2 = await handle_webhook_event(db, b"{}", "sig")
    assert "deduped" not in r2

    async with SessionLocal() as db:
        plan = (await db.execute(select(Account.plan).where(Account.id == acct))).scalar()
    assert plan == "agency"  # second event won


@pytest.mark.asyncio
async def test_bad_signature_rejected_before_any_db_write(acct, monkeypatch):
    """construct_event raising (bad sig) must happen before we touch the ledger."""
    from app.services.billing import stripe_client

    class FakeStripe:
        api_key = None
        class Webhook:
            @staticmethod
            def construct_event(payload, sig, secret):
                raise ValueError("Invalid signature")
    monkeypatch.setattr(stripe_client, "_client", lambda: FakeStripe)
    monkeypatch.setattr(stripe_client, "stripe", FakeStripe)

    with pytest.raises(ValueError):
        async with SessionLocal() as db:
            await handle_webhook_event(db, b"{}", "bad_sig")

    # No ledger row written for a rejected event.
    async with SessionLocal() as db:
        n = (await db.execute(
            select(func.count()).select_from(ProcessedStripeEvent)
            .where(ProcessedStripeEvent.event_id.like(f"evt_{TAG}%"))
        )).scalar()
    assert n == 0


@pytest.mark.asyncio
async def test_malformed_metadata_account_id_does_not_500(acct, monkeypatch):
    """Regression guard for bug #7 — malformed UUID in metadata is tolerated."""
    event = {
        "id": f"evt_{TAG}_malformed",
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": "cs_x", "customer": f"cus_{TAG}",
            "metadata": {"account_id": "not-a-uuid", "plan": "agency"},
        }},
    }
    _patch_construct(monkeypatch, event)
    async with SessionLocal() as db:
        r = await handle_webhook_event(db, b"{}", "sig")
    assert r["ok"] is True

    async with SessionLocal() as db:
        plan = (await db.execute(select(Account.plan).where(Account.id == acct))).scalar()
    assert plan == "free", "malformed account_id must not change any plan"


@pytest.mark.asyncio
async def test_crash_between_insert_and_commit_leaves_event_unrecorded(acct, monkeypatch):
    """If the side effect raises, the dedup row must roll back too (single tx),
    so the redelivery re-applies rather than being wrongly skipped."""
    event_id = f"evt_{TAG}_crash"
    _patch_construct(monkeypatch, _sub_event(event_id, "price_agency_dummy"))

    # Make the plan update blow up AFTER the dedup row is added but BEFORE commit.
    from app.services.billing import stripe_client

    async def boom(db, customer_id, plan):
        raise RuntimeError("simulated mid-processing crash")
    monkeypatch.setattr(stripe_client, "_update_account_plan", boom)

    with pytest.raises(RuntimeError):
        async with SessionLocal() as db:
            await handle_webhook_event(db, b"{}", "sig")

    # The dedup row must NOT persist (transaction rolled back / never committed).
    async with SessionLocal() as db:
        n = (await db.execute(
            select(func.count()).select_from(ProcessedStripeEvent)
            .where(ProcessedStripeEvent.event_id == event_id)
        )).scalar()
    assert n == 0, "crash must not leave a recorded-but-unapplied event"


@pytest.mark.asyncio
async def test_insert_first_skips_when_ledger_row_preexists(acct, monkeypatch):
    """Race simulation: the ledger row already exists (a concurrent worker won the
    insert). The handler must detect the duplicate via the INSERT's unique violation
    — not a prior SELECT — and skip the side effect."""
    event_id = f"evt_{TAG}_preexist"

    # Pre-insert the ledger row out-of-band, committed, to model the other worker.
    async with SessionLocal() as db:
        db.add(ProcessedStripeEvent(event_id=event_id, type="customer.subscription.updated",
                                    received_at=datetime.now(timezone.utc)))
        await db.commit()

    # The side effect must NOT run — assert by making it explode if reached.
    from app.services.billing import stripe_client

    async def must_not_run(db, customer_id, plan):
        raise AssertionError("side effect ran for an already-recorded event")
    monkeypatch.setattr(stripe_client, "_update_account_plan", must_not_run)

    _patch_construct(monkeypatch, _sub_event(event_id, "price_agency_dummy"))
    async with SessionLocal() as db:
        r = await handle_webhook_event(db, b"{}", "sig")
    assert r.get("deduped") is True


@pytest.mark.asyncio
async def test_concurrent_same_event_applies_once(acct, monkeypatch):
    """Two concurrent deliveries of the SAME event_id: the PK unique constraint is
    the arbiter — exactly one applies, the other is deduped."""
    event_id = f"evt_{TAG}_concurrent"
    _patch_construct(monkeypatch, _sub_event(event_id, "price_agency_dummy"))

    async def deliver():
        async with SessionLocal() as db:
            return await handle_webhook_event(db, b"{}", "sig")

    results = await asyncio.gather(deliver(), deliver(), return_exceptions=True)
    # Neither call may error out; both return a dict.
    assert all(isinstance(r, dict) for r in results), results
    deduped = [r for r in results if r.get("deduped")]
    applied = [r for r in results if not r.get("deduped")]
    assert len(applied) == 1, f"exactly one delivery should apply, got {results}"
    assert len(deduped) == 1, f"exactly one delivery should dedup, got {results}"

    # One ledger row; plan applied once.
    async with SessionLocal() as db:
        n = (await db.execute(
            select(func.count()).select_from(ProcessedStripeEvent)
            .where(ProcessedStripeEvent.event_id == event_id)
        )).scalar()
        plan = (await db.execute(select(Account.plan).where(Account.id == acct))).scalar()
    assert n == 1
    assert plan == "agency"


@pytest.mark.asyncio
async def test_verified_event_without_id_is_rejected(acct, monkeypatch):
    """A signature-verified event lacking an id is an anomaly — reject, don't
    process it unprotected by dedup."""
    event = {  # no "id"
        "type": "customer.subscription.updated",
        "data": {"object": {"customer": f"cus_{TAG}",
                            "items": {"data": [{"price": {"id": "price_agency_dummy"}}]}}},
    }
    _patch_construct(monkeypatch, event)
    with pytest.raises(ValueError, match="missing id"):
        async with SessionLocal() as db:
            await handle_webhook_event(db, b"{}", "sig")
