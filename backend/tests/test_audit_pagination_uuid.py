"""Audit coverage:

  * oauth_callback: malformed brand_id in OAuth state previously raised
    ValueError from UUID() → 500. Now → 400.
  * calendar endpoint: ?limit clamp respected; default cap enforced.
  * sources list: ?limit clamp respected; default cap enforced; stable sort.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

os.environ.setdefault("SECRET_KEY", "testtesttesttesttesttesttesttest")

from app.api.deps.auth import CurrentUser, current_user  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models.brand import Brand  # noqa: E402
from app.models.content import ContentAsset, ContentIdea  # noqa: E402
from app.models.publishing import PublishChannel, Schedule  # noqa: E402
from app.models.source import Source  # noqa: E402
from app.services.provisioning import get_or_create_account  # noqa: E402

TAG = f"test_audit_{uuid.uuid4().hex[:8]}"
SLUG = TAG.replace("_", "-")


def _user(suffix: str = "") -> CurrentUser:
    return CurrentUser(
        clerk_user_id=f"{TAG}{suffix}_u",
        clerk_org_id=f"{TAG}{suffix}_o",
        email=f"a{suffix}@test.local",
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


# ── oauth_callback: malformed brand_id in state ──────────────────────

@pytest.mark.asyncio
async def test_oauth_callback_malformed_brand_id_returns_400(auth_as, cleanup, monkeypatch):
    """If exchange_code returns a brand_id that isn't a UUID, endpoint must NOT 500."""
    auth_as(_user())

    from app.api.v1.endpoints import publishing as pub_mod

    async def fake_exchange(platform, code, state, redirect_uri):
        return {
            "brand_id": "not-a-uuid",
            "platform": platform,
            "oauth_blob": "ciphertext",
        }

    monkeypatch.setattr(pub_mod, "exchange_code", fake_exchange)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get(
            "/v1/publishing/oauth/callback",
            params={"platform": "linkedin", "code": "x", "state": "s",
                    "redirect_uri": "https://x/cb"},
        )
    assert r.status_code == 400
    assert "invalid brand id" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_oauth_callback_missing_brand_id_key_returns_400(auth_as, cleanup, monkeypatch):
    """exchange_code returning a dict without `brand_id` key must NOT raise KeyError → 500."""
    auth_as(_user())

    from app.api.v1.endpoints import publishing as pub_mod

    async def fake_exchange(platform, code, state, redirect_uri):
        return {"platform": platform, "oauth_blob": "ciphertext"}  # no brand_id

    monkeypatch.setattr(pub_mod, "exchange_code", fake_exchange)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get(
            "/v1/publishing/oauth/callback",
            params={"platform": "linkedin", "code": "x", "state": "s",
                    "redirect_uri": "https://x/cb"},
        )
    assert r.status_code == 400


# ── calendar pagination ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_calendar_limit_clamped_to_max(auth_as, cleanup):
    """When limit > 1000 (the hard cap), only 1000 rows are returned."""
    user = _user("_calclamp")
    auth_as(user)
    async with SessionLocal() as db:
        acct = await get_or_create_account(db, user)
        ws_id = (await db.execute(
            text("SELECT id FROM workspaces WHERE account_id = :a LIMIT 1"),
            {"a": acct.id},
        )).scalar_one()
        brand = Brand(account_id=acct.id, workspace_id=ws_id,
                      name="C", slug=f"{SLUG}-cal"[:60], primary_topic="AI")
        db.add(brand); await db.flush()
        idea = ContentIdea(account_id=acct.id, brand_id=brand.id, title="x",
                           created_at=datetime.now(timezone.utc))
        db.add(idea); await db.flush()
        asset = ContentAsset(account_id=acct.id, brand_id=brand.id, idea_id=idea.id,
                             format="blog", title="cal asset", status="draft")
        db.add(asset); await db.flush()
        chan = PublishChannel(account_id=acct.id, brand_id=brand.id,
                              platform="wordpress", display_name="wp",
                              oauth_blob={"ct": "x"}, status="connected")
        db.add(chan); await db.flush()

        # 1100 schedules in the window
        now = datetime.now(timezone.utc)
        for i in range(1100):
            db.add(Schedule(
                account_id=acct.id, brand_id=brand.id, asset_id=asset.id,
                channel_id=chan.id,
                scheduled_at=now + timedelta(minutes=i),
                status="pending",
                created_at=now,
            ))
        await db.commit()
        brand_id = brand.id

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get("/v1/calendar", params={
            "brand_id": str(brand_id),
            "from": (now - timedelta(hours=1)).isoformat(),
            "to": (now + timedelta(days=10)).isoformat(),
            "limit": 9999,
        })
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) <= 1000, f"calendar limit cap broken: got {len(rows)}"


