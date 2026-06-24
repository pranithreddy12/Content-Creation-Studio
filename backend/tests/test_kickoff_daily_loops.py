"""Daily-loop kickoff dispatcher + Redis lock idempotency.

`kickoff_daily_loops` (the beat job) iterates over active brands, checks the
local-TZ window via `_due_now`, and tries to acquire a 23h Redis lock under
`loop:lock:<brand_id>:<YYYY-MM-DD>`. Only brands that:
  - are active
  - hit the publish_window.start in the last 15min
  - get the Redis NX lock first

…have `run_brand_loop` dispatched.

We mock Redis + datetime + the chained task so this is a pure unit test of the
dispatch policy.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text

os.environ.setdefault("SECRET_KEY", "testtesttesttesttesttesttesttest")

from app.db.session import SessionLocal  # noqa: E402
from app.models.account import Account, Workspace  # noqa: E402
from app.models.brand import Brand  # noqa: E402

TAG = f"test_kick_{uuid.uuid4().hex[:8]}"
SLUG = TAG.replace("_", "-")


async def _make_brand(
    db, *, name: str, tz: str = "UTC", status: str = "active",
    window_start: str = "09:00",
) -> Brand:
    acct = Account(clerk_org_id=f"{TAG}_{name}_o", name=name, plan="free")
    db.add(acct); await db.flush()
    ws = Workspace(account_id=acct.id, name="Default")
    db.add(ws); await db.flush()
    b = Brand(
        account_id=acct.id, workspace_id=ws.id,
        name=name, slug=f"{SLUG}-{name}"[:60],
        primary_topic="AI", status=status, timezone=tz,
        publish_window={"start": window_start, "end": "18:00"},
        daily_quota=1,
    )
    db.add(b); await db.flush()
    await db.commit()
    return b


@pytest.fixture()
async def cleanup():
    yield
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM accounts WHERE clerk_org_id LIKE :p"),
                         {"p": f"{TAG}%"})
        await db.commit()


# ── _due_now ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_due_now_returns_true_when_local_time_in_first_15min_of_window(monkeypatch):
    """A brand whose local time is 09:07 (window starts 09:00) is due."""
    from app.workers.tasks import loop_tasks as lt

    fake_brand = MagicMock()
    fake_brand.timezone = "UTC"
    fake_brand.publish_window = {"start": "09:00", "end": "18:00"}

    class _FakeDatetime:
        @staticmethod
        def now(tz=None):
            return datetime(2026, 6, 24, 9, 7, 0, tzinfo=tz or timezone.utc)

    monkeypatch.setattr(lt, "datetime", _FakeDatetime)
    assert lt._due_now(fake_brand) is True


@pytest.mark.asyncio
async def test_due_now_returns_false_when_outside_window(monkeypatch):
    """At 13:00 with window starting 09:00, the brand is NOT in the 15-min trigger zone."""
    from app.workers.tasks import loop_tasks as lt

    fake_brand = MagicMock()
    fake_brand.timezone = "UTC"
    fake_brand.publish_window = {"start": "09:00", "end": "18:00"}

    class _FakeDatetime:
        @staticmethod
        def now(tz=None):
            return datetime(2026, 6, 24, 13, 0, 0, tzinfo=tz or timezone.utc)

    monkeypatch.setattr(lt, "datetime", _FakeDatetime)
    assert lt._due_now(fake_brand) is False


@pytest.mark.asyncio
async def test_due_now_returns_false_when_before_window_start(monkeypatch):
    from app.workers.tasks import loop_tasks as lt

    fake_brand = MagicMock()
    fake_brand.timezone = "UTC"
    fake_brand.publish_window = {"start": "09:00", "end": "18:00"}

    class _FakeDatetime:
        @staticmethod
        def now(tz=None):
            return datetime(2026, 6, 24, 8, 30, 0, tzinfo=tz or timezone.utc)

    monkeypatch.setattr(lt, "datetime", _FakeDatetime)
    assert lt._due_now(fake_brand) is False


@pytest.mark.asyncio
async def test_due_now_handles_invalid_timezone_gracefully(monkeypatch):
    """Bad TZ string falls back to UTC (doesn't crash)."""
    from app.workers.tasks import loop_tasks as lt

    fake_brand = MagicMock()
    fake_brand.timezone = "Not/A/Real/Zone"
    fake_brand.publish_window = {"start": "09:00", "end": "18:00"}

    result = lt._due_now(fake_brand)  # must not raise
    assert isinstance(result, bool)


# ── _eligible_brands ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_eligible_brands_returns_only_active(cleanup):
    from app.workers.tasks.loop_tasks import _eligible_brands

    async with SessionLocal() as db:
        await _make_brand(db, name="active1", status="active")
        await _make_brand(db, name="active2", status="active")
        await _make_brand(db, name="archived", status="archived")
        await _make_brand(db, name="paused", status="paused")

    brands = await _eligible_brands()
    test_brands = [b for b in brands if b.slug.startswith(SLUG)]
    statuses = {b.status for b in test_brands}
    assert statuses == {"active"}, f"non-active brands leaked through: {statuses}"


# ── kickoff_daily_loops dispatcher policy ───────────────────────────

@pytest.mark.asyncio
async def test_kickoff_dispatches_only_to_due_brands(monkeypatch, cleanup):
    """Two brands are eligible; one is due, one is not. Only the due one fires."""
    from app.workers.tasks import loop_tasks as lt

    async with SessionLocal() as db:
        due_brand = await _make_brand(db, name="due", window_start="09:00")
        not_due = await _make_brand(db, name="quiet", window_start="03:00")

    # Mock _due_now to be deterministic
    def fake_due(b):
        return b.id == due_brand.id
    monkeypatch.setattr(lt, "_due_now", fake_due)

    # Mock Redis NX lock to always succeed
    async def fake_set(*args, **kwargs):
        return True
    monkeypatch.setattr(lt.redis, "set", fake_set)

    # Capture run_brand_loop dispatches
    fired = []
    class FakeAsyncResult: id = "fake-id"
    monkeypatch.setattr(lt.run_brand_loop, "delay",
                        lambda brand_id: fired.append(brand_id) or FakeAsyncResult())

    result = await lt._do_kickoff()
    assert result["fired"] == [str(due_brand.id)]
    assert fired == [str(due_brand.id)]


@pytest.mark.asyncio
async def test_kickoff_skips_when_lock_already_held(monkeypatch, cleanup):
    """A brand whose loop:lock key is already set today must NOT re-fire."""
    from app.workers.tasks import loop_tasks as lt

    async with SessionLocal() as db:
        brand = await _make_brand(db, name="locked")

    monkeypatch.setattr(lt, "_due_now", lambda b: True)

    # Redis.set with NX returns False when the key already exists
    async def already_held(*args, **kwargs):
        return False
    monkeypatch.setattr(lt.redis, "set", already_held)

    fired = []
    monkeypatch.setattr(lt.run_brand_loop, "delay",
                        lambda bid: fired.append(bid))

    result = await lt._do_kickoff()
    assert result["fired"] == [], f"loop fired despite held lock: {result}"
    assert fired == []


@pytest.mark.asyncio
async def test_kickoff_returns_empty_when_no_brands_due(monkeypatch, cleanup):
    from app.workers.tasks import loop_tasks as lt

    async with SessionLocal() as db:
        await _make_brand(db, name="any")

    monkeypatch.setattr(lt, "_due_now", lambda b: False)  # nobody due
    async def fake_set(*a, **kw): return True
    monkeypatch.setattr(lt.redis, "set", fake_set)

    fired = []
    monkeypatch.setattr(lt.run_brand_loop, "delay", lambda bid: fired.append(bid))

    result = await lt._do_kickoff()
    assert result == {"fired": []}
    assert fired == []


@pytest.mark.asyncio
async def test_kickoff_lock_key_includes_today_and_brand(monkeypatch, cleanup):
    """The Redis lock key must be `loop:lock:<brand_id>:<YYYY-MM-DD>` with 23h TTL."""
    from app.workers.tasks import loop_tasks as lt

    async with SessionLocal() as db:
        brand = await _make_brand(db, name="keycheck")

    monkeypatch.setattr(lt, "_due_now", lambda b: True)

    captured = {}
    async def capture_set(key, value, ex=None, nx=False):
        captured["key"] = key
        captured["ex"] = ex
        captured["nx"] = nx
        return True
    monkeypatch.setattr(lt.redis, "set", capture_set)

    monkeypatch.setattr(lt.run_brand_loop, "delay", lambda bid: None)
    await lt._do_kickoff()

    assert captured["key"].startswith(f"loop:lock:{brand.id}:")
    # YYYY-MM-DD has 10 chars
    suffix = captured["key"].split(":")[-1]
    assert len(suffix) == 10 and suffix[4] == "-" and suffix[7] == "-"
    assert captured["ex"] == 23 * 3600
    assert captured["nx"] is True
