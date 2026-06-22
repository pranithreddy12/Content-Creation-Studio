"""Ingest worker tasks — extract → chunk → embed → Qdrant."""
from __future__ import annotations

import asyncio
from uuid import UUID

from app.core.logging import log
from app.db.session import SessionLocal
from app.services.ingestion.pipeline import ingest_source
from app.workers.celery_app import celery_app


async def _run(source_id: str) -> dict:
    async with SessionLocal() as db:
        return await ingest_source(db, UUID(source_id))


@celery_app.task(
    name="app.workers.tasks.ingest_tasks.ingest_source_task",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    max_retries=4,
)
def ingest_source_task(self, source_id: str) -> dict:
    log.info("ingest_task_start", source_id=source_id, attempt=self.request.retries)
    return asyncio.run(_run(source_id))
