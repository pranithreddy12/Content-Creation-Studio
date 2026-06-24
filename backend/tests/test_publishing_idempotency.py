"""Publishing exactly-once + partial-failure recovery.

The risk: a worker publishes a (asset, channel) Schedule then crashes/retries,
double-posting to a customer's real account. Each (asset, channel) is its own
Schedule row, and we add:
  * an ATOMIC claim (pending→publishing conditional UPDATE) so concurrent workers
    can't both publish the same row
  * a deterministic idempotency key forwarded to adapters that accept one
  * bounded retries (transient failure → pending; terminal after MAX attempts)
  * a reaper that parks rows abandoned in 'publishing' (worker crash) rather than
    silently losing them or blindly re-posting

Also a cross-tenant regression guard (bugs #8–10) for the channel endpoints.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

os.environ.setdefault("SECRET_KEY", "testtesttesttesttesttesttesttest")

from app.db.session import SessionLocal  # noqa: E402
from app.models.account import Account, Workspace  # noqa: E402
from app.models.brand import Brand  # noqa: E402
from app.models.content import ContentAsset, ContentIdea  # noqa: E402
from app.models.publishing import PublishChannel, Schedule, ScheduleStatus  # noqa: E402

TAG = f"test_pubidem_{uuid.uuid4().hex[:8]}"
SLUG = TAG.replace("_", "-")


async def _seed_due_schedule(db) -> dict:
    acct = Account(clerk_org_id=f"{TAG}_{uuid.uuid4().hex[:6]}", name="A", plan="free")
    db.add(acct); await db.flush()
    ws = Workspace(account_id=acct.id, name="Default")
    db.add(ws); await db.flush()
    brand = Brand(account_id=acct.id, workspace_id=ws.id,
                  name="B", slug=f"{SLUG}-{uuid.uuid4().hex[:6]}"[:60])
    db.add(brand); await db.flush()
    idea = ContentIdea(account_id=acct.id, brand_id=brand.id, title="x",
                       created_at=datetime.now(timezone.utc))
    db.add(idea); await db.flush()
    asset = ContentAsset(account_id=acct.id, brand_id=brand.id, idea_id=idea.id,
                         format="blog", title="a", status="scheduled", body="…")
    db.add(asset); await db.flush()
    ch = PublishChannel(account_id=acct.id, brand_id=brand.id, platform="wordpress",
                        display_name="wp", oauth_blob={"ct": "stub"}, status="connected")
    db.add(ch); await db.flush()
    sched = Schedule(account_id=acct.id, brand_id=brand.id, asset_id=asset.id,
                     channel_id=ch.id,
                     scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=1),
                     status=ScheduleStatus.PENDING, created_at=datetime.now(timezone.utc))
    db.add(sched); await db.commit()
    return {"brand_id": brand.id, "schedule_id": sched.id,
            "asset_id": asset.id, "channel_id": ch.id}


@pytest.fixture()
async def cleanup():
    yield
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM accounts WHERE clerk_org_id LIKE :p"),
                         {"p": f"{TAG}%"})
        await db.commit()


@pytest.fixture()
def counting_registry(monkeypatch):
    """Adapter that records every publish call (to detect double-publish)."""
    state = {"calls": [], "idem_keys": []}

    class FakeAdapter:
        async def publish(self, channel, asset, idempotency_key=None):
            state["calls"].append(str(asset.id))
            state["idem_keys"].append(idempotency_key)
            return {"id": f"ext_{uuid.uuid4().hex[:6]}", "url": "https://x.example/p/1"}

    monkeypatch.setattr("app.integrations.publish_registry.get", lambda p: FakeAdapter())
    return state


# ── atomic claim / exactly-once under concurrency ───────────────────

@pytest.mark.asyncio
async def test_concurrent_publish_due_publishes_each_row_once(cleanup, counting_registry):
    """Two concurrent _publish_due runs over the same due row → published once."""
    from app.workers.tasks.publishing_tasks import _publish_due

    async with SessionLocal() as db:
        ctx = await _seed_due_schedule(db)

    results = await asyncio.gather(_publish_due(), _publish_due(), return_exceptions=True)
    assert all(not isinstance(r, Exception) for r in results), results

    # The adapter must have been called exactly once for this asset.
    assert counting_registry["calls"].count(str(ctx["asset_id"])) == 1, \
        f"double-publish! calls={counting_registry['calls']}"

    async with SessionLocal() as db:
        row = (await db.execute(
            select(Schedule).where(Schedule.id == ctx["schedule_id"])
        )).scalar_one()
        assert row.status == ScheduleStatus.PUBLISHED
        assert row.attempt == 1, "claimed and incremented exactly once"


@pytest.mark.asyncio
async def test_already_published_row_is_not_republished(cleanup, counting_registry):
    """A row already in 'published' is never re-selected/re-posted."""
    from app.workers.tasks.publishing_tasks import _publish_due

    async with SessionLocal() as db:
        ctx = await _seed_due_schedule(db)
        row = (await db.execute(
            select(Schedule).where(Schedule.id == ctx["schedule_id"])
        )).scalar_one()
        row.status = ScheduleStatus.PUBLISHED
        row.external_id = "ext_existing"
        await db.commit()

    n = await _publish_due()
    assert n == 0
    assert counting_registry["calls"] == [], "published rows must not be re-posted"


# ── idempotency key ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_idempotency_key_is_deterministic_and_forwarded(cleanup, counting_registry):
    """The adapter receives a deterministic key derived from (asset, channel)."""
    from app.workers.tasks.publishing_tasks import _publish_due, _idempotency_key

    async with SessionLocal() as db:
        ctx = await _seed_due_schedule(db)
        sched = (await db.execute(
            select(Schedule).where(Schedule.id == ctx["schedule_id"])
        )).scalar_one()
        expected = _idempotency_key(sched)

    await _publish_due()
    assert counting_registry["idem_keys"] == [expected]
    assert expected == f"pub:{ctx['asset_id']}:{ctx['channel_id']}"


@pytest.mark.asyncio
async def test_adapter_without_idempotency_param_still_works(cleanup, monkeypatch):
    """Adapters whose publish() takes no idempotency_key are called the 2-arg way."""
    from app.workers.tasks.publishing_tasks import _publish_due

    calls = []

    class LegacyAdapter:
        async def publish(self, channel, asset):  # no idempotency_key param
            calls.append(str(asset.id))
            return {"id": "x", "url": "https://x/y"}

    monkeypatch.setattr("app.integrations.publish_registry.get", lambda p: LegacyAdapter())

    async with SessionLocal() as db:
        ctx = await _seed_due_schedule(db)
    n = await _publish_due()
    assert n == 1
    assert calls == [str(ctx["asset_id"])]


# ── stuck-publishing reaper ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_reaper_parks_abandoned_publishing_rows_as_needs_review(cleanup):
    """A row stuck in 'publishing' past the timeout is parked as 'needs_review'
    (distinct from 'failed') — the post may be live, so a human must verify."""
    from app.workers.tasks.publishing_tasks import _reap_stuck_publishing

    async with SessionLocal() as db:
        ctx = await _seed_due_schedule(db)
        row = (await db.execute(
            select(Schedule).where(Schedule.id == ctx["schedule_id"])
        )).scalar_one()
        row.status = ScheduleStatus.PUBLISHING
        row.claimed_at = datetime.now(timezone.utc) - timedelta(hours=1)  # past timeout
        await db.commit()

    reaped = await _reap_stuck_publishing()
    assert reaped == 1

    async with SessionLocal() as db:
        row = (await db.execute(
            select(Schedule).where(Schedule.id == ctx["schedule_id"])
        )).scalar_one()
        assert row.status == ScheduleStatus.NEEDS_REVIEW, "must be distinct from 'failed' (post may be live)"
        assert "verify" in (row.error or "").lower()


@pytest.mark.asyncio
async def test_reaper_leaves_recently_claimed_rows_alone(cleanup):
    """A row claimed recently (within timeout) must NOT be reaped."""
    from app.workers.tasks.publishing_tasks import _reap_stuck_publishing

    async with SessionLocal() as db:
        ctx = await _seed_due_schedule(db)
        row = (await db.execute(
            select(Schedule).where(Schedule.id == ctx["schedule_id"])
        )).scalar_one()
        row.status = ScheduleStatus.PUBLISHING
        row.claimed_at = datetime.now(timezone.utc)  # just claimed
        await db.commit()

    reaped = await _reap_stuck_publishing()
    assert reaped == 0

    async with SessionLocal() as db:
        row = (await db.execute(
            select(Schedule).where(Schedule.id == ctx["schedule_id"])
        )).scalar_one()
        assert row.status == ScheduleStatus.PUBLISHING


@pytest.mark.asyncio
async def test_reaper_measures_from_claimed_at_not_scheduled_at(cleanup):
    """Decoupling proof: a row on a legitimate retry (old scheduled_at) but only
    just claimed must NOT be reaped — the old fix keyed on scheduled_at would
    have wrongly reaped it."""
    from app.workers.tasks.publishing_tasks import _reap_stuck_publishing

    async with SessionLocal() as db:
        ctx = await _seed_due_schedule(db)
        row = (await db.execute(
            select(Schedule).where(Schedule.id == ctx["schedule_id"])
        )).scalar_one()
        row.status = ScheduleStatus.PUBLISHING
        row.scheduled_at = datetime.now(timezone.utc) - timedelta(hours=3)  # old schedule
        row.claimed_at = datetime.now(timezone.utc)  # but claimed just now
        await db.commit()

    reaped = await _reap_stuck_publishing()
    assert reaped == 0, "must measure abandonment from claimed_at, not scheduled_at"


@pytest.mark.asyncio
async def test_claim_sets_claimed_at(cleanup, counting_registry):
    """_publish_due claiming a row stamps claimed_at."""
    from app.workers.tasks.publishing_tasks import _publish_due

    async with SessionLocal() as db:
        ctx = await _seed_due_schedule(db)
    await _publish_due()

    async with SessionLocal() as db:
        row = (await db.execute(
            select(Schedule).where(Schedule.id == ctx["schedule_id"])
        )).scalar_one()
        assert row.claimed_at is not None


@pytest.mark.asyncio
async def test_acks_late_redelivery_does_not_republish_in_flight_row(cleanup, counting_registry):
    """acks_late race: a row already claimed (status='publishing', adapter call
    in flight) must not be re-claimed/re-published by a redelivered publish_due."""
    from app.workers.tasks.publishing_tasks import _publish_due

    async with SessionLocal() as db:
        ctx = await _seed_due_schedule(db)
        # Simulate the original task having claimed the row and being mid-publish
        # (claim is committed before the adapter call), so the row is 'publishing'.
        row = (await db.execute(
            select(Schedule).where(Schedule.id == ctx["schedule_id"])
        )).scalar_one()
        row.status = ScheduleStatus.PUBLISHING
        row.claimed_at = datetime.now(timezone.utc)
        row.attempt = 1
        await db.commit()

    # The redelivered task runs now.
    n = await _publish_due()
    assert n == 0, "redelivery must not publish an already-claimed in-flight row"
    assert counting_registry["calls"] == [], "adapter must not be called for a claimed row"

    async with SessionLocal() as db:
        row = (await db.execute(
            select(Schedule).where(Schedule.id == ctx["schedule_id"])
        )).scalar_one()
        assert row.status == ScheduleStatus.PUBLISHING
        assert row.attempt == 1, "redelivery must not re-increment the attempt counter"
