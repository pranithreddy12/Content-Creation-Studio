"""Publishing tasks — dispatch + scheduled-publish + webhook ingest.

Per-platform adapters live in app/integrations/<platform>/publisher.py and are
registered via app.integrations.publish_registry. This module is the
brand-agnostic dispatcher.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import log
from app.db.session import SessionLocal
from app.models.brand import Brand
from app.models.content import ContentAsset, ContentIdea
from app.models.publishing import PublishChannel, Schedule
from app.workers.celery_app import celery_app


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
                status="pending",
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


async def _publish_due() -> int:
    """Pick up Schedules whose scheduled_at has passed and dispatch to adapter."""
    from app.integrations import publish_registry  # late import to avoid cycle
    async with SessionLocal() as db:
        now = datetime.now(timezone.utc)
        due = (await db.execute(
            select(Schedule).where(and_(Schedule.status == "pending",
                                         Schedule.scheduled_at <= now))
            .limit(100)
        )).scalars().all()
        n = 0
        for s in due:
            channel = (await db.execute(
                select(PublishChannel).where(PublishChannel.id == s.channel_id)
            )).scalar_one()
            asset = (await db.execute(
                select(ContentAsset).where(ContentAsset.id == s.asset_id)
            )).scalar_one()
            s.status = "publishing"
            s.attempt += 1
            await db.commit()
            try:
                adapter = publish_registry.get(channel.platform)
                if not adapter:
                    raise RuntimeError(f"no adapter for {channel.platform}")
                ext = await adapter.publish(channel, asset)
                s.status = "published"
                s.external_id = ext.get("id")
                s.external_url = ext.get("url")
                s.published_at = datetime.now(timezone.utc)
                asset.status = "published"
                n += 1
            except Exception as exc:
                s.status = "failed"
                s.error = str(exc)[:1000]
                log.exception("publish_failed", schedule=str(s.id))
            await db.commit()
        return n


@celery_app.task(name="app.workers.tasks.publishing_tasks.publish_due")
def publish_due() -> int:
    return asyncio.run(_publish_due())
