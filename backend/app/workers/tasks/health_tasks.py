"""Worker health-check task — used for runtime smoke tests."""
from __future__ import annotations

from datetime import datetime, timezone

from app.workers.celery_app import celery_app


@celery_app.task(name="app.workers.tasks.health_tasks.ping")
def ping(echo: str | None = None) -> dict:
    return {
        "pong": True,
        "echo": echo,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
