"""Cross-tenant scoping for /v1/publishing/* endpoints.

Covers three production bugs:
  * Bug #8: GET /channels/{brand_id} previously listed any brand's channels
            without checking account ownership.
  * Bug #9: DELETE /channels/{channel_id} previously called .scalar_one() —
            crashed with 500 on missing/foreign channel — and had no tenant
            check, letting a user disconnect another tenant's channel.
  * Bug #10: POST /publishing/wordpress and POST /publishing/email accepted
             brand_id from the payload without verifying the brand belonged
             to the caller's account.
"""
from __future__ import annotations

import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

os.environ.setdefault("SECRET_KEY", "testtesttesttesttesttesttesttest")

from app.api.deps.auth import CurrentUser, current_user  # noqa: E402
from app.core.security import encrypt  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models.account import Account  # noqa: E402
from app.models.brand import Brand  # noqa: E402
from app.models.publishing import PublishChannel  # noqa: E402
from app.services.provisioning import get_or_create_account  # noqa: E402

TAG = f"test_pubiso_{uuid.uuid4().hex[:8]}"
SLUG = TAG.replace("_", "-")


def _user(suffix: str = "") -> CurrentUser:
    return CurrentUser(
        clerk_user_id=f"{TAG}{suffix}_u",
        clerk_org_id=f"{TAG}{suffix}_o",
        email=f"p{suffix}@test.local",
        role="owner", raw={},
    )


@pytest.fixture()
def auth_as():
    def _set(u: CurrentUser):
        app.dependency_overrides[current_user] = lambda: u
    yield _set
    app.dependency_overrides.pop(current_user, None)


@pytest.fixture()
async def cleanup():
    yield
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM accounts WHERE clerk_org_id LIKE :p"),
                         {"p": f"{TAG}%"})
        await db.commit()


@pytest.fixture()
async def two_tenants_with_channels():
    """Tenant A owns brand A + 2 channels; tenant B owns brand B + 1 channel."""
    user_a = _user("_A")
    user_b = _user("_B")

    async with SessionLocal() as db:
        acct_a = await get_or_create_account(db, user_a)
        acct_b = await get_or_create_account(db, user_b)
        ws_a = (await db.execute(text("SELECT id FROM workspaces WHERE account_id = :a LIMIT 1"),
                                  {"a": acct_a.id})).scalar_one()
        ws_b = (await db.execute(text("SELECT id FROM workspaces WHERE account_id = :a LIMIT 1"),
                                  {"a": acct_b.id})).scalar_one()
        brand_a = Brand(account_id=acct_a.id, workspace_id=ws_a,
                        name="A", slug=f"{SLUG}-a"[:60], primary_topic="AI")
        brand_b = Brand(account_id=acct_b.id, workspace_id=ws_b,
                        name="B", slug=f"{SLUG}-b"[:60], primary_topic="AI")
        db.add_all([brand_a, brand_b]); await db.flush()

        chans_a = [
            PublishChannel(account_id=acct_a.id, brand_id=brand_a.id,
                           platform="wordpress", display_name="wp1",
                           oauth_blob={"ct": encrypt('{"x":1}')}, status="connected"),
            PublishChannel(account_id=acct_a.id, brand_id=brand_a.id,
                           platform="email", display_name="em1",
                           oauth_blob={"ct": encrypt('{"x":1}')}, status="connected"),
        ]
        chan_b = PublishChannel(account_id=acct_b.id, brand_id=brand_b.id,
                                platform="wordpress", display_name="wpB",
                                oauth_blob={"ct": encrypt('{"x":1}')}, status="connected")
        db.add_all(chans_a + [chan_b]); await db.commit()
        yield {
            "user_a": user_a, "user_b": user_b,
            "brand_a": brand_a.id, "brand_b": brand_b.id,
            "chan_a_ids": [c.id for c in chans_a],
            "chan_b_id": chan_b.id,
        }


# ── Bug #8: list_channels cross-tenant ───────────────────────────────

