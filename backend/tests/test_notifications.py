"""Notifications endpoint round-trip:
  * push-token register (idempotent upsert + cross-user reassignment)
  * notifications list (per-user isolation)
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

os.environ.setdefault("SECRET_KEY", "testtesttesttesttesttesttesttest")

from app.api.deps.auth import CurrentUser, current_user  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models.notification import Notification, PushToken  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.provisioning import get_or_create_account  # noqa: E402

TAG = f"test_notif_{uuid.uuid4().hex[:8]}"


def _user(name: str) -> CurrentUser:
    return CurrentUser(
        clerk_user_id=f"{TAG}_{name}",
        clerk_org_id=f"{TAG}_{name}_org",
        email=f"{name}@test.local",
        role="owner",
        raw={},
    )


@pytest.fixture()
def auth_as():
    """Override the FastAPI auth dep to act as a specific synthetic Clerk user."""
    def _set(u: CurrentUser):
        app.dependency_overrides[current_user] = lambda: u
    yield _set
    app.dependency_overrides.pop(current_user, None)


@pytest.fixture()
async def cleanup_tag():
    yield
    async with SessionLocal() as db:
        # Order matters because of FK cascades
        await db.execute(text("DELETE FROM accounts WHERE clerk_org_id LIKE :p"),
                         {"p": f"{TAG}_%"})
        await db.execute(text("DELETE FROM users WHERE clerk_user_id LIKE :p"),
                         {"p": f"{TAG}_%"})
        await db.commit()


@pytest.mark.asyncio
async def test_register_push_token_first_time_creates_row(auth_as, cleanup_tag):
    u = _user("a")
    auth_as(u)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.post("/v1/notifications/register",
                          json={"token": "ExponentPushToken[xyz1]", "platform": "ios"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["updated"] is False

    async with SessionLocal() as db:
        row = (await db.execute(
            select(PushToken).where(PushToken.token == "ExponentPushToken[xyz1]")
        )).scalar_one()
        assert row.platform == "ios"
        # The user row should have been auto-created
        usr = (await db.execute(
            select(User).where(User.clerk_user_id == u.clerk_user_id)
        )).scalar_one()
        assert row.user_id == usr.id


@pytest.mark.asyncio
async def test_register_push_token_idempotent_updates(auth_as, cleanup_tag):
    u = _user("b")
    auth_as(u)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r1 = await cx.post("/v1/notifications/register",
                           json={"token": "ExponentPushToken[xyz2]", "platform": "ios"})
        r2 = await cx.post("/v1/notifications/register",
                           json={"token": "ExponentPushToken[xyz2]", "platform": "android"})
    assert r1.json()["updated"] is False
    assert r2.json()["updated"] is True
    assert r1.json()["id"] == r2.json()["id"], "same token must keep the same row id"

    async with SessionLocal() as db:
        rows = (await db.execute(
            select(PushToken).where(PushToken.token == "ExponentPushToken[xyz2]")
        )).scalars().all()
        assert len(rows) == 1
        assert rows[0].platform == "android"


@pytest.mark.asyncio
async def test_register_push_token_reassigns_across_users(auth_as, cleanup_tag):
    """If user A registers token X, then user B sends the same token, the row should reassign to B
    (a real device handed off between user accounts)."""
    user_a = _user("c")
    user_b = _user("d")

    auth_as(user_a)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r1 = await cx.post("/v1/notifications/register",
                           json={"token": "TKN_SHARED", "platform": "ios"})
    assert r1.status_code == 200

    auth_as(user_b)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r2 = await cx.post("/v1/notifications/register",
                           json={"token": "TKN_SHARED", "platform": "android"})
    assert r2.json()["updated"] is True

    async with SessionLocal() as db:
        usr_b = (await db.execute(
            select(User).where(User.clerk_user_id == user_b.clerk_user_id)
        )).scalar_one()
        row = (await db.execute(select(PushToken).where(PushToken.token == "TKN_SHARED"))).scalar_one()
        assert row.user_id == usr_b.id


@pytest.mark.asyncio
async def test_list_notifications_returns_only_users_rows(auth_as, cleanup_tag):
    user_a = _user("e")
    user_b = _user("f")

    # Manually create users (so we can insert notifications keyed to them).
    async with SessionLocal() as db:
        await get_or_create_account(db, user_a)
        await get_or_create_account(db, user_b)
        ua = User(clerk_user_id=user_a.clerk_user_id, email=user_a.email)
        ub = User(clerk_user_id=user_b.clerk_user_id, email=user_b.email)
        db.add_all([ua, ub])
        await db.flush()

        from sqlalchemy import select
        from app.models.account import Account
        acct_a = (await db.execute(
            select(Account).where(Account.clerk_org_id == user_a.clerk_org_id)
        )).scalar_one()
        acct_b = (await db.execute(
            select(Account).where(Account.clerk_org_id == user_b.clerk_org_id)
        )).scalar_one()

        # Two notifications for A, one for B.
        for i in range(2):
            db.add(Notification(
                account_id=acct_a.id, user_id=ua.id, kind="info",
                title=f"A {i}", body="...",
                created_at=datetime.now(timezone.utc),
            ))
        db.add(Notification(
            account_id=acct_b.id, user_id=ub.id, kind="info",
            title="B only", body="...",
            created_at=datetime.now(timezone.utc),
        ))
        await db.commit()

    auth_as(user_a)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get("/v1/notifications")
    assert r.status_code == 200
    rows = r.json()
    titles = {row["title"] for row in rows}
    assert {"A 0", "A 1"} == titles
    assert "B only" not in titles
