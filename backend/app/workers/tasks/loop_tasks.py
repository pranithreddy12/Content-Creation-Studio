"""Daily content loop orchestrator — fires per-brand based on local TZ + run window.

Beat job `kickoff_daily_loops` runs every 15 min and triggers `run_brand_loop` for any
brand whose local clock has just crossed its configured publish_window start (within the
last 15 min), and which has not already run in the last 23 h. Idempotency via
DAILY_LOOP_LOCK Redis key per (brand_id, YYYY-MM-DD).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from celery import chain, group
from sqlalchemy import select

from app.core.logging import log
from app.db.redis import redis
from app.db.session import SessionLocal
from app.models.brand import Brand
from app.workers.celery_app import celery_app
from app.workers.tasks.ideas_tasks import generate_ideas, score_idea, select_top_ideas
from app.workers.tasks.media_tasks import generate_media
from app.workers.tasks.research_tasks import research_brand
from app.workers.tasks.seo_tasks import optimize_asset
from app.workers.tasks.video_tasks import render_video_for_idea
from app.workers.tasks.writing_tasks import generate_blog, generate_social_bundle

# Aliased dotted-name references for chaining without a Python import cycle.
PUBLISH_TASK = "app.workers.tasks.publishing_tasks.dispatch_for_idea"
ANALYTICS_TASK = "app.workers.tasks.analytics_tasks.collect_for_asset"
LEARNING_TASK = "app.workers.tasks.learning_tasks.update_brand"


async def _eligible_brands() -> list[Brand]:
    async with SessionLocal() as db:
        brands = (await db.execute(
            select(Brand).where(Brand.status == "active")
        )).scalars().all()
        return list(brands)


def _due_now(brand: Brand) -> bool:
    try:
        tz = ZoneInfo(brand.timezone or "UTC")
    except Exception:
        tz = ZoneInfo("UTC")
    now_local = datetime.now(tz=tz)
    window_start_str = (brand.publish_window or {}).get("start", "09:00")
    hh, mm = window_start_str.split(":")
    start_today = now_local.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    delta = (now_local - start_today).total_seconds()
    return 0 <= delta < 15 * 60


async def _do_kickoff() -> dict:
    """Async core of the kickoff task — callable from both Celery (via asyncio.run)
    and from tests directly (since they run inside an event loop)."""
    brands = await _eligible_brands()
    fired: list[str] = []
    for b in brands:
        if not _due_now(b):
            continue
        today = datetime.now(timezone.utc).date().isoformat()
        key = f"loop:lock:{b.id}:{today}"
        # Set lock with 23h TTL atomically (NX)
        won = await redis.set(key, "1", ex=23 * 3600, nx=True)
        if not won:
            continue
        run_brand_loop.delay(str(b.id))
        fired.append(str(b.id))
    return {"fired": fired}


@celery_app.task(name="app.workers.tasks.loop_tasks.kickoff_daily_loops")
def kickoff_daily_loops() -> dict:
    """Beat job — fan out daily runs for every eligible brand."""
    return asyncio.run(_do_kickoff())


@celery_app.task(name="app.workers.tasks.loop_tasks.run_brand_loop")
def run_brand_loop(brand_id: str, n_ideas: int = 100) -> str:
    """Full daily chain: research → ideas → score → select → fan-out per idea."""
    log.info("loop_start", brand=brand_id)
    research_id = research_brand.apply_async(args=[brand_id]).get(disable_sync_subtasks=False)
    idea_ids: list[str] = generate_ideas.apply_async(
        args=[brand_id, research_id, n_ideas]
    ).get(disable_sync_subtasks=False)
    # Score in parallel (10 at a time)
    group(score_idea.s(iid) for iid in idea_ids).apply_async().get(disable_sync_subtasks=False)
    # Pick top-K from brand.daily_quota
    quota = _quota(brand_id)
    top_ids: list[str] = select_top_ideas.apply_async(
        args=[brand_id, research_id, quota]
    ).get(disable_sync_subtasks=False)
    # Per-idea pipeline runs in parallel
    group(idea_pipeline.s(iid) for iid in top_ids).apply_async()
    # Schedule learning update at end of day
    learning_signature = celery_app.signature(LEARNING_TASK, args=[brand_id])
    learning_signature.apply_async(countdown=20 * 3600)
    return f"brand:{brand_id} fired pipelines for {len(top_ids)} ideas"


def _quota(brand_id: str) -> int:
    async def _q():
        async with SessionLocal() as db:
            b = (await db.execute(select(Brand).where(Brand.id == UUID(brand_id)))).scalar_one()
            return max(1, int(b.daily_quota or 1))
    return asyncio.run(_q())


@celery_app.task(name="app.workers.tasks.loop_tasks.idea_pipeline")
def idea_pipeline(idea_id: str) -> str:
    """For a single selected idea: write all formats, optimize SEO, render media, publish, schedule analytics."""
    blog_asset_id = generate_blog.apply_async(args=[idea_id]).get(disable_sync_subtasks=False)
    social_asset_ids: list[str] = generate_social_bundle.apply_async(args=[idea_id]).get(disable_sync_subtasks=False)
    all_asset_ids = [blog_asset_id] + social_asset_ids

    # SEO + media in parallel per asset.
    group(
        chain(optimize_asset.s(aid), generate_media.s(aid))
        for aid in all_asset_ids
    ).apply_async()

    # Render videos for video-shaped formats (their bodies live in body_json).
    for aid in social_asset_ids:
        render_video_for_idea.apply_async(args=[idea_id, aid])

    # Dispatch publishing per idea — sequencing is the publisher's job.
    celery_app.signature(PUBLISH_TASK, args=[idea_id]).apply_async()

    # Analytics polling at +24h, +7d, +30d
    for delay in (24 * 3600, 7 * 24 * 3600, 30 * 24 * 3600):
        for aid in all_asset_ids:
            celery_app.signature(ANALYTICS_TASK, args=[aid]).apply_async(countdown=delay)
    return f"idea:{idea_id} pipeline fanned"