@pytest.mark.asyncio
async def test_list_channels_owner_sees_their_channels(auth_as, two_tenants_with_channels, cleanup):
    auth_as(two_tenants_with_channels["user_a"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get(f"/v1/publishing/channels/{two_tenants_with_channels['brand_a']}")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 2
    assert {row["platform"] for row in rows} == {"wordpress", "email"}


@pytest.mark.asyncio
async def test_list_channels_cross_tenant_returns_empty(auth_as, two_tenants_with_channels, cleanup):
    """Tenant B requesting tenant A's brand_id must NOT see A's channels."""
    auth_as(two_tenants_with_channels["user_b"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get(f"/v1/publishing/channels/{two_tenants_with_channels['brand_a']}")
    assert r.status_code == 200
    assert r.json() == [], "list must scope by account_id and return no rows for foreign brands"


# ── Bug #9: disconnect_channel cross-tenant + missing ────────────────

@pytest.mark.asyncio
async def test_disconnect_channel_owner_succeeds(auth_as, two_tenants_with_channels, cleanup):
    auth_as(two_tenants_with_channels["user_a"])
    chan_id = two_tenants_with_channels["chan_a_ids"][0]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.delete(f"/v1/publishing/channels/{chan_id}")
    assert r.status_code == 204

    async with SessionLocal() as db:
        row = (await db.execute(
            select(PublishChannel).where(PublishChannel.id == chan_id)
        )).scalar_one()
        assert row.status == "disconnected"


@pytest.mark.asyncio
async def test_disconnect_channel_unknown_id_returns_404(auth_as, two_tenants_with_channels, cleanup):
    """Previously .scalar_one() raised NoResultFound → 500. Now 404."""
    auth_as(two_tenants_with_channels["user_a"])
    bogus = uuid.uuid4()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.delete(f"/v1/publishing/channels/{bogus}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_disconnect_channel_cross_tenant_blocked(auth_as, two_tenants_with_channels, cleanup):
    """Tenant B addressing tenant A's channel must 404, and A's channel must remain connected."""
    auth_as(two_tenants_with_channels["user_b"])
    chan_a_id = two_tenants_with_channels["chan_a_ids"][1]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.delete(f"/v1/publishing/channels/{chan_a_id}")
    assert r.status_code == 404

    async with SessionLocal() as db:
        row = (await db.execute(
            select(PublishChannel).where(PublishChannel.id == chan_a_id)
        )).scalar_one()
        assert row.status == "connected", "foreign tenant must not be able to disconnect"


# ── Bug #10: POST wordpress / email cross-tenant brand_id ───────────

@pytest.mark.asyncio
async def test_create_wordpress_channel_under_foreign_brand_is_404(auth_as, two_tenants_with_channels, cleanup):
    """Tenant B sending tenant A's brand_id must NOT successfully create a channel."""
    auth_as(two_tenants_with_channels["user_b"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.post("/v1/publishing/wordpress", json={
            "brand_id": str(two_tenants_with_channels["brand_a"]),
            "site": "https://evil.example",
            "username": "intruder",
            "app_password": "lol",
        })
    assert r.status_code == 404, "must not accept foreign brand_id"

    # Confirm no channel got created
    async with SessionLocal() as db:
        count = (await db.execute(text(
            "SELECT count(*) FROM publish_channels WHERE display_name = 'https://evil.example'"
        ))).scalar()
        assert count == 0


@pytest.mark.asyncio
async def test_create_email_channel_under_foreign_brand_is_404(auth_as, two_tenants_with_channels, cleanup):
    auth_as(two_tenants_with_channels["user_b"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.post("/v1/publishing/email", json={
            "brand_id": str(two_tenants_with_channels["brand_a"]),
            "api_key": "sk_evil",
            "sender_name": "Intruder",
            "sender_email": "evil@example.com",
            "list_ids": [1, 2],
        })
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_wordpress_channel_under_owned_brand_succeeds(auth_as, two_tenants_with_channels, cleanup):
    auth_as(two_tenants_with_channels["user_b"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.post("/v1/publishing/wordpress", json={
            "brand_id": str(two_tenants_with_channels["brand_b"]),
            "site": "https://b.example",
            "username": "b_user",
            "app_password": "secret",
        })
    assert r.status_code == 200
    body = r.json()
    assert "id" in body
