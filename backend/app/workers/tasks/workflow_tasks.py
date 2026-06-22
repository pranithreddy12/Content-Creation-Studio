"""Workflow runner tasks — bridge Celery → workflow runner."""
from __future__ import annotations

import asyncio
from uuid import UUID

from app.services.workflow.runner import run_workflow
from app.workers.celery_app import celery_app


@celery_app.task(name="app.workers.tasks.workflow_tasks.run_workflow_task",
                 autoretry_for=(Exception,), retry_backoff=True, max_retries=2)
def run_workflow_task(workflow_id: str, payload: dict | None = None) -> str:
    return str(asyncio.run(run_workflow(UUID(workflow_id), payload or {})))
