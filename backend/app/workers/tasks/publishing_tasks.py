"""Publishing tasks — dispatch + scheduled-publish + webhook ingest.

Per-platform adapters live in app/integrations/<platform>/publisher.py and are
registered via app.integrations.publish_registry. This module is the
brand-agnostic dispatcher.
"""
from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import log
from app.db.session import SessionLocal
from app.models.brand import Brand
from app.models.content import ContentAsset, ContentIdea
from app.models.publishing import PublishChannel, Schedule, ScheduleStatus
from app.workers.celery_app import celery_app

# A publish that keeps failing transiently is retried up to this many attempts,
# then parked in a terminal 'failed' state instead of looping forever.
MAX_PUBLISH_ATTEMPTS = 5
# A row left in 'publishing' longer than this is considered abandoned (worker
# crashed mid-publish) and reaped to a terminal state for operator review —
# NOT blindly re-posted, since we can't prove the provider call didn't land.
STUCK_PUBLISHING_TIMEOUT = timedelta(minutes=15)


def _idempotency_key(schedule: Schedule) -> str:
    """Deterministic key for a (asset, channel) publish so a provider that
    supports idempotency keys dedupes a duplicate request server-side."""
    return f"pub:{schedule.asset_id}:{schedule.channel_id}"


async def _call_adapter(adapter, channel: PublishChannel, asset: ContentAsset, idem_key: str) -> dict:
    """Call adapter.publish, forwarding the idempotency key only if it accepts one."""
    if "idempotency_key" in inspect.signature(adapter.publish).parameters:
        return await adapter.publish(channel, asset, idempotency_key=idem_key)
    return await adapter.publish(channel, asset)


# Default mapping content_asset.format → publish_channel.platform
FORMAT_TO_PLATFORM = {
    "blog":             "wordpress",
    "linkedin":         "linkedin",
    "x_thread":         "x",
    "instagram":        "instagram",
    "carousel":         "instagram",
    "reel":             "instagram",
    "short":            "youtube",
    "tiktok":           "tiktok",
    "facebook":         "facebook",
    "reddit":           "reddit",
    "quora":            "quora",
    "yt_script":        "youtube",
    "email_newsletter": "email",
    "sales_email":      "email",
}


async def _dispatch(idea_id: UUID) -> int:
    """For every asset of an idea, create a Schedule row pointing at the matching channel."""
    async with SessionLocal() as db:
        idea = (await db.execute(select(ContentIdea).where(ContentIdea.id == idea_id))).scalar_one()
        brand = (await db.execute(select(Brand).where(Brand.id == idea.brand_id))).scalar_one()
        assets = (await db.execute(
            select(ContentAsset).where(ContentAsset.idea_id == idea.id, ContentAsset.status == "draft")
        )).scalars().all()
        channels = (await db.execute(
            select(PublishChannel).where(PublishChannel.brand_id == brand.id,
                                          PublishChannel.status == "connected")
        )).scalars().all()
        by_platform = {c.platform: c for c in channels}

        # Stagger publishes across the brand's publish_window
        now = datetime.now(timezone.utc)
        offset_min = 0
        created = 0
        for a in assets:
            plat = FORMAT_TO_PLATFORM.get(a.format)
            channel = by_platform.get(plat) if plat else None
            if not channel:
                continue
            scheduled_at = now + timedelta(minutes=offset_min)
            db.add(Schedule(
                account_id=brand.account_id,
                brand_id=brand.id,
                asset_id=a.id,
                channel_id=channel.id,
                scheduled_at=scheduled_at,
                status=ScheduleStatus.PENDING,
                created_at=now,
            ))
            a.status = "scheduled"
            offset_min += 30
            created += 1
        await db.commit()
        return created


@celery_app.task(name="app.workers.tasks.publishing_tasks.dispatch_for_idea",
                 autoretry_for=(Exception,), retry_backoff=True, max_retries=2)
def dispatch_for_idea(idea_id: str) -> int:
    return asyncio.run(_dispatch(UUID(idea_id)))


