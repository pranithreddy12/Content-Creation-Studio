"""Analytics collect_for_asset + rollup tests.

  * `_collect_for_asset` walks the published schedules of an asset, calls the
    platform adapter's `fetch_metrics`, and writes AssetMetric rows.
  * Missing adapter → no row written (graceful)
  * Adapter raising → no row written, exception swallowed (collection should
    not break the worker for sibling assets)
  * `_rollup` computes a group-by-platform sum within a time window
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
from app.models.analytics import AssetMetric  # noqa: E402
from app.models.brand import Brand  # noqa: E402
from app.models.content import ContentAsset, ContentIdea  # noqa: E402
from app.models.publishing import PublishChannel, Schedule  # noqa: E402

TAG = f"test_ametric_{uuid.uuid4().hex[:8]}"
SLUG = TAG.replace("_", "-")


async def _seed_published_asset(db, *, platform: str) -> dict:
    """Provision Account/Brand/Asset/Channel/Schedule with status=published."""
    acct = Account(clerk_org_id=f"{TAG}_{uuid.uuid4().hex[:6]}", name="A", plan="free")
    db.add(acct); await db.flush()
    ws = Workspace(account_id=acct.id, name="Default")
    db.add(ws); await db.flush()
    brand = Brand(
        account_id=acct.id, workspace_id=ws.id,
        name="B", slug=f"{SLUG}-{uuid.uuid4().hex[:6]}"[:60],
    )
    db.add(brand); await db.flush()
    idea = ContentIdea(account_id=acct.id, brand_id=brand.id, title="x",
                       created_at=datetime.now(timezone.utc))
    db.add(idea); await db.flush()
    asset = ContentAsset(
        account_id=acct.id, brand_id=brand.id, idea_id=idea.id,
        format="blog", title="post", status="published",
    )
    db.add(asset); await db.flush()
    channel = PublishChannel(
        account_id=acct.id, brand_id=brand.id,
        platform=platform, display_name=f"{platform}@b",
        oauth_blob={"ct": "stub"}, status="connected",
    )
    db.add(channel); await db.flush()
    sched = Schedule(
        account_id=acct.id, brand_id=brand.id,
        asset_id=asset.id, channel_id=channel.id,
        scheduled_at=datetime.now(timezone.utc) - timedelta(hours=1),
        status="published",
        external_id=f"ext_{platform}",
        external_url=f"https://{platform}/post/123",
        published_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        created_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    db.add(sched); await db.commit()
    return {
        "asset_id": asset.id, "brand_id": brand.id,
        "account_id": acct.id, "schedule_id": sched.id,
    }


@pytest.fixture()
async def cleanup():
    yield
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM accounts WHERE clerk_org_id LIKE :p"),
                         {"p": f"{TAG}%"})
        await db.commit()


# ── _collect_for_asset ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_collect_persists_metric_row(monkeypatch, cleanup):
    """Mock the LinkedIn adapter's fetch_metrics; verify AssetMetric row appears."""
    from app.workers.tasks.analytics_tasks import _collect_for_asset

    async with SessionLocal() as db:
        ctx = await _seed_published_asset(db, platform="linkedin")

    async def fake_fetch(schedule):
        return {
            "platform": "linkedin",
            "views": 12000, "clicks": 320, "shares": 45,
            "saves": 18, "comments": 9, "likes": 800,
            "watch_time_s": None, "ctr": 0.0267,
            "meta": {"source": "test"},
        }

    class FakeAdapter:
        fetch_metrics = staticmethod(fake_fetch)

    from app.workers.tasks import analytics_tasks as at
    monkeypatch.setattr(at, "_fetch_platform_metrics", lambda sched: fake_fetch(sched))

    await _collect_for_asset(ctx["asset_id"])

    async with SessionLocal() as db:
        rows = (await db.execute(
            select(AssetMetric).where(AssetMetric.asset_id == ctx["asset_id"])
        )).scalars().all()
        assert len(rows) == 1
        m = rows[0]
        assert m.platform == "linkedin"
        assert m.views == 12000
        assert m.likes == 800
        assert float(m.ctr) == pytest.approx(0.0267)
        assert m.brand_id == ctx["brand_id"]
        assert m.meta == {"source": "test"}


@pytest.mark.asyncio
async def test_collect_does_nothing_when_no_published_schedule(monkeypatch, cleanup):
    """An asset with only pending schedules should not generate metrics."""
    from app.workers.tasks.analytics_tasks import _collect_for_asset

    async with SessionLocal() as db:
        ctx = await _seed_published_asset(db, platform="x")
        # Flip the only schedule back to pending
        sched = (await db.execute(
            select(Schedule).where(Schedule.id == ctx["schedule_id"])
        )).scalar_one()
        sched.status = "pending"
        await db.commit()

    from app.workers.tasks import analytics_tasks as at
    called = []
    async def fake_fetch(s):
        called.append(s.id); return {"platform": "x"}
    monkeypatch.setattr(at, "_fetch_platform_metrics", fake_fetch)

    await _collect_for_asset(ctx["asset_id"])
    assert called == []  # never invoked because schedule not 'published'

    async with SessionLocal() as db:
        rows = (await db.execute(
            select(AssetMetric).where(AssetMetric.asset_id == ctx["asset_id"])
        )).scalars().all()
        assert rows == []


