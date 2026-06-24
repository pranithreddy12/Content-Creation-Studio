"""Celery beat schedule sanity tests.

Verify that every entry in `beat_schedule` references a real registered task,
and that every cron schedule is parseable.
"""
from __future__ import annotations

import pytest
from celery.schedules import crontab

from app.workers.celery_app import celery_app


# Force-import every task module so @task decorators register before introspection.
# The Celery app would normally do this when a worker boots; for unit tests we do it manually.
for _mod in celery_app.conf.imports or celery_app.conf.include or []:
    __import__(_mod)


def test_beat_schedule_has_expected_jobs():
    schedule = celery_app.conf.beat_schedule
    expected = {"kickoff-daily-loops", "collect-analytics-rollup", "viral-crawl-hourly"}
    assert expected <= set(schedule.keys()), f"missing: {expected - set(schedule.keys())}"


def test_every_beat_entry_targets_a_registered_task():
    schedule = celery_app.conf.beat_schedule
    registered = set(celery_app.tasks.keys())
    for name, entry in schedule.items():
        task_name = entry["task"]
        assert task_name in registered, (
            f"beat entry '{name}' targets unknown task '{task_name}'; "
            f"registered={[t for t in registered if 'tasks' in t][:5]}..."
        )


def test_every_beat_entry_has_crontab_schedule():
    """Every entry's `schedule` value must be a crontab (not an int interval) — keeps cadence predictable."""
    for name, entry in celery_app.conf.beat_schedule.items():
        sched = entry["schedule"]
        assert isinstance(sched, crontab), f"beat entry '{name}' uses non-crontab schedule {type(sched).__name__}"


def test_task_routes_target_registered_tasks_or_globs():
    """Every key in task_routes is either a wildcard (`*.something.*`) or a concrete task name that exists."""
    routes = celery_app.conf.task_routes
    registered = set(celery_app.tasks.keys())
    for pattern, _route in routes.items():
        if pattern.endswith(".*"):
            prefix = pattern[:-2]
            assert any(t.startswith(prefix) for t in registered), (
                f"task_routes pattern '{pattern}' matches no registered task"
            )
        else:
            assert pattern in registered, f"task_routes targets unknown task '{pattern}'"


def test_workflow_health_research_writing_video_tasks_all_load():
    """Confirm every Celery task module imports cleanly via the registry."""
    expected_modules = [
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
        "app.workers.tasks.health_tasks",
    ]
    registered = celery_app.tasks.keys()
    for mod in expected_modules:
        assert any(t.startswith(mod + ".") for t in registered), (
            f"no registered tasks under module '{mod}' — module did not load?"
        )
