from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery(
    "studio",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "app.workers.tasks.research_tasks",
        "app.workers.tasks.ideas_tasks",
        "app.workers.tasks.writing_tasks",
        "app.workers.tasks.media_tasks",
        "app.workers.tasks.video_tasks",
        "app.workers.tasks.seo_tasks",
        "app.workers.tasks.publishing_tasks",
        "app.workers.tasks.analytics_tasks",
        "app.workers.tasks.learning_tasks",
        "app.workers.tasks.viral_tasks",
        "app.workers.tasks.loop_tasks",
        "app.workers.tasks.ingest_tasks",
        "app.workers.tasks.workflow_tasks",
    ],
)

celery_app.conf.update(
    task_default_queue="default",
    task_routes={
        "app.workers.tasks.research_tasks.*":   {"queue": "research"},
        "app.workers.tasks.ideas_tasks.*":      {"queue": "research"},
        "app.workers.tasks.writing_tasks.*":    {"queue": "writing"},
        "app.workers.tasks.media_tasks.*":      {"queue": "writing"},
        "app.workers.tasks.video_tasks.*":      {"queue": "video"},
        "app.workers.tasks.seo_tasks.*":        {"queue": "writing"},
        "app.workers.tasks.publishing_tasks.*": {"queue": "publishing"},
        "app.workers.tasks.analytics_tasks.*":  {"queue": "analytics"},
        "app.workers.tasks.learning_tasks.*":   {"queue": "analytics"},
        "app.workers.tasks.viral_tasks.*":      {"queue": "research"},
        "app.workers.tasks.ingest_tasks.*":     {"queue": "heavy"},
        "app.workers.tasks.loop_tasks.*":       {"queue": "default"},
    },
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "kickoff-daily-loops": {
            "task": "app.workers.tasks.loop_tasks.kickoff_daily_loops",
            "schedule": crontab(minute="*/15"),
        },
        "collect-analytics-rollup": {
            "task": "app.workers.tasks.analytics_tasks.rollup_recent",
            "schedule": crontab(minute=10),
        },
        "viral-crawl-hourly": {
            "task": "app.workers.tasks.viral_tasks.crawl_all_platforms",
            "schedule": crontab(minute=5),
        },
    },
)