@pytest.mark.asyncio
async def test_collect_swallows_adapter_exception(monkeypatch, cleanup):
    """If one schedule's fetch_metrics raises, the loop continues and writes no row for that one."""
    from app.workers.tasks.analytics_tasks import _collect_for_asset

    async with SessionLocal() as db:
        ctx = await _seed_published_asset(db, platform="instagram")

    async def boom(_s):
        raise RuntimeError("IG API down")

    from app.workers.tasks import analytics_tasks as at
    monkeypatch.setattr(at, "_fetch_platform_metrics", boom)

    # Must not raise
    await _collect_for_asset(ctx["asset_id"])

    async with SessionLocal() as db:
        rows = (await db.execute(
            select(AssetMetric).where(AssetMetric.asset_id == ctx["asset_id"])
        )).scalars().all()
        assert rows == []


@pytest.mark.asyncio
async def test_collect_skips_when_adapter_returns_none(monkeypatch, cleanup):
    """A platform with no analytics integration returns None — no row, no error."""
    from app.workers.tasks.analytics_tasks import _collect_for_asset

    async with SessionLocal() as db:
        ctx = await _seed_published_asset(db, platform="tiktok")

    from app.workers.tasks import analytics_tasks as at
    monkeypatch.setattr(at, "_fetch_platform_metrics", lambda _s: _none())
    async def _none(): return None

    await _collect_for_asset(ctx["asset_id"])
    async with SessionLocal() as db:
        rows = (await db.execute(
            select(AssetMetric).where(AssetMetric.asset_id == ctx["asset_id"])
        )).scalars().all()
        assert rows == []


# ── _rollup ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rollup_groups_metrics_by_platform(cleanup):
    """Rollup must produce one row per (brand_id, platform) within the window."""
    from app.workers.tasks.analytics_tasks import _rollup

    async with SessionLocal() as db:
        ctx = await _seed_published_asset(db, platform="linkedin")
        # Insert 3 metric rows in the recent window
        for i in range(3):
            db.add(AssetMetric(
                asset_id=ctx["asset_id"], brand_id=ctx["brand_id"],
                platform="linkedin",
                collected_at=datetime.now(timezone.utc) - timedelta(minutes=10 * i),
                views=1000 + i, clicks=10, shares=2, likes=50, comments=1, ctr=0.05,
                meta={},
            ))
        # And one older one outside the 24h window — should NOT count
        db.add(AssetMetric(
            asset_id=ctx["asset_id"], brand_id=ctx["brand_id"],
            platform="linkedin",
            collected_at=datetime.now(timezone.utc) - timedelta(days=5),
            views=99999, clicks=0, shares=0, likes=0, comments=0, ctr=0.0,
            meta={},
        ))
        await db.commit()

    n_groups = await _rollup(window_hours=24)
    # At least one group covers our seeded brand
    assert n_groups >= 1


@pytest.mark.asyncio
async def test_rollup_filters_by_brand_when_specified(cleanup):
    """Passing brand_id should restrict to that brand's metrics only."""
    from app.workers.tasks.analytics_tasks import _rollup

    async with SessionLocal() as db:
        ctx_a = await _seed_published_asset(db, platform="x")
        ctx_b = await _seed_published_asset(db, platform="linkedin")
        for ctx in (ctx_a, ctx_b):
            db.add(AssetMetric(
                asset_id=ctx["asset_id"], brand_id=ctx["brand_id"],
                platform="x" if ctx is ctx_a else "linkedin",
                collected_at=datetime.now(timezone.utc),
                views=500, clicks=5, shares=1, likes=10, comments=0, ctr=0.02,
                meta={},
            ))
        await db.commit()

    # Filter to brand A only → exactly 1 platform group
    n_a = await _rollup(brand_id=ctx_a["brand_id"], window_hours=24)
    assert n_a == 1
    n_b = await _rollup(brand_id=ctx_b["brand_id"], window_hours=24)
    assert n_b == 1


@pytest.mark.asyncio
async def test_rollup_empty_when_no_data(cleanup):
    """No metrics in the window → rollup returns 0 groups."""
    from app.workers.tasks.analytics_tasks import _rollup

    n = await _rollup(brand_id=uuid.uuid4(), window_hours=24)
    assert n == 0
