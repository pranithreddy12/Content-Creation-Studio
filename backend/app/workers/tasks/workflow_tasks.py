"""Workflow runner tasks — bridge Celery → workflow runner."""
from __future__ import annotations

import asyncio
from uuid import UUID

from app.core.logging import log
from app.services.billing import BudgetError
from app.services.workflow.runner import WorkflowCeilingExceeded, run_workflow
from app.workers.celery_app import celery_app


@celery_app.task(name="app.workers.tasks.workflow_tasks.run_workflow_task",
                 autoretry_for=(Exception,), retry_backoff=True, max_retries=2)
def run_workflow_task(workflow_id: str, payload: dict | None = None) -> str:
    try:
        return str(asyncio.run(run_workflow(UUID(workflow_id), payload or {})))
    except (WorkflowCeilingExceeded, BudgetError) as exc:
        # Terminal: the run already records the failure; retrying would re-burn
        # budget / re-trip the ceiling. Do not re-enqueue.
        log.warning("workflow_run_terminal", workflow=workflow_id, reason=str(exc))
        return f"terminal: {exc}"
