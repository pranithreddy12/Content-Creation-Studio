"""Cross-tenant scoping for brand creation's workspace_id.

Production bug #14: POST /v1/brands accepted a `workspace_id` from the payload
and used it verbatim — a user could plant a brand inside another tenant's
workspace, leaking the brand into the victim's dashboard and (via workspace
joins) potentially their analytics scope.

Fix: brand_service.create() validates any supplied workspace_id belongs to the
caller's account, else 404.
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
from app.models.account import Workspace  # noqa: E402
from app.models.brand import Brand  # noqa: E402
from app.services.provisioning import get_or_create_account  # noqa: E402

TAG = f"test_bws_{uuid.uuid4().hex[:8]}"
SLUG = TAG.replace("_", "-")


def _user(suffix: str = "") -> CurrentUser:
    return CurrentUser(
        clerk_user_id=f"{TAG}{suffix}_u",
        clerk_org_id=f"{TAG}{suffix}_o",
        email=f"w{suffix}@test.local",
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
async def two_tenants():
    """Provision tenant A and tenant B, returning their accounts + workspace ids."""
    user_a = _user("_A")
    user_b = _user("_B")
    async with SessionLocal() as db:
        acct_a = await get_or_create_account(db, user_a)
        acct_b = await get_or_create_account(db, user_b)
        ws_a = (await db.execute(
            select(Workspace.id).where(Workspace.account_id == acct_a.id)
        )).scalar_one()
        ws_b = (await db.execute(
            select(Workspace.id).where(Workspace.account_id == acct_b.id)
        )).scalar_one()
        yield {"user_a": user_a, "user_b": user_b,
               "acct_a": acct_a.id, "acct_b": acct_b.id,
               "ws_a": ws_a, "ws_b": ws_b}


@pytest.mark.asyncio
async def test_create_brand_with_own_workspace_succeeds(auth_as, two_tenants, cleanup):
    auth_as(two_tenants["user_a"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.post("/v1/brands", json={
            "name": "Mine",
            "slug": f"{SLUG}-own"[:60],
            "workspace_id": str(two_tenants["ws_a"]),
        })
    assert r.status_code == 201, r.text
    assert r.json()["workspace_id"] == str(two_tenants["ws_a"])


@pytest.mark.asyncio
async def test_create_brand_with_foreign_workspace_is_404(auth_as, two_tenants, cleanup):
    """Tenant B sending tenant A's workspace_id must NOT create the brand."""
    auth_as(two_tenants["user_b"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.post("/v1/brands", json={
            "name": "Intruder",
            "slug": f"{SLUG}-intr"[:60],
            "workspace_id": str(two_tenants["ws_a"]),  # A's workspace!
        })
    assert r.status_code == 404, "must reject foreign workspace_id"

    # Confirm no brand landed in tenant A's workspace
    async with SessionLocal() as db:
        count = (await db.execute(
            select(text("count(*)")).select_from(Brand)
            .where(Brand.workspace_id == two_tenants["ws_a"])
        )).scalar()
        assert count == 0, "foreign brand must not be planted in victim workspace"


@pytest.mark.asyncio
async def test_create_brand_with_unknown_workspace_is_404(auth_as, two_tenants, cleanup):
    """A wholly nonexistent workspace_id is also a 404, not a 500."""
    auth_as(two_tenants["user_a"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.post("/v1/brands", json={
            "name": "Ghost",
            "slug": f"{SLUG}-ghost"[:60],
            "workspace_id": str(uuid.uuid4()),
        })
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_brand_without_workspace_uses_default(auth_as, two_tenants, cleanup):
    """Omitting workspace_id falls back to the caller's default workspace."""
    auth_as(two_tenants["user_b"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.post("/v1/brands", json={
            "name": "Defaulted",
            "slug": f"{SLUG}-def"[:60],
        })
    assert r.status_code == 201
    assert r.json()["workspace_id"] == str(two_tenants["ws_b"])
