"""Brand CRUD edge cases beyond the basic happy-path.

  * PATCH partial update — only the supplied fields change
  * Soft-delete — status flips to "archived" without removing the row
  * Slug uniqueness per account (UNIQUE constraint on (account_id, slug))
  * Two different accounts CAN reuse the same slug (no global collision)
  * GET / UPDATE / DELETE for a brand the caller doesn't own → 404
"""
from __future__ import annotations

import os
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

os.environ.setdefault("SECRET_KEY", "testtesttesttesttesttesttesttest")

from app.api.deps.auth import CurrentUser  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.brand import Brand  # noqa: E402
from app.schemas.brand import BrandCreate, BrandUpdate  # noqa: E402
from app.services import brand_service  # noqa: E402

TAG = f"test_bcrud_{uuid.uuid4().hex[:8]}"
SLUG = TAG.replace("_", "-")


def _user(suffix: str = "") -> CurrentUser:
    return CurrentUser(
        clerk_user_id=f"{TAG}{suffix}_u",
        clerk_org_id=f"{TAG}{suffix}_o",
        email=f"u{suffix}@test.local",
        role="owner",
        raw={},
    )


@pytest.fixture()
async def cleanup():
    yield
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM accounts WHERE clerk_org_id LIKE :p"), {"p": f"{TAG}%"})
        await db.commit()


@pytest.mark.asyncio
async def test_patch_only_updates_supplied_fields(cleanup):
    user = _user()
    async with SessionLocal() as db:
        b = await brand_service.create(db, user, BrandCreate(
            name="Original", slug=f"{SLUG}-orig"[:60],
            description="initial", primary_topic="X",
            tone="professional", daily_quota=3, timezone="UTC",
        ))
        bid = b.id

    async with SessionLocal() as db:
        updated = await brand_service.update(
            db, user, bid,
            BrandUpdate(description="updated-desc", daily_quota=10),
        )
    assert updated.description == "updated-desc"
    assert updated.daily_quota == 10
    # Fields not touched must keep their old values
    assert updated.name == "Original"
    assert updated.tone == "professional"
    assert updated.primary_topic == "X"


@pytest.mark.asyncio
async def test_soft_delete_flips_status(cleanup):
    user = _user()
    async with SessionLocal() as db:
        b = await brand_service.create(db, user, BrandCreate(
            name="Doomed", slug=f"{SLUG}-doom"[:60],
        ))
        bid = b.id

    async with SessionLocal() as db:
        await brand_service.soft_delete(db, user, bid)

    # Row still exists, status="archived"
    async with SessionLocal() as db:
        row = (await db.execute(select(Brand).where(Brand.id == bid))).scalar_one()
        assert row.status == "archived"


@pytest.mark.asyncio
async def test_slug_uniqueness_within_account(cleanup):
    """Two brands in the SAME account with the same slug must violate UNIQUE constraint."""
    user = _user()
    same_slug = f"{SLUG}-dupe"[:60]
    async with SessionLocal() as db:
        await brand_service.create(db, user, BrandCreate(name="A", slug=same_slug))

    async with SessionLocal() as db:
        with pytest.raises(IntegrityError):
            await brand_service.create(db, user, BrandCreate(name="A2", slug=same_slug))


@pytest.mark.asyncio
async def test_slug_can_be_reused_across_accounts(cleanup):
    """Slug uniqueness is scoped per-account, NOT global."""
    same_slug = f"{SLUG}-shared"[:60]
    user_a = _user("_A")
    user_b = _user("_B")

    async with SessionLocal() as db:
        a = await brand_service.create(db, user_a, BrandCreate(name="A", slug=same_slug))
    async with SessionLocal() as db:
        b = await brand_service.create(db, user_b, BrandCreate(name="B", slug=same_slug))

    assert a.account_id != b.account_id
    assert a.slug == b.slug


@pytest.mark.asyncio
async def test_get_foreign_brand_returns_none(cleanup):
    """User A creates a brand. User B asking for it gets None (not the foreign row)."""
    user_a = _user("_X")
    user_b = _user("_Y")
    async with SessionLocal() as db:
        a = await brand_service.create(db, user_a, BrandCreate(name="X", slug=f"{SLUG}-x"[:60]))

    async with SessionLocal() as db:
        result = await brand_service.get(db, user_b, a.id)
    assert result is None


@pytest.mark.asyncio
async def test_update_foreign_brand_404(cleanup):
    """Foreign update raises HTTPException(404)."""
    user_a = _user("_P")
    user_b = _user("_Q")
    async with SessionLocal() as db:
        a = await brand_service.create(db, user_a, BrandCreate(name="P", slug=f"{SLUG}-p"[:60]))

    async with SessionLocal() as db:
        with pytest.raises(HTTPException) as exc:
            await brand_service.update(db, user_b, a.id, BrandUpdate(name="hijacked"))
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_soft_delete_foreign_brand_noops(cleanup):
    """Soft-deleting a foreign brand must silently no-op (don't leak existence)."""
    user_a = _user("_M")
    user_b = _user("_N")
    async with SessionLocal() as db:
        a = await brand_service.create(db, user_a, BrandCreate(name="M", slug=f"{SLUG}-m"[:60]))

    async with SessionLocal() as db:
        # Should not raise
        await brand_service.soft_delete(db, user_b, a.id)

    # Original brand must still be active.
    async with SessionLocal() as db:
        row = (await db.execute(select(Brand).where(Brand.id == a.id))).scalar_one()
        assert row.status == "active"
