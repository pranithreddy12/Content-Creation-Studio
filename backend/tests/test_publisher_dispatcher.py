"""Publisher dispatcher tests.

Covers the per-idea fan-out (`dispatch_for_idea`) and the scheduled-publish
poll loop (`publish_due`). Adapter HTTP calls are mocked so we can exercise
status transitions without hitting LinkedIn/X/WordPress.
"""
from __future__ import annotations

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

TAG = f"test_disp_{uuid.uuid4().hex[:8]}"
SLUG = TAG.replace("_", "-")


async def _seed(db, *, formats: list[str], channel_platforms: list[str]) -> dict:
    """Provision account/brand + a content idea with one asset per format + channels per platform."""
    acct = Account(clerk_org_id=f"{TAG}_{uuid.uuid4().hex[:6]}", name="A", plan="free")
    db.add(acct); await db.flush()
    ws = Workspace(account_id=acct.id, name="Default")
    db.add(ws); await db.flush()
    brand = Brand(
        account_id=acct.id, workspace_id=ws.id,
        name="DispBrand", slug=f"{SLUG}-{uuid.uuid4().hex[:6]}"[:60],
    )
    db.add(brand); await db.flush()
    idea = ContentIdea(
        account_id=acct.id, brand_id=brand.id, title="x",
        created_at=datetime.now(timezone.utc),
    )
    db.add(idea); await db.flush()
    asset_ids = []
    for fmt in formats:
        a = ContentAsset(
            account_id=acct.id, brand_id=brand.id, idea_id=idea.id,
            format=fmt, title=f"{fmt} asset", status="draft", body="…",
        )
        db.add(a); await db.flush()
        asset_ids.append(a.id)
    channel_ids = {}
    for plat in channel_platforms:
        ch = PublishChannel(
            account_id=acct.id, brand_id=brand.id,
            platform=plat, display_name=f"{plat}@brand",
            oauth_blob={"ct": "stub"}, status="connected",
        )
        db.add(ch); await db.flush()
        channel_ids[plat] = ch.id
    await db.commit()
    return {
        "brand_id": brand.id, "idea_id": idea.id,
        "asset_ids": asset_ids, "channel_ids": channel_ids,
    }


@pytest.fixture()
async def cleanup():
    yield
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM accounts WHERE clerk_org_id LIKE :p"),
                         {"p": f"{TAG}%"})
        await db.commit()


# ─── dispatch_for_idea ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_creates_one_schedule_per_matched_format(cleanup):
    from app.workers.tasks.publishing_tasks import _dispatch
    async with SessionLocal() as db:
        ctx = await _seed(
            db,
            formats=["blog", "linkedin", "x_thread"],
            channel_platforms=["wordpress", "linkedin", "x"],
        )
    n = await _dispatch(ctx["idea_id"])
    assert n == 3

    async with SessionLocal() as db:
        scheds = (await db.execute(
            select(Schedule).where(Schedule.brand_id == ctx["brand_id"])
            .order_by(Schedule.scheduled_at)
        )).scalars().all()
        assert len(scheds) == 3
        # Status pending, attempt=0, no external id yet
        assert all(s.status == ScheduleStatus.PENDING for s in scheds)
        assert all(s.attempt == 0 for s in scheds)
        assert all(s.external_id is None for s in scheds)
        # All three assets should flip to scheduled
        assets = (await db.execute(
            select(ContentAsset).where(ContentAsset.idea_id == ctx["idea_id"])
        )).scalars().all()
        assert all(a.status == "scheduled" for a in assets)


@pytest.mark.asyncio
async def test_dispatch_staggers_scheduled_at_by_30_min(cleanup):
    from app.workers.tasks.publishing_tasks import _dispatch
    async with SessionLocal() as db:
        ctx = await _seed(
            db,
            formats=["blog", "linkedin", "x_thread"],
            channel_platforms=["wordpress", "linkedin", "x"],
        )
    await _dispatch(ctx["idea_id"])

    async with SessionLocal() as db:
        scheds = (await db.execute(
            select(Schedule).where(Schedule.brand_id == ctx["brand_id"])
            .order_by(Schedule.scheduled_at)
        )).scalars().all()
    deltas = [(scheds[i].scheduled_at - scheds[i - 1].scheduled_at).total_seconds()
              for i in range(1, len(scheds))]
    # Each pair should be exactly 30 minutes apart (the FORMAT loop adds 30 min per asset).
    for d in deltas:
        assert d == pytest.approx(30 * 60, abs=5), f"unexpected stagger: {d}s"


