"""Account lifecycle tasks — the async hard-delete purge worker."""
from __future__ import annotations

import asyncio
from uuid import UUID

from app.services.account_deletion import run_purge
from app.workers.celery_app import celery_app


@celery_app.task(name="app.workers.tasks.account_tasks.purge_account",
                 autoretry_for=(Exception,), retry_backoff=True, max_retries=5)
def purge_account(job_id: str) -> str:
    # Resumable + idempotent (see app.services.account_deletion): a retry re-enters
    # run_purge, which skips already-completed stores and finishes the rest.
    asyncio.run(run_purge(UUID(job_id)))
    return job_id
