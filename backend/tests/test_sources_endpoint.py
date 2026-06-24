"""Sources endpoint coverage: upload-intent + POST /sources + tenant isolation."""
from __future__ import annotations

import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

os.environ.setdefault("SECRET_KEY", "testtesttesttesttesttesttesttest")
os.environ.setdefault("S3_BUCKET", "studio-media")

from app.api.deps.auth import CurrentUser, current_user  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models.brand import Brand  # noqa: E402
from app.models.source import Source  # noqa: E402
from app.services.provisioning import get_or_create_account  # noqa: E402

TAG = f"test_src_{uuid.uuid4().hex[:8]}"
SLUG = TAG.replace("_", "-")


def _user(suffix: str = "") -> CurrentUser:
    return CurrentUser(
        clerk_user_id=f"{TAG}{suffix}_u",
        clerk_org_id=f"{TAG}{suffix}_o",
        email=f"s{suffix}@test.local",
        role="owner", raw={},
    )


@pytest.fixture()
def auth_as():
    def _set(u: CurrentUser):
        app.dependency_overrides[current_user] = lambda: u
    yield _set
    app.dependency_overrides.pop(current_user, None)


@pytest.fixture()
async def seeded_brand():
    """Yields a brand id owned by the default _user()."""
    user = _user()
    async with SessionLocal() as db:
        acct = await get_or_create_account(db, user)
        ws_id = (await db.execute(
            text("SELECT id FROM workspaces WHERE account_id = :a LIMIT 1"),
            {"a": acct.id},
        )).scalar_one()
        brand = Brand(
            account_id=acct.id, workspace_id=ws_id,
            name="SrcBrand", slug=f"{SLUG}-sb"[:60],
        )
        db.add(brand)
        await db.commit()
        bid = brand.id
    yield bid
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM accounts WHERE clerk_org_id LIKE :p"), {"p": f"{TAG}%"})
        await db.commit()


@pytest.fixture()
def stub_ingest_task(monkeypatch):
    """Prevent the real Celery task from running while tests are exercising POST /sources."""
    calls = []

    def _delay(source_id):
        calls.append(source_id)
        class _R:
            id = "fake-ingest-task"
        return _R()

    from app.api.v1.endpoints import sources as ep
    monkeypatch.setattr(ep.ingest_source_task, "delay", _delay)
    yield calls


# ── Upload intent ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_upload_intent_returns_presigned_pdf_url(auth_as, seeded_brand):
    auth_as(_user())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.post("/v1/sources/upload-intent", json={
            "brand_id": str(seeded_brand),
            "kind": "pdf",
            "filename": "my doc.pdf",
            "content_type": "application/pdf",
        })
    assert r.status_code == 200
    body = r.json()
    assert body["expires_in"] == 3600
    assert body["storage_key"].startswith(f"sources/{seeded_brand}/")
    assert body["storage_key"].endswith("-my doc.pdf")
    assert body["upload_url"].startswith("http"), "presigned URL must be a real http(s) link"
    # Presigned URL must include the AWS signature query params
    assert "X-Amz-Signature" in body["upload_url"] or "X-Amz-Credential" in body["upload_url"]


@pytest.mark.asyncio
async def test_upload_intent_returns_presigned_voice_url(auth_as, seeded_brand):
    auth_as(_user())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.post("/v1/sources/upload-intent", json={
            "brand_id": str(seeded_brand),
            "kind": "voice",
            "filename": "memo.m4a",
            "content_type": "audio/mp4",
        })
    assert r.status_code == 200
    body = r.json()
    assert body["storage_key"].endswith("-memo.m4a")


@pytest.mark.asyncio
async def test_upload_intent_rejects_url_kind(auth_as, seeded_brand):
    """Schema only allows pdf|voice; other kinds → 422."""
    auth_as(_user())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.post("/v1/sources/upload-intent", json={
            "brand_id": str(seeded_brand),
            "kind": "url",       # not allowed for uploads
            "filename": "x",
            "content_type": "text/html",
        })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_upload_intent_unique_per_call(auth_as, seeded_brand):
    """Two calls for the same filename produce distinct storage_keys (UUID prefix)."""
    auth_as(_user())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r1 = (await cx.post("/v1/sources/upload-intent", json={
            "brand_id": str(seeded_brand), "kind": "pdf",
            "filename": "report.pdf", "content_type": "application/pdf",
        })).json()
        r2 = (await cx.post("/v1/sources/upload-intent", json={
            "brand_id": str(seeded_brand), "kind": "pdf",
            "filename": "report.pdf", "content_type": "application/pdf",
        })).json()
    assert r1["storage_key"] != r2["storage_key"]


# ── POST /v1/sources ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_topic_source_dispatches_ingest_task(auth_as, seeded_brand, stub_ingest_task):
    auth_as(_user())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.post("/v1/sources", json={
            "brand_id": str(seeded_brand),
            "kind": "topic",
            "raw_text": "AI agents in 2026",
        })
    assert r.status_code == 201
    body = r.json()
    assert body["kind"] == "topic"
    assert body["status"] == "pending"
    # Celery task got the source_id
    assert len(stub_ingest_task) == 1
    assert stub_ingest_task[0] == body["id"]


@pytest.mark.asyncio
async def test_create_source_blocks_ssrf_url(auth_as, seeded_brand, stub_ingest_task):
    """URL kind sources go through validate_external_url → loopback blocked."""
    auth_as(_user())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.post("/v1/sources", json={
            "brand_id": str(seeded_brand),
            "kind": "url",
            "url": "http://127.0.0.1:8000/admin",
        })
    assert r.status_code == 400
    assert "unsafe url" in r.json()["detail"]
    # No Celery dispatch
    assert stub_ingest_task == []


@pytest.mark.asyncio
async def test_create_source_for_foreign_brand_404(auth_as, seeded_brand, stub_ingest_task):
    """User B can't attach a source to User A's brand."""
    user_b = _user("_B")
    auth_as(user_b)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.post("/v1/sources", json={
            "brand_id": str(seeded_brand),
            "kind": "topic", "raw_text": "hijack",
        })
    assert r.status_code == 404
    # No task dispatched
    assert stub_ingest_task == []


@pytest.mark.asyncio
async def test_list_sources_by_brand_excludes_other_tenants(auth_as, seeded_brand, stub_ingest_task):
    user_a = _user()
    user_b = _user("_B")
    auth_as(user_a)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        await cx.post("/v1/sources", json={
            "brand_id": str(seeded_brand),
            "kind": "topic", "raw_text": "Alpha note",
        })

    # User B asks for the list of brand_a's sources → 404 (brand not theirs)
    auth_as(user_b)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get(f"/v1/sources/brand/{seeded_brand}")
    assert r.status_code == 404
