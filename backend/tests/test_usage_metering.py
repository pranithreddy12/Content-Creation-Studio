"""Usage-metering + plan-limit enforcement tests."""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import text

os.environ.setdefault("SECRET_KEY", "testtesttesttesttesttesttesttest")

from app.api.deps.auth import CurrentUser  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.billing import PlanLimit  # noqa: E402
from app.services.billing import current_usage, enforce, meter  # noqa: E402
from app.services.provisioning import get_or_create_account  # noqa: E402

TAG = f"test_usage_{uuid.uuid4().hex[:8]}"


@pytest.fixture()
async def acct():
    """Provision an isolated account for each test."""
    user = CurrentUser(
        clerk_user_id=f"{TAG}_u",
        clerk_org_id=f"{TAG}_o_{uuid.uuid4().hex[:6]}",
        email="u@test.local",
        role="owner",
        raw={},
    )
    async with SessionLocal() as db:
        a = await get_or_create_account(db, user)
        yield a
        await db.execute(text("DELETE FROM accounts WHERE id = :a"), {"a": a.id})
        await db.commit()


@pytest.fixture()
async def seeded_free_plan():
    """Insert a tight-cap row in plan_limits for the duration of the test."""
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM plan_limits WHERE plan = 'free'"))
        pl = PlanLimit(
            plan="free",
            max_brands=3,
            max_workspaces=2,
            monthly_assets=10,
            monthly_video_minutes=5,
            monthly_publish_ops=20,
            monthly_llm_usd=1.0,
        )
        db.add(pl)
        await db.commit()
    yield
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM plan_limits WHERE plan = 'free'"))
        await db.commit()


@pytest.mark.asyncio
async def test_meter_records_usage_events(acct):
    async with SessionLocal() as db:
        await meter(db, account_id=acct.id, brand_id=None, kind="asset_generated", amount=1)
        await meter(db, account_id=acct.id, brand_id=None, kind="asset_generated", amount=2)
        await meter(db, account_id=acct.id, brand_id=None, kind="llm_usd", amount=0.25)

        month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        usage = await current_usage(db, acct.id, since=month_start)
    assert usage["asset_generated"] == 3
    assert usage["llm_usd"] == pytest.approx(0.25)


@pytest.mark.asyncio
async def test_enforce_passes_under_cap(acct, seeded_free_plan):
    async with SessionLocal() as db:
        await meter(db, account_id=acct.id, brand_id=None, kind="asset_generated", amount=8)
        # 8 + 1 = 9, under cap of 10
        await enforce(db, acct.id, "asset_generated", 1)


@pytest.mark.asyncio
async def test_enforce_raises_402_at_cap(acct, seeded_free_plan):
    async with SessionLocal() as db:
        await meter(db, account_id=acct.id, brand_id=None, kind="asset_generated", amount=10)
        with pytest.raises(HTTPException) as exc:
            await enforce(db, acct.id, "asset_generated", 1)
    assert exc.value.status_code == 402
    assert "asset_generated" in exc.value.detail
    assert "free" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_enforce_raises_402_on_video_minute_cap(acct, seeded_free_plan):
    async with SessionLocal() as db:
        await meter(db, account_id=acct.id, brand_id=None, kind="video_minute", amount=4.5)
        # 4.5 + 1 = 5.5, exceeds cap of 5
        with pytest.raises(HTTPException) as exc:
            await enforce(db, acct.id, "video_minute", 1)
    assert exc.value.status_code == 402


@pytest.mark.asyncio
async def test_enforce_raises_402_on_llm_usd_cap(acct, seeded_free_plan):
    async with SessionLocal() as db:
        await meter(db, account_id=acct.id, brand_id=None, kind="llm_usd", amount=0.95)
        with pytest.raises(HTTPException) as exc:
            await enforce(db, acct.id, "llm_usd", 0.10)
    assert exc.value.status_code == 402


@pytest.mark.asyncio
async def test_enforce_noops_when_no_plan_row(acct):
    """If the account's plan has no `plan_limits` row, enforce silently allows."""
    async with SessionLocal() as db:
        # Make sure no `free` plan row exists
        await db.execute(text("DELETE FROM plan_limits WHERE plan = 'free'"))
        await db.commit()
        await meter(db, account_id=acct.id, brand_id=None, kind="asset_generated", amount=10_000)
        # Should not raise.
        await enforce(db, acct.id, "asset_generated", 10_000)


@pytest.mark.asyncio
async def test_enforce_noops_on_unbounded_kind(acct):
    """A plan with NULL cap on a kind means unlimited — enforce must allow."""
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM plan_limits WHERE plan = 'free'"))
        db.add(PlanLimit(plan="free",
                         max_brands=3, max_workspaces=2,
                         monthly_assets=None, monthly_video_minutes=None,
                         monthly_publish_ops=None, monthly_llm_usd=None))
        await db.commit()
        try:
            await meter(db, account_id=acct.id, brand_id=None, kind="asset_generated", amount=99_999)
            await enforce(db, acct.id, "asset_generated", 1)
        finally:
            await db.execute(text("DELETE FROM plan_limits WHERE plan = 'free'"))
            await db.commit()


@pytest.mark.asyncio
async def test_current_usage_excludes_old_events(acct):
    """Events occurred_at before `since` must not count toward the current window."""
    async with SessionLocal() as db:
        # Insert one event with a backdated occurred_at to simulate a prior month
        from app.models.billing import UsageEvent
        old_ts = datetime.now(timezone.utc) - timedelta(days=90)
        db.add(UsageEvent(
            account_id=acct.id, kind="asset_generated", amount=1000,
            meta={}, occurred_at=old_ts,
        ))
        await db.commit()

        # And one event "now"
        await meter(db, account_id=acct.id, brand_id=None, kind="asset_generated", amount=3)

        month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        usage = await current_usage(db, acct.id, since=month_start)
    assert usage["asset_generated"] == 3, f"expected only the recent event, got {usage}"