async def _claim(db: AsyncSession, schedule_id: UUID) -> bool:
    """Atomically flip pending→publishing for exactly one worker.

    The conditional UPDATE (status must still be 'pending') is the concurrency
    arbiter: if two workers race the same row, only the one whose UPDATE matches
    a row gets rowcount==1 and proceeds; the other skips. Prevents double-publish.
    """
    res = await db.execute(
        update(Schedule)
        .where(Schedule.id == schedule_id, Schedule.status == ScheduleStatus.PENDING)
        .values(status=ScheduleStatus.PUBLISHING, attempt=Schedule.attempt + 1,
                claimed_at=datetime.now(timezone.utc))
    )
    await db.commit()
    return res.rowcount == 1


async def _publish_due() -> int:
    """Pick up Schedules whose scheduled_at has passed and dispatch to adapter.

    Safe under concurrent runners and re-delivery: each row is claimed atomically
    (so it's published by exactly one worker), already-published rows are never
    re-selected, and transient failures retry up to MAX_PUBLISH_ATTEMPTS before
    going terminal.
    """
    from app.integrations import publish_registry  # late import to avoid cycle
    async with SessionLocal() as db:
        now = datetime.now(timezone.utc)
        due = (await db.execute(
            select(Schedule).where(and_(Schedule.status == ScheduleStatus.PENDING,
                                         Schedule.scheduled_at <= now))
            .limit(100)
        )).scalars().all()
        n = 0
        for s in due:
            if not await _claim(db, s.id):
                continue  # another worker claimed it first
            await db.refresh(s)
            channel = (await db.execute(
                select(PublishChannel).where(PublishChannel.id == s.channel_id)
            )).scalar_one()
            asset = (await db.execute(
                select(ContentAsset).where(ContentAsset.id == s.asset_id)
            )).scalar_one()
            try:
                adapter = publish_registry.get(channel.platform)
                if not adapter:
                    raise RuntimeError(f"no adapter for {channel.platform}")
                ext = await _call_adapter(adapter, channel, asset, _idempotency_key(s))
                s.status = ScheduleStatus.PUBLISHED
                s.external_id = ext.get("id")
                s.external_url = ext.get("url")
                s.published_at = datetime.now(timezone.utc)
                asset.status = "published"
                n += 1
            except Exception as exc:
                # Bounded retry: transient failures go back to 'pending' (re-tried
                # next cycle) until we exhaust attempts, then park terminally.
                if s.attempt >= MAX_PUBLISH_ATTEMPTS:
                    s.status = ScheduleStatus.FAILED
                else:
                    s.status = ScheduleStatus.PENDING
                s.error = str(exc)[:1000]
                log.warning("publish_failed", schedule=str(s.id), attempt=s.attempt, status=s.status)
            await db.commit()
        return n


async def _reap_stuck_publishing() -> int:
    """Park rows abandoned in 'publishing' (worker crashed mid-call).

    We do NOT re-post: a crash after the provider accepted the post would
    double-publish to a customer's real account. Such rows get a DISTINCT
    terminal status 'needs_review' — not 'failed' — because the post may
    already be live; an operator must verify before any re-publish. Abandonment
    is measured from claimed_at (when it entered 'publishing'), not scheduled_at,
    so a row on a legitimate retry isn't reaped for having an old schedule time.
    """
    async with SessionLocal() as db:
        cutoff = datetime.now(timezone.utc) - STUCK_PUBLISHING_TIMEOUT
        res = await db.execute(
            update(Schedule)
            .where(Schedule.status == ScheduleStatus.PUBLISHING,
                   or_(Schedule.claimed_at <= cutoff, Schedule.claimed_at.is_(None)))
            .values(status=ScheduleStatus.NEEDS_REVIEW,
                    error="abandoned in 'publishing' (worker crash?) — post may be live, verify before re-publish")
        )
        await db.commit()
        if res.rowcount:
            log.warning("publish_reaped_stuck", count=res.rowcount)
        return res.rowcount


@celery_app.task(name="app.workers.tasks.publishing_tasks.publish_due", acks_late=True)
def publish_due() -> int:
    # acks_late is safe here: the atomic claim + pending-only select make a
    # redelivered batch idempotent (published rows are skipped, in-flight rows
    # are claimed by whichever worker wins).
    return asyncio.run(_publish_due())


@celery_app.task(name="app.workers.tasks.publishing_tasks.reap_stuck_publishes")
def reap_stuck_publishes() -> int:
    return asyncio.run(_reap_stuck_publishing())
