"""Backend → frontend response shape contracts.

The frontend declares TypeScript interfaces in `frontend/types/api.ts` for the
shapes the dashboard reads. This file holds the inverse spec on the backend
side: for every type, we exercise the corresponding endpoint and assert the
response dict has the required (non-optional) keys with the expected Python
types.

Add a new entry here whenever the frontend types or backend response shape
changes — that's the canary that protects against silent contract drift.

Auth is bypassed via FastAPI dependency_overrides[current_user] so we can
exercise the live router without a Clerk JWT.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

os.environ.setdefault("SECRET_KEY", "testtesttesttesttesttesttesttest")

from app.api.deps.auth import CurrentUser, current_user  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models.account import Account, Workspace  # noqa: E402
from app.models.brand import Brand  # noqa: E402
from app.models.content import ContentAsset, ContentIdea  # noqa: E402
from app.models.publishing import PublishChannel, Schedule  # noqa: E402
from app.models.source import Source  # noqa: E402
from app.core.security import encrypt  # noqa: E402

TAG = f"test_contract_{uuid.uuid4().hex[:8]}"
SLUG_TAG = TAG.replace("_", "-")


def _user() -> CurrentUser:
    return CurrentUser(
        clerk_user_id=f"{TAG}_u",
        clerk_org_id=f"{TAG}_o",
        email="c@test.local",
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
async def seeded():
    """Provision Account → Workspace → Brand → Source → Idea → ContentAsset → Channel → Schedule."""
    user = _user()
    from app.services.provisioning import get_or_create_account
    async with SessionLocal() as db:
        acct = await get_or_create_account(db, user)
        ws_row = (await db.execute(
            text("SELECT id FROM workspaces WHERE account_id = :a LIMIT 1"),
            {"a": acct.id},
        )).first()
        ws_id = ws_row[0]
        brand = Brand(
            account_id=acct.id, workspace_id=ws_id,
            name="ContractBrand", slug=f"{SLUG_TAG}-cb"[:60],
            description="x", primary_topic="AI", audience="founders",
            tone="professional", daily_quota=2, timezone="UTC",
            publish_window={"start": "09:00", "end": "18:00"},
        )
        db.add(brand); await db.flush()

        source = Source(
            account_id=acct.id, brand_id=brand.id,
            kind="topic", title="seed", raw_text="hi",
            status="embedded",
        )
        db.add(source); await db.flush()

        idea = ContentIdea(
            account_id=acct.id, brand_id=brand.id,
            title="Test Idea", created_at=datetime.now(timezone.utc),
        )
        db.add(idea); await db.flush()

        asset = ContentAsset(
            account_id=acct.id, brand_id=brand.id, idea_id=idea.id,
            format="blog", title="A Blog", body="body",
            seo={"title": "A Blog"}, status="draft",
        )
        db.add(asset); await db.flush()

        channel = PublishChannel(
            account_id=acct.id, brand_id=brand.id,
            platform="wordpress", display_name="wp@brand",
            oauth_blob={"ct": encrypt('{"site":"http://x","username":"u","app_password":"p"}')},
            status="connected", meta={},
        )
        db.add(channel); await db.flush()

        schedule = Schedule(
            account_id=acct.id, brand_id=brand.id,
            asset_id=asset.id, channel_id=channel.id,
            scheduled_at=datetime(2026, 12, 1, 9, 0, tzinfo=timezone.utc),
            status="pending", external_url=None,
            created_at=datetime.now(timezone.utc),
        )
        db.add(schedule); await db.commit()
        ctx = {"brand_id": brand.id, "source_id": source.id, "asset_id": asset.id,
               "channel_id": channel.id}

    yield ctx

    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM accounts WHERE clerk_org_id = :o"), {"o": f"{TAG}_o"})
        await db.commit()


# ─── Shape helpers ────────────────────────────────────────────────────────

def _assert_shape(row: dict, required: dict[str, type | tuple[type, ...]], optional: dict[str, type | tuple[type, ...]] = {}) -> None:
    for key, kind in required.items():
        assert key in row, f"missing required key '{key}' in {row}"
        if row[key] is not None:
            assert isinstance(row[key], kind), (
                f"key '{key}' expected {kind}, got {type(row[key]).__name__}: {row[key]!r}"
            )
    for key, kind in optional.items():
        if key in row and row[key] is not None:
            assert isinstance(row[key], kind), (
                f"optional key '{key}' expected {kind}, got {type(row[key]).__name__}"
            )


# ─── Brand contract ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_brand_shape_matches_frontend_type(seeded, auth_as_user):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get("/v1/brands")
    assert r.status_code == 200
    brands = r.json()
    assert len(brands) >= 1
    b = brands[0]
    _assert_shape(b,
        required={
            "id": str, "account_id": str, "workspace_id": str,
            "name": str, "slug": str,
            "competitor_urls": list, "daily_quota": int,
            "timezone": str, "publish_window": dict, "status": str,
            "created_at": str, "updated_at": str,
        },
        optional={
            "description": str, "website_url": str, "product_url": str,
            "primary_topic": str, "audience": str, "tone": str,
        },
    )
    assert {"start", "end"} <= set(b["publish_window"].keys())


# ─── Source contract ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_source_shape_matches_frontend_type(seeded, auth_as_user):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get(f"/v1/sources/brand/{seeded['brand_id']}")
    assert r.status_code == 200
    items = r.json()
    assert len(items) >= 1
    s = items[0]
    _assert_shape(s,
        required={
            "id": str, "brand_id": str, "kind": str,
            "status": str, "meta": dict,
            "created_at": str, "updated_at": str,
        },
        optional={"title": str, "url": str, "storage_key": str, "error": str},
    )


# ─── ContentAsset contract ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_content_asset_shape_matches_frontend_type(seeded, auth_as_user):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get(f"/v1/assets?brand_id={seeded['brand_id']}")
    assert r.status_code == 200
    items = r.json()
    assert len(items) >= 1
    a = items[0]
    _assert_shape(a,
        required={
            "id": str, "brand_id": str, "idea_id": str,
            "format": str, "status": str, "seo": dict,
            "created_at": str, "updated_at": str,
        },
        optional={"title": str, "body": str, "word_count": int},
    )
    assert a["status"] in {"draft", "review", "approved", "scheduled", "published", "failed"}


# ─── Channel contract ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_channel_shape_matches_frontend_type(seeded, auth_as_user):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get(f"/v1/publishing/channels/{seeded['brand_id']}")
    assert r.status_code == 200
    items = r.json()
    assert len(items) >= 1
    c = items[0]
    _assert_shape(c,
        required={
            "id": str, "platform": str, "display_name": str,
            "status": str, "meta": dict,
        },
    )


# ─── Schedule (Calendar) contract ───────────────────────────────────────

@pytest.mark.asyncio
async def test_schedule_shape_matches_frontend_type(seeded, auth_as_user):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get(
            f"/v1/calendar?brand_id={seeded['brand_id']}"
            "&from=2026-01-01T00:00:00Z&to=2026-12-31T00:00:00Z"
        )
    assert r.status_code == 200
    items = r.json()
    assert len(items) >= 1
    s = items[0]
    _assert_shape(s,
        required={
            "id": str, "brand_id": str, "asset_id": str, "channel_id": str,
            "scheduled_at": str, "status": str,
        },
        optional={"external_url": str, "format": str, "title": str},
    )


# ─── Analytics overview + timeseries ────────────────────────────────────

@pytest.mark.asyncio
async def test_overview_shape(seeded, auth_as_user):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get(f"/v1/analytics/overview?brand_id={seeded['brand_id']}")
    assert r.status_code == 200
    o = r.json()
    _assert_shape(o,
        required={
            "generated": int, "scheduled": int, "published": int,
            "avg_viral_score": (int, float),
            "avg_seo_score": (int, float),
            "revenue_attributed": (int, float),
        },
    )


@pytest.mark.asyncio
async def test_timeseries_shape_empty_or_metric_row(seeded, auth_as_user):
    """When no metrics exist the array is empty; if a row is returned, validate against AssetMetricRow."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get(f"/v1/analytics/timeseries?brand_id={seeded['brand_id']}&window=30d")
    assert r.status_code == 200
    items = r.json()
    assert isinstance(items, list)
    for row in items:
        _assert_shape(row,
            required={
                "platform": str, "views": int, "clicks": int, "shares": int,
                "likes": int, "comments": int,
            },
            optional={"ctr": (int, float), "collected_at": str},
        )