@pytest.mark.asyncio
async def test_dispatch_skips_assets_without_matching_channel(cleanup):
    """Idea has 2 assets but only 1 channel that matches — only 1 Schedule row."""
    from app.workers.tasks.publishing_tasks import _dispatch
    async with SessionLocal() as db:
        ctx = await _seed(
            db,
            formats=["blog", "tiktok"],
            channel_platforms=["wordpress"],  # no tiktok channel
        )
    n = await _dispatch(ctx["idea_id"])
    assert n == 1

    async with SessionLocal() as db:
        scheds = (await db.execute(
            select(Schedule).where(Schedule.brand_id == ctx["brand_id"])
        )).scalars().all()
        assert len(scheds) == 1
        # The blog asset got scheduled
        blog_asset = (await db.execute(
            select(ContentAsset).where(
                ContentAsset.idea_id == ctx["idea_id"], ContentAsset.format == "blog"
            )
        )).scalar_one()
        assert blog_asset.status == "scheduled"
        # The tiktok asset stayed in draft
        tt_asset = (await db.execute(
            select(ContentAsset).where(
                ContentAsset.idea_id == ctx["idea_id"], ContentAsset.format == "tiktok"
            )
        )).scalar_one()
        assert tt_asset.status == "draft"


@pytest.mark.asyncio
async def test_dispatch_ignores_already_scheduled_assets(cleanup):
    """If an asset is already scheduled (not draft), dispatch should NOT double-schedule it."""
    from app.workers.tasks.publishing_tasks import _dispatch
    async with SessionLocal() as db:
        ctx = await _seed(
            db,
            formats=["blog", "linkedin"],
            channel_platforms=["wordpress", "linkedin"],
        )
        # Manually flip one asset to 'scheduled'
        asset = (await db.execute(
            select(ContentAsset).where(ContentAsset.id == ctx["asset_ids"][0])
        )).scalar_one()
        asset.status = "scheduled"
        await db.commit()

    n = await _dispatch(ctx["idea_id"])
    # Only the still-draft asset got dispatched
    assert n == 1


# ─── publish_due ─────────────────────────────────────────────────────

@pytest.fixture()
def stub_publish_registry(monkeypatch):
    """Stub publish_registry.get(platform) so no real HTTP fires."""
    state = {"calls": [], "raise_for": set()}

    class FakeAdapter:
        async def publish(self, channel, asset):
            state["calls"].append({
                "platform": channel.platform,
                "asset_id": str(asset.id),
            })
            if channel.platform in state["raise_for"]:
                raise RuntimeError(f"{channel.platform} blew up")
            return {"id": f"ext_{channel.platform}_{uuid.uuid4().hex[:6]}",
                    "url": f"https://{channel.platform}.example/post/123"}

    fake = FakeAdapter()

    from app.workers.tasks import publishing_tasks as pt
    # Late import: registry is fetched inside _publish_due via integrations module
    monkeypatch.setattr("app.integrations.publish_registry.get", lambda p: fake)
    return state


@pytest.mark.asyncio
async def test_publish_due_picks_up_past_schedules(cleanup, stub_publish_registry):
    """A Schedule with scheduled_at in the past gets processed; future ones are left alone."""
    from app.workers.tasks.publishing_tasks import _publish_due, _dispatch

    async with SessionLocal() as db:
        ctx = await _seed(
            db,
            formats=["blog", "linkedin"],
            channel_platforms=["wordpress", "linkedin"],
        )
    await _dispatch(ctx["idea_id"])

    async with SessionLocal() as db:
        # Backdate ONE schedule, leave the other in the future
        scheds = (await db.execute(
            select(Schedule).where(Schedule.brand_id == ctx["brand_id"])
            .order_by(Schedule.scheduled_at)
        )).scalars().all()
        scheds[0].scheduled_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        # scheds[1] keeps its original (30 min in future)
        await db.commit()

    n = await _publish_due()
    assert n == 1

    async with SessionLocal() as db:
        rows = (await db.execute(
            select(Schedule).where(Schedule.brand_id == ctx["brand_id"])
            .order_by(Schedule.scheduled_at)
        )).scalars().all()
        assert rows[0].status == ScheduleStatus.PUBLISHED
        assert rows[0].external_id is not None
        assert rows[0].external_url is not None
        assert rows[0].published_at is not None
        assert rows[0].attempt == 1
        # The future schedule is untouched
        assert rows[1].status == ScheduleStatus.PENDING