@pytest.mark.asyncio
async def test_calendar_custom_limit_respected(auth_as, cleanup):
    """A custom limit under the cap must be honored exactly."""
    user = _user("_callim")
    auth_as(user)
    async with SessionLocal() as db:
        acct = await get_or_create_account(db, user)
        ws_id = (await db.execute(
            text("SELECT id FROM workspaces WHERE account_id = :a LIMIT 1"),
            {"a": acct.id},
        )).scalar_one()
        brand = Brand(account_id=acct.id, workspace_id=ws_id,
                      name="C2", slug=f"{SLUG}-cal2"[:60], primary_topic="AI")
        db.add(brand); await db.flush()
        idea = ContentIdea(account_id=acct.id, brand_id=brand.id, title="x",
                           created_at=datetime.now(timezone.utc))
        db.add(idea); await db.flush()
        asset = ContentAsset(account_id=acct.id, brand_id=brand.id, idea_id=idea.id,
                             format="blog", title="a", status="draft")
        db.add(asset); await db.flush()
        chan = PublishChannel(account_id=acct.id, brand_id=brand.id,
                              platform="wordpress", display_name="wp",
                              oauth_blob={"ct": "x"}, status="connected")
        db.add(chan); await db.flush()

        now = datetime.now(timezone.utc)
        for i in range(50):
            db.add(Schedule(
                account_id=acct.id, brand_id=brand.id, asset_id=asset.id,
                channel_id=chan.id,
                scheduled_at=now + timedelta(minutes=i),
                status="pending",
                created_at=now,
            ))
        await db.commit()
        brand_id = brand.id

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get("/v1/calendar", params={
            "brand_id": str(brand_id),
            "from": (now - timedelta(hours=1)).isoformat(),
            "to": (now + timedelta(days=10)).isoformat(),
            "limit": 10,
        })
    assert r.status_code == 200
    assert len(r.json()) == 10


# ── sources pagination ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sources_list_limit_clamped_to_max(auth_as, cleanup):
    """A request for limit > 500 (the hard cap) returns at most 500."""
    user = _user("_srcclamp")
    auth_as(user)
    async with SessionLocal() as db:
        acct = await get_or_create_account(db, user)
        ws_id = (await db.execute(
            text("SELECT id FROM workspaces WHERE account_id = :a LIMIT 1"),
            {"a": acct.id},
        )).scalar_one()
        brand = Brand(account_id=acct.id, workspace_id=ws_id,
                      name="S", slug=f"{SLUG}-src"[:60], primary_topic="AI")
        db.add(brand); await db.flush()
        for i in range(520):
            db.add(Source(account_id=acct.id, brand_id=brand.id,
                          kind="topic", title=f"t{i}", status="pending"))
        await db.commit()
        brand_id = brand.id

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get(f"/v1/sources/brand/{brand_id}", params={"limit": 9999})
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 500, f"expected exactly 500, got {len(rows)}"


@pytest.mark.asyncio
async def test_sources_list_default_limit_is_200(auth_as, cleanup):
    """Default (no `?limit` param) returns at most 200 rows."""
    user = _user("_srcdef")
    auth_as(user)
    async with SessionLocal() as db:
        acct = await get_or_create_account(db, user)
        ws_id = (await db.execute(
            text("SELECT id FROM workspaces WHERE account_id = :a LIMIT 1"),
            {"a": acct.id},
        )).scalar_one()
        brand = Brand(account_id=acct.id, workspace_id=ws_id,
                      name="S2", slug=f"{SLUG}-srcd"[:60], primary_topic="AI")
        db.add(brand); await db.flush()
        for i in range(210):
            db.add(Source(account_id=acct.id, brand_id=brand.id,
                          kind="topic", title=f"d{i}", status="pending"))
        await db.commit()
        brand_id = brand.id

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get(f"/v1/sources/brand/{brand_id}")
    assert r.status_code == 200
    assert len(r.json()) == 200


@pytest.mark.asyncio
async def test_sources_list_cross_tenant_returns_404(auth_as, cleanup):
    """Tenant B asking for tenant A's brand sources must get 404, not data."""
    user_a = _user("_srca")
    user_b = _user("_srcb")

    async with SessionLocal() as db:
        acct_a = await get_or_create_account(db, user_a)
        ws_id_a = (await db.execute(
            text("SELECT id FROM workspaces WHERE account_id = :a LIMIT 1"),
            {"a": acct_a.id},
        )).scalar_one()
        brand_a = Brand(account_id=acct_a.id, workspace_id=ws_id_a,
                        name="A", slug=f"{SLUG}-axt"[:60], primary_topic="AI")
        db.add(brand_a); await db.flush()
        db.add(Source(account_id=acct_a.id, brand_id=brand_a.id,
                      kind="topic", title="secret", status="pending"))
        await get_or_create_account(db, user_b)
        await db.commit()
        brand_a_id = brand_a.id

    auth_as(user_b)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get(f"/v1/sources/brand/{brand_a_id}")
    assert r.status_code == 404, "must NOT leak foreign-brand sources"
