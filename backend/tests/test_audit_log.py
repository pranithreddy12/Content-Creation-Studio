"""Audit log writer tests."""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select, text

os.environ.setdefault("SECRET_KEY", "testtesttesttesttesttesttesttest")

from app.db.session import SessionLocal  # noqa: E402
from app.models.audit import AuditLog  # noqa: E402
from app.services.audit import record  # noqa: E402

TAG = f"test_audit_{uuid.uuid4().hex[:8]}"


@pytest.fixture()
async def cleanup():
    yield
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM audit_log WHERE action LIKE :p"), {"p": f"{TAG}%"})
        await db.commit()


@pytest.mark.asyncio
async def test_record_writes_minimal_row(cleanup):
    async with SessionLocal() as db:
        await record(db, action=f"{TAG}_minimal")

        row = (await db.execute(
            select(AuditLog).where(AuditLog.action == f"{TAG}_minimal")
        )).scalar_one()
        assert row.target is None
        assert row.account_id is None
        assert row.user_id is None
        assert row.brand_id is None
        assert row.data == {}
        assert row.ip is None
        assert row.ua is None
        assert isinstance(row.occurred_at, datetime)


@pytest.mark.asyncio
async def test_record_persists_all_fields(cleanup):
    acct_id = uuid.uuid4()
    user_id = uuid.uuid4()
    brand_id = uuid.uuid4()

    async with SessionLocal() as db:
        await record(db,
            action=f"{TAG}_publish",
            target="content_assets/abc",
            account_id=acct_id,
            user_id=user_id,
            brand_id=brand_id,
            data={"platform": "linkedin", "external_id": "li_123"},
            ip="203.0.113.42",
            ua="StudioBot/1.0",
        )

        row = (await db.execute(
            select(AuditLog).where(AuditLog.action == f"{TAG}_publish")
        )).scalar_one()
        assert row.target == "content_assets/abc"
        assert row.account_id == acct_id
        assert row.user_id == user_id
        assert row.brand_id == brand_id
        assert row.data == {"platform": "linkedin", "external_id": "li_123"}
        assert str(row.ip) == "203.0.113.42"
        assert row.ua == "StudioBot/1.0"


@pytest.mark.asyncio
async def test_record_is_append_only_within_a_session(cleanup):
    """Multiple record() calls in the same session create distinct rows ordered by occurred_at."""
    async with SessionLocal() as db:
        await record(db, action=f"{TAG}_one")
        await record(db, action=f"{TAG}_two")
        await record(db, action=f"{TAG}_three")

        rows = (await db.execute(
            select(AuditLog).where(AuditLog.action.like(f"{TAG}_%"))
            .order_by(AuditLog.occurred_at.asc())
        )).scalars().all()
        assert [r.action for r in rows] == [f"{TAG}_one", f"{TAG}_two", f"{TAG}_three"]


@pytest.mark.asyncio
async def test_record_occurred_at_is_utc(cleanup):
    """The persisted occurred_at must carry UTC tzinfo (not a naive timestamp)."""
    before = datetime.now(timezone.utc)
    async with SessionLocal() as db:
        await record(db, action=f"{TAG}_utc")
        row = (await db.execute(
            select(AuditLog).where(AuditLog.action == f"{TAG}_utc")
        )).scalar_one()
    after = datetime.now(timezone.utc)
    assert row.occurred_at.tzinfo is not None
    assert before <= row.occurred_at <= after