@pytest.mark.asyncio
async def test_publish_due_retries_transient_failure_then_terminal(cleanup, stub_publish_registry):
    """A failing adapter is retried (status back to 'pending') up to
    MAX_PUBLISH_ATTEMPTS, then parked terminally as 'failed'."""
    from app.workers.tasks.publishing_tasks import _publish_due, _dispatch, MAX_PUBLISH_ATTEMPTS

    async with SessionLocal() as db:
        ctx = await _seed(db, formats=["blog"], channel_platforms=["wordpress"])
    await _dispatch(ctx["idea_id"])

    async with SessionLocal() as db:
        sched = (await db.execute(
            select(Schedule).where(Schedule.brand_id == ctx["brand_id"])
        )).scalar_one()
        sched.scheduled_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await db.commit()

    stub_publish_registry["raise_for"].add("wordpress")

    # First failure → back to 'pending' for retry, error captured, attempt counted.
    n = await _publish_due()
    assert n == 0, "failed publish should not count toward success tally"
    async with SessionLocal() as db:
        row = (await db.execute(
            select(Schedule).where(Schedule.brand_id == ctx["brand_id"])
        )).scalar_one()
        assert row.status == ScheduleStatus.PENDING, "transient failure must be retryable, not terminal"
        assert "wordpress blew up" in (row.error or "")
        assert row.attempt == 1

    # Keep failing until attempts are exhausted → terminal 'failed'.
    for _ in range(MAX_PUBLISH_ATTEMPTS):
        async with SessionLocal() as db:
            # keep it due
            r = (await db.execute(
                select(Schedule).where(Schedule.brand_id == ctx["brand_id"])
            )).scalar_one()
            if r.status != ScheduleStatus.PENDING:
                break
            r.scheduled_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            await db.commit()
        await _publish_due()

    async with SessionLocal() as db:
        row = (await db.execute(
            select(Schedule).where(Schedule.brand_id == ctx["brand_id"])
        )).scalar_one()
        assert row.status == ScheduleStatus.FAILED, "must go terminal after exhausting attempts"
        assert row.attempt >= MAX_PUBLISH_ATTEMPTS


@pytest.mark.asyncio
async def test_publish_due_skips_when_no_adapter(cleanup, monkeypatch):
    """If the registry has no adapter for the platform, schedule.status='failed'."""
    from app.workers.tasks.publishing_tasks import _publish_due, _dispatch

    async with SessionLocal() as db:
        ctx = await _seed(db, formats=["blog"], channel_platforms=["wordpress"])
    await _dispatch(ctx["idea_id"])
    async with SessionLocal() as db:
        sched = (await db.execute(
            select(Schedule).where(Schedule.brand_id == ctx["brand_id"])
        )).scalar_one()
        sched.scheduled_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await db.commit()

    # Registry returns None for any platform
    monkeypatch.setattr("app.integrations.publish_registry.get", lambda p: None)
    n = await _publish_due()
    assert n == 0

    async with SessionLocal() as db:
        row = (await db.execute(
            select(Schedule).where(Schedule.brand_id == ctx["brand_id"])
        )).scalar_one()
        # First attempt is retryable (bounded); the error is captured either way.
        assert row.status == ScheduleStatus.PENDING
        assert "no adapter" in (row.error or "")
        assert row.attempt == 1


@pytest.mark.asyncio
async def test_publish_due_processes_at_most_100_per_invocation(cleanup, stub_publish_registry):
    """Safety: even with many due schedules, one call processes at most 100."""
    from app.workers.tasks.publishing_tasks import _publish_due
    async with SessionLocal() as db:
        ctx = await _seed(db, formats=["blog"], channel_platforms=["wordpress"])
        # Manually insert 105 due-now schedules
        for _ in range(105):
            db.add(Schedule(
                account_id=(await db.execute(
                    select(Brand.account_id).where(Brand.id == ctx["brand_id"])
                )).scalar_one(),
                brand_id=ctx["brand_id"],
                asset_id=ctx["asset_ids"][0],
                channel_id=ctx["channel_ids"]["wordpress"],
                scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=5),
                status=ScheduleStatus.PENDING,
                created_at=datetime.now(timezone.utc),
            ))
        await db.commit()

    n = await _publish_due()
    assert n <= 100, "publish_due must batch — saw it process more than 100 at once"
    assert n == 100
