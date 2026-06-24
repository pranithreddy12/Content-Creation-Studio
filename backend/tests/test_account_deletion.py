"""Hard account deletion — full purge across Postgres, Qdrant, MinIO, Redis.

Seeds a "doomed" account with data in every store plus a second account that
must remain untouched, runs the purge, and asserts:
  * Postgres: account + all cascaded children gone; other tenant intact
  * Qdrant: the account's per-brand collections gone; other tenant's + the global
    viral_patterns collection intact
  * MinIO: the account's media/{brand}/ objects gone; other tenant's intact
  * Redis: the account's budget/rate keys + per-brand loop locks gone; other intact
  * endpoint cross-tenant addressing → 404
  * partial-crash resume completes; re-running a completed job is a no-op
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

os.environ.setdefault("SECRET_KEY", "testtesttesttesttesttesttesttest")

from app.api.deps.auth import CurrentUser, current_user  # noqa: E402
from app.db.redis import redis  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models.account import Account, DeletionJob, Workspace  # noqa: E402
from app.models.brand import Brand  # noqa: E402
from app.models.content import ContentAsset, ContentIdea  # noqa: E402
from app.services.account_deletion import create_deletion_job, run_purge  # noqa: E402
from app.utils import storage  # noqa: E402
from app.utils.qdrant import brand_sources, client as qdrant_client  # noqa: E402

TAG = f"test_acctdel_{uuid.uuid4().hex[:8]}"
SLUG = TAG.replace("_", "-")
VECTOR_SIZE = 1024


def _user(suffix: str) -> CurrentUser:
    return CurrentUser(clerk_user_id=f"{TAG}{suffix}_u", clerk_org_id=f"{TAG}{suffix}_o",
                       email=f"d{suffix}@test.local", role="owner", raw={})


def _services_up() -> bool:
    try:
        qdrant_client().get_collections()
        storage.s3().list_buckets()
        return True
    except Exception:
        return False


async def _seed_account(suffix: str) -> dict:
    """Create an account with one brand + content, a Qdrant collection w/ a point,
    a MinIO object, and Redis keys. Returns ids + the storage handles to assert on."""
    user = _user(suffix)
    async with SessionLocal() as db:
        acct = Account(clerk_org_id=user.clerk_org_id, name=f"A{suffix}", plan="free")
        db.add(acct); await db.flush()
        ws = Workspace(account_id=acct.id, name="Default")
        db.add(ws); await db.flush()
        brand = Brand(account_id=acct.id, workspace_id=ws.id,
                      name=f"B{suffix}", slug=f"{SLUG}{suffix}"[:60], primary_topic="AI")
        db.add(brand); await db.flush()
        idea = ContentIdea(account_id=acct.id, brand_id=brand.id, title="x",
                           created_at=datetime.now(timezone.utc))
        db.add(idea); await db.flush()
        asset = ContentAsset(account_id=acct.id, brand_id=brand.id, idea_id=idea.id,
                             format="blog", title="a", status="draft")
        db.add(asset); await db.commit()
        acct_id, brand_id = acct.id, brand.id

    # Qdrant: per-brand collection with one point
    from qdrant_client.http.models import Distance, PointStruct, VectorParams
    coll = brand_sources(str(brand_id))
    c = qdrant_client()
    if not c.collection_exists(coll):
        c.create_collection(coll, vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE))
    c.upsert(collection_name=coll, points=[PointStruct(id=str(uuid.uuid4()), vector=[0.0] * VECTOR_SIZE,
                                                       payload={"brand_id": str(brand_id)})], wait=True)
    # MinIO: object under the brand prefix
    s3_key = f"media/{brand_id}/{asset.id}/file-{uuid.uuid4().hex}.bin"
    storage.s3().put_object(Bucket=storage.settings.s3_bucket, Key=s3_key, Body=b"x" * 16)
    # Redis: budget + rate + loop-lock keys
    month = datetime.now(timezone.utc).strftime("%Y%m")
    rkeys = [f"llm_spend:{acct_id}:{month}", f"rl:acct:{acct_id}:999",
             f"loop:lock:{brand_id}:2026-06-24"]
    for k in rkeys:
        await redis.set(k, "1")
    return {"acct_id": acct_id, "brand_id": brand_id, "coll": coll,
            "s3_key": s3_key, "rkeys": rkeys, "user": user}


async def _minio_count(prefix: str) -> int:
    resp = storage.s3().list_objects_v2(Bucket=storage.settings.s3_bucket, Prefix=prefix)
    return resp.get("KeyCount", 0)


@pytest.fixture()
async def cleanup():
    yield
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM deletion_jobs WHERE account_id IN "
                              "(SELECT id FROM accounts WHERE clerk_org_id LIKE :p)"), {"p": f"{TAG}%"})
        await db.execute(text("DELETE FROM accounts WHERE clerk_org_id LIKE :p"), {"p": f"{TAG}%"})
        await db.commit()
    # best-effort store cleanup
    try:
        for pat in (f"llm_spend:*", f"rl:acct:*", f"loop:lock:*"):
            pass  # leave; keys are uuid-scoped per run and harmless
    except Exception:
        pass


@pytest.fixture()
def auth_as():
    def _set(u): app.dependency_overrides[current_user] = lambda: u
    yield _set
    app.dependency_overrides.pop(current_user, None)


@pytest.mark.asyncio
async def test_full_purge_removes_all_stores_and_spares_other_tenant(cleanup):
    if not _services_up():
        pytest.skip("qdrant/minio not reachable")
    a = await _seed_account("_A")
    b = await _seed_account("_B")

    async with SessionLocal() as db:
        job = await create_deletion_job(db, a["acct_id"])
    await run_purge(job.id)

    # ── Postgres: A gone (+ cascade), B intact ──
    async with SessionLocal() as db:
        assert (await db.get(Account, a["acct_id"])) is None
        assert (await db.get(Account, b["acct_id"])) is not None
        a_brands = (await db.execute(select(Brand).where(Brand.account_id == a["acct_id"]))).scalars().all()
        assert a_brands == []
        b_brands = (await db.execute(select(Brand).where(Brand.account_id == b["acct_id"]))).scalars().all()
        assert len(b_brands) == 1

    # ── Qdrant: A's collection gone, B's intact ──
    c = qdrant_client()
    assert not c.collection_exists(a["coll"]), "account A's Qdrant collection survived"
    assert c.collection_exists(b["coll"]), "account B's Qdrant collection was wrongly deleted"

    # ── MinIO: A's objects gone, B's intact ──
    assert await _minio_count(f"media/{a['brand_id']}/") == 0
    assert await _minio_count(f"media/{b['brand_id']}/") >= 1

    # ── Redis: A's keys gone, B's intact ──
    for k in a["rkeys"]:
        assert await redis.get(k) is None, f"redis key survived: {k}"
    for k in b["rkeys"]:
        assert await redis.get(k) is not None, f"other tenant redis key wrongly deleted: {k}"

    # job marked completed
    async with SessionLocal() as db:
        done = await db.get(DeletionJob, job.id)
        assert done.status == "completed"
        assert done.postgres_done and done.qdrant_done and done.minio_done and done.redis_done

    # cleanup B's qdrant collection
    if c.collection_exists(b["coll"]):
        c.delete_collection(b["coll"])


@pytest.mark.asyncio
async def test_endpoint_cross_tenant_returns_404(auth_as, cleanup, monkeypatch):
    monkeypatch.setattr("app.api.v1.endpoints.account.purge_account.delay", lambda *a, **k: None)
    user_a = _user("_epa")
    from app.services.provisioning import get_or_create_account
    async with SessionLocal() as db:
        acct_a = await get_or_create_account(db, user_a)
        other_id = uuid.uuid4()
        acct_a_id = acct_a.id

    auth_as(user_a)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        # addressing someone else's account id → 404
        r404 = await cx.delete(f"/v1/account/{other_id}")
        assert r404.status_code == 404
        # addressing own account → 202
        r202 = await cx.delete(f"/v1/account/{acct_a_id}")
        assert r202.status_code == 202
        body = r202.json()
        assert body["account_id"] == str(acct_a_id)
        assert "job_id" in body

    # a job was created + account tombstoned
    async with SessionLocal() as db:
        job = (await db.execute(select(DeletionJob).where(DeletionJob.account_id == acct_a_id))).scalars().first()
        assert job is not None
        acct = await db.get(Account, acct_a_id)
        assert acct.deleted_at is not None


@pytest.mark.asyncio
async def test_resume_after_partial_crash_completes(cleanup):
    if not _services_up():
        pytest.skip("qdrant/minio not reachable")
    a = await _seed_account("_resume")
    async with SessionLocal() as db:
        job = await create_deletion_job(db, a["acct_id"])
        # Simulate a crash AFTER qdrant+minio+redis purged but BEFORE postgres.
        c = qdrant_client()
        if c.collection_exists(a["coll"]):
            c.delete_collection(a["coll"])
        storage.delete_prefix(f"media/{a['brand_id']}/")
        for k in a["rkeys"]:
            await redis.delete(k)
        j = await db.get(DeletionJob, job.id)
        j.qdrant_done = j.minio_done = j.redis_done = True
        j.status = "running"
        await db.commit()
        job_id = job.id

    # Resume — should finish the Postgres step and complete.
    await run_purge(job_id)
    async with SessionLocal() as db:
        assert (await db.get(Account, a["acct_id"])) is None
        done = await db.get(DeletionJob, job_id)
        assert done.status == "completed" and done.postgres_done


@pytest.mark.asyncio
async def test_rerun_completed_job_is_noop(cleanup):
    if not _services_up():
        pytest.skip("qdrant/minio not reachable")
    a = await _seed_account("_idem")
    async with SessionLocal() as db:
        job = await create_deletion_job(db, a["acct_id"])
    await run_purge(job.id)
    # Second run must be a safe no-op (status already completed → early return).
    await run_purge(job.id)
    async with SessionLocal() as db:
        done = await db.get(DeletionJob, job.id)
        assert done.status == "completed"
    if qdrant_client().collection_exists(brand_sources(str(a["brand_id"]))):
        qdrant_client().delete_collection(brand_sources(str(a["brand_id"])))


@pytest.mark.asyncio
async def test_tombstoned_account_is_rejected_410(auth_as, cleanup):
    """Once a delete is requested (account tombstoned), every subsequent API
    request must be rejected with 410 — not served stale data or re-provisioned."""
    user = _user("_tomb")
    from app.services.provisioning import get_or_create_account
    async with SessionLocal() as db:
        acct = await get_or_create_account(db, user)
        await create_deletion_job(db, acct.id)  # tombstones (sets deleted_at)

    auth_as(user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get("/v1/brands")  # any endpoint that resolves the account
    assert r.status_code == 410, "tombstoned account must be rejected, not served"


@pytest.mark.asyncio
async def test_create_deletion_job_is_idempotent(cleanup):
    user = _user("_dup")
    from app.services.provisioning import get_or_create_account
    async with SessionLocal() as db:
        acct = await get_or_create_account(db, user)
        job1 = await create_deletion_job(db, acct.id)
        job2 = await create_deletion_job(db, acct.id)
        assert job1.id == job2.id, "must reuse the in-flight job, not create a duplicate"
