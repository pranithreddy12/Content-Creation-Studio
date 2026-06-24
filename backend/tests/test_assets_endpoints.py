"""Full coverage of /v1/assets/* — list filters, lifecycle transitions, tenant isolation."""
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
from app.models.account import Account, Workspace  # noqa: E402
from app.models.brand import Brand  # noqa: E402
from app.models.content import ContentAsset, ContentIdea  # noqa: E402
from app.services.provisioning import get_or_create_account  # noqa: E402

TAG = f"test_assets_{uuid.uuid4().hex[:8]}"
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


async def _seed_brand_and_assets(user: CurrentUser, asset_specs: list[tuple[str, str, str]]) -> dict:
    """`asset_specs` is a list of (format, status, title)."""
    async with SessionLocal() as db:
        acct = await get_or_create_account(db, user)
        ws_id = (await db.execute(
            text("SELECT id FROM workspaces WHERE account_id = :a LIMIT 1"),
            {"a": acct.id},
        )).scalar_one()
        brand = Brand(
            account_id=acct.id, workspace_id=ws_id,
            name="Brand", slug=f"{SLUG}-{uuid.uuid4().hex[:6]}"[:60],
        )
        db.add(brand); await db.flush()
        idea = ContentIdea(
            account_id=acct.id, brand_id=brand.id, title="seed",
            created_at=datetime.now(timezone.utc),
        )
        db.add(idea); await db.flush()
        asset_ids = []
        for fmt, status, title in asset_specs:
            a = ContentAsset(
                account_id=acct.id, brand_id=brand.id, idea_id=idea.id,
                format=fmt, status=status, title=title, body="body",
            )
            db.add(a); await db.flush()
            asset_ids.append(a.id)
        await db.commit()
        return {"brand_id": brand.id, "asset_ids": asset_ids}


@pytest.fixture()
async def cleanup():
    yield
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM accounts WHERE clerk_org_id LIKE :p"), {"p": f"{TAG}%"})
        await db.commit()


# ── List + filters ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_returns_all_assets_when_unfiltered(auth_as, cleanup):
    user = _user()
    auth_as(user)
    ctx = await _seed_brand_and_assets(user, [
        ("blog", "draft", "B1"),
        ("linkedin", "review", "L1"),
        ("x_thread", "published", "X1"),
    ])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get("/v1/assets")
    assert r.status_code == 200
    titles = {a["title"] for a in r.json()}
    assert titles == {"B1", "L1", "X1"}


@pytest.mark.asyncio
async def test_list_filters_by_brand(auth_as, cleanup):
    user = _user()
    auth_as(user)
    ctx_a = await _seed_brand_and_assets(user, [("blog", "draft", "AlphaBlog")])
    ctx_b = await _seed_brand_and_assets(user, [("blog", "draft", "BetaBlog")])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get(f"/v1/assets?brand_id={ctx_a['brand_id']}")
    titles = {a["title"] for a in r.json()}
    assert titles == {"AlphaBlog"}


@pytest.mark.asyncio
async def test_list_filters_by_status(auth_as, cleanup):
    user = _user()
    auth_as(user)
    await _seed_brand_and_assets(user, [
        ("blog", "draft", "D1"), ("blog", "draft", "D2"),
        ("blog", "review", "R1"),
        ("blog", "published", "P1"),
    ])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get("/v1/assets?status=review")
    titles = {a["title"] for a in r.json()}
    assert titles == {"R1"}


@pytest.mark.asyncio
async def test_list_filters_by_format(auth_as, cleanup):
    user = _user()
    auth_as(user)
    await _seed_brand_and_assets(user, [
        ("blog", "draft", "B"), ("linkedin", "draft", "L"), ("x_thread", "draft", "X"),
    ])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get("/v1/assets?format=linkedin")
    titles = {a["title"] for a in r.json()}
    assert titles == {"L"}


@pytest.mark.asyncio
async def test_list_for_user_with_no_brands_returns_empty(auth_as, cleanup):
    """Brand-new user with zero brands should get [] (not 404)."""
    user = _user("_empty")
    auth_as(user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get("/v1/assets")
    assert r.status_code == 200
    assert r.json() == []


# ── GET / approve / reject / schedule ─────────────────────────────────

@pytest.mark.asyncio
async def test_get_asset_returns_full_shape(auth_as, cleanup):
    user = _user()
    auth_as(user)
    ctx = await _seed_brand_and_assets(user, [("blog", "review", "Detail")])
    aid = str(ctx["asset_ids"][0])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get(f"/v1/assets/{aid}")
    assert r.status_code == 200
    a = r.json()
    assert a["id"] == aid
    assert a["title"] == "Detail"
    assert a["format"] == "blog"
    assert a["status"] == "review"


@pytest.mark.asyncio
async def test_get_unknown_asset_returns_404(auth_as, cleanup):
    user = _user()
    auth_as(user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get(f"/v1/assets/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_approve_flips_status_and_records_approver(auth_as, cleanup):
    user = _user()
    auth_as(user)
    ctx = await _seed_brand_and_assets(user, [("blog", "review", "A1")])
    aid = str(ctx["asset_ids"][0])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.post(f"/v1/assets/{aid}/approve")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "approved"
    assert body["approval_state"]["approver"] == user.clerk_user_id
    assert "approved_at" in body["approval_state"]


@pytest.mark.asyncio
async def test_reject_flips_status_back_to_draft(auth_as, cleanup):
    user = _user()
    auth_as(user)
    ctx = await _seed_brand_and_assets(user, [("blog", "review", "R1")])
    aid = str(ctx["asset_ids"][0])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.post(f"/v1/assets/{aid}/reject")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "draft"
    assert body["approval_state"]["rejected_by"] == user.clerk_user_id


@pytest.mark.asyncio
async def test_schedule_flips_status(auth_as, cleanup):
    user = _user()
    auth_as(user)
    ctx = await _seed_brand_and_assets(user, [("blog", "approved", "S1")])
    aid = str(ctx["asset_ids"][0])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.post(f"/v1/assets/{aid}/schedule")
    assert r.status_code == 200
    assert r.json()["status"] == "scheduled"


# ── Cross-tenant ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cannot_get_foreign_asset(auth_as, cleanup):
    user_a = _user("_A")
    user_b = _user("_B")
    ctx_a = await _seed_brand_and_assets(user_a, [("blog", "draft", "Secret")])
    aid = str(ctx_a["asset_ids"][0])

    auth_as(user_b)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get(f"/v1/assets/{aid}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_cannot_approve_foreign_asset(auth_as, cleanup):
    user_a = _user("_X")
    user_b = _user("_Y")
    ctx_a = await _seed_brand_and_assets(user_a, [("blog", "review", "Forbidden")])
    aid = str(ctx_a["asset_ids"][0])

    auth_as(user_b)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.post(f"/v1/assets/{aid}/approve")
    assert r.status_code == 404

    # And verify the original asset is still in 'review'
    async with SessionLocal() as db:
        row = (await db.execute(select(ContentAsset).where(ContentAsset.id == uuid.UUID(aid)))).scalar_one()
        assert row.status == "review"


@pytest.mark.asyncio
async def test_list_assets_excludes_other_tenants(auth_as, cleanup):
    user_a = _user("_M")
    user_b = _user("_N")
    await _seed_brand_and_assets(user_a, [("blog", "draft", "ASecret")])
    await _seed_brand_and_assets(user_b, [("blog", "draft", "BPublic")])

    auth_as(user_b)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get("/v1/assets")
    titles = {a["title"] for a in r.json()}
    assert "ASecret" not in titles
    assert "BPublic" in titles
