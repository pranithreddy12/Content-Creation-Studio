"""Input-validation tests for `/v1/publishing/*` endpoints.

Bypasses Clerk via dependency_overrides[current_user] so we exercise the live
router shape (FastAPI 4xx behavior) without needing a real JWT.
"""
from __future__ import annotations

import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

os.environ.setdefault("SECRET_KEY", "testtesttesttesttesttesttesttest")

from app.api.deps.auth import CurrentUser, current_user  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models.brand import Brand  # noqa: E402
from app.models.publishing import PublishChannel  # noqa: E402
from app.services.provisioning import get_or_create_account  # noqa: E402

TAG = f"test_pubval_{uuid.uuid4().hex[:8]}"
SLUG = TAG.replace("_", "-")


def _user() -> CurrentUser:
    return CurrentUser(
        clerk_user_id=f"{TAG}_u",
        clerk_org_id=f"{TAG}_o",
        email="pub@test.local",
        role="owner",
        raw={},
    )


@pytest.fixture()
def auth_as_user():
    u = _user()
    app.dependency_overrides[current_user] = lambda: u
    yield u
    app.dependency_overrides.pop(current_user, None)


@pytest.fixture()
async def seeded_brand():
    user = _user()
    async with SessionLocal() as db:
        acct = await get_or_create_account(db, user)
        ws_id = (await db.execute(
            text("SELECT id FROM workspaces WHERE account_id = :a LIMIT 1"),
            {"a": acct.id},
        )).scalar_one()
        brand = Brand(
            account_id=acct.id, workspace_id=ws_id,
            name="PubBrand", slug=f"{SLUG}-pb"[:60],
        )
        db.add(brand)
        await db.commit()
        bid = brand.id
    yield bid
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM accounts WHERE clerk_org_id = :o"), {"o": f"{TAG}_o"})
        await db.commit()


# ─── GET /v1/publishing/oauth/start ────────────────────────────────────

@pytest.mark.asyncio
async def test_oauth_start_unknown_platform_returns_400(auth_as_user, seeded_brand):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get(
            f"/v1/publishing/oauth/start?platform=myspace&brand_id={seeded_brand}"
            "&redirect_uri=https://x/callback"
        )
    assert r.status_code == 400
    assert "unsupported platform" in r.json()["detail"]


@pytest.mark.asyncio
async def test_oauth_start_malformed_brand_id_returns_422(auth_as_user):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get(
            "/v1/publishing/oauth/start?platform=linkedin&brand_id=not-a-uuid"
            "&redirect_uri=https://x/callback"
        )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_oauth_start_missing_required_query_returns_422(auth_as_user):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        # Missing redirect_uri
        r = await cx.get("/v1/publishing/oauth/start?platform=linkedin&brand_id="
                         f"{uuid.uuid4()}")
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_oauth_start_missing_admin_creds_returns_400(auth_as_user, seeded_brand):
    """When platform OAuth creds aren't configured in .env AND no BYO sent, 400."""
    from app.core import config as cfg
    # Force LinkedIn creds to be unset so the helper raises
    orig_id, orig_sec = cfg.settings.linkedin_client_id, cfg.settings.linkedin_client_secret
    cfg.settings.linkedin_client_id = None
    cfg.settings.linkedin_client_secret = None
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
            r = await cx.get(
                f"/v1/publishing/oauth/start?platform=linkedin&brand_id={seeded_brand}"
                "&redirect_uri=https://x/callback"
            )
    finally:
        cfg.settings.linkedin_client_id = orig_id
        cfg.settings.linkedin_client_secret = orig_sec

    assert r.status_code == 400
    assert "missing setting" in r.json()["detail"]


# ─── POST /v1/publishing/oauth/start (BYO) ─────────────────────────────

@pytest.mark.asyncio
async def test_byo_oauth_start_returns_url(auth_as_user, seeded_brand):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.post("/v1/publishing/oauth/start", json={
            "platform": "linkedin",
            "brand_id": str(seeded_brand),
            "redirect_uri": "https://my-app/callback",
            "client_id": "USER_CID",
            "client_secret": "USER_SEC",
        })
    assert r.status_code == 200
    body = r.json()
    assert "linkedin.com/oauth/v2/authorization" in body["url"]
    assert "client_id=USER_CID" in body["url"]


@pytest.mark.asyncio
async def test_byo_oauth_start_rejects_unknown_platform(auth_as_user, seeded_brand):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.post("/v1/publishing/oauth/start", json={
            "platform": "myspace",
            "brand_id": str(seeded_brand),
            "redirect_uri": "https://x",
            "client_id": "x", "client_secret": "y",
        })
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_byo_oauth_start_bad_brand_id_returns_422(auth_as_user):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.post("/v1/publishing/oauth/start", json={
            "platform": "linkedin",
            "brand_id": "definitely-not-a-uuid",
            "redirect_uri": "https://x",
            "client_id": "x", "client_secret": "y",
        })
    assert r.status_code == 422


# ─── POST /v1/publishing/wordpress ─────────────────────────────────────

@pytest.mark.asyncio
async def test_create_wordpress_channel_persists_encrypted_blob(auth_as_user, seeded_brand):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.post("/v1/publishing/wordpress", json={
            "brand_id": str(seeded_brand),
            "site": "https://blog.example.com",
            "username": "admin",
            "app_password": "xxxx yyyy zzzz",
            "display_name": "My Blog",
        })
    assert r.status_code == 200
    cid = r.json()["id"]

    async with SessionLocal() as db:
        row = (await db.execute(
            select(PublishChannel).where(PublishChannel.id == uuid.UUID(cid))
        )).scalar_one()
        assert row.platform == "wordpress"
        assert row.display_name == "My Blog"
        assert row.status == "connected"
        # oauth_blob is stored as encrypted JSON
        blob_ct = row.oauth_blob.get("ct") if isinstance(row.oauth_blob, dict) else None
        assert blob_ct
        # Encrypted blob should NOT contain the plaintext password
        assert "xxxx yyyy zzzz" not in str(row.oauth_blob)


@pytest.mark.asyncio
async def test_create_wordpress_missing_field_returns_422(auth_as_user, seeded_brand):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        # missing `username`
        r = await cx.post("/v1/publishing/wordpress", json={
            "brand_id": str(seeded_brand),
            "site": "https://b/",
            "app_password": "x",
        })
    assert r.status_code == 422


# ─── POST /v1/publishing/email ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_email_channel_normalizes_list_ids(auth_as_user, seeded_brand):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.post("/v1/publishing/email", json={
            "brand_id": str(seeded_brand),
            "api_key": "brevo_xxx",
            "sender_name": "Studio",
            "sender_email": "hi@studio.io",
            "list_ids": [1, 2, 3],
        })
    assert r.status_code == 200
    cid = r.json()["id"]

    async with SessionLocal() as db:
        row = (await db.execute(
            select(PublishChannel).where(PublishChannel.id == uuid.UUID(cid))
        )).scalar_one()
        assert row.platform == "email"
        assert row.display_name == "hi@studio.io"


# ─── GET /v1/publishing/channels/{brand_id} ────────────────────────────

@pytest.mark.asyncio
async def test_list_channels_for_unknown_brand_returns_empty(auth_as_user):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get(f"/v1/publishing/channels/{uuid.uuid4()}")
    assert r.status_code == 200
    assert r.json() == []
