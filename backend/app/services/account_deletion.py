"""Hard account deletion — full tenant-data purge across all stores.

Where a tenant's data lives (the map this purge must cover):
  * Postgres — `accounts` row + every account_id-FK table. All FKs are
    ON DELETE CASCADE (verified by test_cascade_delete), so deleting the Account
    row erases all children. Done LAST, because the earlier stores need brand ids.
  * Qdrant — per-BRAND collections `brand_{brand_id}_sources` / `_assets`
    (app.utils.qdrant). The global `viral_patterns` collection is platform-wide
    and is deliberately NOT touched.
  * MinIO/S3 — objects keyed `media/{brand_id}/...` (tts/image_gen/video_render).
  * Redis — per-account `llm_spend:{account_id}:*` (budget) and
    `rl:acct:{account_id}:*` (rate limit); per-brand `loop:lock:{brand_id}:*`.

Ordering matters: brand ids are captured at job creation (while the account still
exists) and stored on the job, because Qdrant + MinIO are keyed by brand and must
be purged using ids that Postgres no longer holds once the cascade runs.

Resumability: each store's completion is a boolean flag on the DeletionJob. A
crash mid-purge leaves the finished flags set; re-running skips them and finishes.
Every step is idempotent, so re-running a completed job is a safe no-op.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.core.logging import log
from app.db.redis import redis
from app.db.session import SessionLocal
from app.models.account import Account, DeletionJob
from app.models.brand import Brand
from app.utils import storage
from app.utils.qdrant import brand_assets, brand_sources
from app.utils.qdrant import client as qdrant_client


async def create_deletion_job(db, account_id: UUID) -> DeletionJob:
    """Create (or return the existing in-flight) purge job for an account.

    Captures brand ids now, while the account still exists. Idempotent: if a
    non-completed job already exists for the account, returns it instead of
    creating a duplicate.
    """
    existing = (await db.execute(
        select(DeletionJob).where(
            DeletionJob.account_id == account_id,
            DeletionJob.status != "completed",
        )
    )).scalars().first()
    if existing:
        return existing

    brand_ids = [str(b) for b in (await db.execute(
        select(Brand.id).where(Brand.account_id == account_id)
    )).scalars().all()]

    job = DeletionJob(account_id=account_id, status="pending", brand_ids=brand_ids)
    db.add(job)
    # Tombstone the account immediately so it reads as "deleting" before the
    # async purge runs.
    acct = await db.get(Account, account_id)
    if acct is not None and acct.deleted_at is None:
        from datetime import datetime, timezone
        acct.deleted_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(job)
    return job


def _purge_qdrant(brand_ids: list[str]) -> None:
    c = qdrant_client()
    for bid in brand_ids:
        for name in (brand_sources(bid), brand_assets(bid)):
            if c.collection_exists(name):
                c.delete_collection(name)


def _purge_minio(brand_ids: list[str]) -> None:
    for bid in brand_ids:
        storage.delete_prefix(f"media/{bid}/")


async def _purge_redis(account_id: UUID, brand_ids: list[str]) -> None:
    patterns = [f"llm_spend:{account_id}:*", f"rl:acct:{account_id}:*"]
    patterns += [f"loop:lock:{bid}:*" for bid in brand_ids]
    for pat in patterns:
        async for key in redis.scan_iter(match=pat):
            await redis.delete(key)


async def _purge_postgres(account_id: UUID) -> None:
    async with SessionLocal() as db:
        acct = await db.get(Account, account_id)
        if acct is not None:
            await db.delete(acct)  # ON DELETE CASCADE clears every child table
            await db.commit()


async def _mark(job_id: UUID, *, field: str | None = None, status: str | None = None) -> None:
    async with SessionLocal() as db:
        job = await db.get(DeletionJob, job_id)
        if job is None:
            return
        if field:
            setattr(job, field, True)
        if status:
            job.status = status
        await db.commit()


async def run_purge(job_id: UUID) -> None:
    """Execute (or resume) the purge for a job. Safe to call repeatedly."""
    async with SessionLocal() as db:
        job = await db.get(DeletionJob, job_id)
        if job is None or job.status == "completed":
            return
        account_id = job.account_id
        brand_ids = list(job.brand_ids or [])
        flags = {
            "qdrant_done": job.qdrant_done,
            "minio_done": job.minio_done,
            "redis_done": job.redis_done,
            "postgres_done": job.postgres_done,
        }
        job.status = "running"
        await db.commit()

    try:
        if not flags["qdrant_done"]:
            _purge_qdrant(brand_ids)
            await _mark(job_id, field="qdrant_done")
        if not flags["minio_done"]:
            _purge_minio(brand_ids)
            await _mark(job_id, field="minio_done")
        if not flags["redis_done"]:
            await _purge_redis(account_id, brand_ids)
            await _mark(job_id, field="redis_done")
        if not flags["postgres_done"]:
            await _purge_postgres(account_id)  # LAST — cascade-deletes all PG rows
            await _mark(job_id, field="postgres_done")
        await _mark(job_id, status="completed")
        log.info("account_purge_done", account_id=str(account_id), job_id=str(job_id))
    except Exception as exc:
        async with SessionLocal() as db:
            job = await db.get(DeletionJob, job_id)
            if job is not None:
                job.status = "failed"
                job.error = str(exc)[:2000]
                await db.commit()
        log.exception("account_purge_failed", job_id=str(job_id))
        raise
