"""Analytics + calendar empty-state behavior.

Brand-new users hitting /dashboard load these endpoints with no brand selected.
They must return sensible zero/empty payloads, not 404 or 500.
"""
from __future__ import annotations

import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

os.environ.setdefault("SECRET_KEY", "testtesttesttesttesttesttesttest")

from app.api.deps.auth import CurrentUser, current_user  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402

TAG = f"test_aempty_{uuid.uuid4().hex[:8]}"


def _user() -> CurrentUser:
    return CurrentUser(
        clerk_user_id=f"{TAG}_u",
        clerk_org_id=f"{TAG}_o",
        email="e@test.local",
        role="owner", raw={},
    )


@pytest.fixture()
def auth_as_user():
    u = _user()
    app.dependency_overrides[current_user] = lambda: u
    yield u
    app.dependency_overrides.pop(current_user, None)


@pytest.fixture()
async def cleanup():
    yield
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM accounts WHERE clerk_org_id = :o"), {"o": f"{TAG}_o"})
        await db.commit()


@pytest.mark.asyncio
async def test_overview_for_brand_new_user_returns_zeros(auth_as_user, cleanup):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get("/v1/analytics/overview")
    assert r.status_code == 200
    o = r.json()
    assert o["generated"] == 0
    assert o["scheduled"] == 0
    assert o["published"] == 0


@pytest.mark.asyncio
async def test_overview_with_unknown_brand_id_returns_zeros(auth_as_user, cleanup):
    """Bogus brand_id (not owned by user) silently returns zeros — same as empty."""
    bogus = uuid.uuid4()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get(f"/v1/analytics/overview?brand_id={bogus}")
    assert r.status_code == 200
    o = r.json()
    assert o == {
        "generated": 0, "scheduled": 0, "published": 0,
        "avg_viral_score": 0.0, "avg_seo_score": 0.0, "revenue_attributed": 0.0,
    }


@pytest.mark.asyncio
async def test_timeseries_for_brand_new_user_returns_empty_list(auth_as_user, cleanup):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get("/v1/analytics/timeseries?window=30d")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_timeseries_rejects_unknown_window(auth_as_user, cleanup):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get("/v1/analytics/timeseries?window=99y")
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_calendar_for_unknown_brand_returns_empty(auth_as_user, cleanup):
    bogus = uuid.uuid4()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get(
            f"/v1/calendar?brand_id={bogus}"
            "&from=2026-01-01T00:00:00Z&to=2026-12-31T00:00:00Z"
        )
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_calendar_rejects_malformed_date(auth_as_user, cleanup):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get(
            f"/v1/calendar?brand_id={uuid.uuid4()}"
            "&from=not-a-date&to=2026-12-31T00:00:00Z"
        )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_overview_first_touch_provisions_account(auth_as_user, cleanup):
    """Hitting /overview for the very first time should auto-provision Account + Workspace.
    A follow-up call must see a non-error response and the row count rises in `accounts`."""
    from sqlalchemy import func, select
    from app.models.account import Account

    async with SessionLocal() as db:
        before = (await db.execute(
            select(func.count()).select_from(Account)
            .where(Account.clerk_org_id == f"{TAG}_o")
        )).scalar()
    assert before == 0

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get("/v1/analytics/overview")
    assert r.status_code == 200

    async with SessionLocal() as db:
        after = (await db.execute(
            select(func.count()).select_from(Account)
            .where(Account.clerk_org_id == f"{TAG}_o")
        )).scalar()
    assert after == 1, "first-touch /overview did not provision the Account"
