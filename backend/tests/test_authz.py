"""Cross-tenant authorization tests.

These exercise the service layer directly with two synthetic Clerk users
(orgA and orgB). Each user's brand_service / source_service calls must
NEVER surface another tenant's rows.

The tests insert fixtures with a `TEST_PREFIX` so a teardown can sweep
them, and roll all writes back through an explicit DELETE in finally.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text

# Point at the live container DB; this file runs inside the backend container.
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://studio:studio@postgres:5432/studio"
)
os.environ.setdefault("REDIS_URL", "redis://redis:6379/0")
os.environ.setdefault("SECRET_KEY", "testtesttesttesttesttesttesttest")

from app.api.deps.auth import CurrentUser  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.account import Account, Workspace  # noqa: E402
from app.models.brand import Brand  # noqa: E402
from app.models.source import Source  # noqa: E402
from app.schemas.brand import BrandCreate, BrandUpdate  # noqa: E402
from app.schemas.source import SourceCreate  # noqa: E402
from app.services import brand_service, source_service  # noqa: E402

TEST_PREFIX = f"test_authz_{uuid.uuid4().hex[:8]}_"
SLUG_PREFIX = TEST_PREFIX.replace("_", "-")


def _user(sub: str, org: str | None = None) -> CurrentUser:
    return CurrentUser(
        clerk_user_id=f"{TEST_PREFIX}{sub}",
        clerk_org_id=f"{TEST_PREFIX}{org}" if org else None,
        email=f"{sub}@test.local",
        role="owner",
        raw={},
    )


@pytest_asyncio.fixture()
async def tenants():
    """Provision two independent Clerk-org tenants A and B with one brand each."""
    user_a = _user("a", org="A")
    user_b = _user("b", org="B")

    async with SessionLocal() as db:
        brand_a = await brand_service.create(db, user_a, BrandCreate(
            name="A Brand", slug=f"{SLUG_PREFIX}a"[:60], description="x",
        ))
        brand_b = await brand_service.create(db, user_b, BrandCreate(
            name="B Brand", slug=f"{SLUG_PREFIX}b"[:60], description="y",
        ))

    yield {"user_a": user_a, "user_b": user_b, "brand_a_id": brand_a.id, "brand_b_id": brand_b.id}

    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM accounts WHERE clerk_org_id LIKE :p"),
                         {"p": f"{TEST_PREFIX}%"})
        await db.commit()


@pytest.mark.asyncio
async def test_user_a_cannot_get_brand_b(tenants):
    async with SessionLocal() as db:
        result = await brand_service.get(db, tenants["user_a"], tenants["brand_b_id"])
    assert result is None


@pytest.mark.asyncio
async def test_user_a_cannot_update_brand_b(tenants):
    async with SessionLocal() as db:
        with pytest.raises(HTTPException) as exc:
            await brand_service.update(db, tenants["user_a"], tenants["brand_b_id"],
                                       BrandUpdate(name="hijacked"))
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_user_a_cannot_delete_brand_b(tenants):
    async with SessionLocal() as db:
        await brand_service.soft_delete(db, tenants["user_a"], tenants["brand_b_id"])
        b = await brand_service.get(db, tenants["user_b"], tenants["brand_b_id"])
    assert b is not None
    assert b.status == "active", "soft_delete must NOT have flipped status across tenants"


@pytest.mark.asyncio
async def test_user_a_brand_list_excludes_b(tenants):
    async with SessionLocal() as db:
        listed = await brand_service.list_for_user(db, tenants["user_a"])
    ids = {str(b.id) for b in listed}
    assert str(tenants["brand_a_id"]) in ids
    assert str(tenants["brand_b_id"]) not in ids, "tenant leak in list_for_user"


@pytest.mark.asyncio
async def test_user_a_cannot_attach_source_to_brand_b(tenants):
    async with SessionLocal() as db:
        with pytest.raises(HTTPException) as exc:
            await source_service.create_source(db, tenants["user_a"], SourceCreate(
                brand_id=tenants["brand_b_id"], kind="topic", raw_text="injected",
            ))
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_user_a_source_list_for_brand_b_404s(tenants):
    async with SessionLocal() as db:
        with pytest.raises(HTTPException) as exc:
            await source_service.list_for_brand(db, tenants["user_a"], tenants["brand_b_id"])
    assert exc.value.status_code == 404
