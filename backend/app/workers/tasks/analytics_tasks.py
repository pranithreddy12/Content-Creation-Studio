"""Analytics — pulls platform metrics for published assets and rolls up insights."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Iterable
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import log
from app.db.session import SessionLocal
from app.models.analytics import AssetMetric
from app.models.brand import Brand
from app.models.publishing import Schedule
from app.workers.celery_app import celery_app


async def _collect_for_asset(asset_id: UUID) -> None:
    """Pull platform metrics for each published schedule of an asset.

    The actual API calls live in app/integrations/<platform>/analytics.py and
    are dispatched per-platform here. Missing integrations no-op silently.
    """
    async with SessionLocal() as db:
        scheds = (await db.execute(
            select(Schedule).where(Schedule.asset_id == asset_id, Schedule.status == "published")
        )).scalars().all()
        for s in scheds:
            try:
                metrics = await _fetch_platform_metrics(s)
            except Exception:
                log.exception("metrics_fetch_failed", schedule_id=str(s.id))
                continue
            if not metrics:
                continue
            db.add(AssetMetric(
                asset_id=asset_id,
                brand_id=s.brand_id,
                platform=metrics.get("platform"),
                collected_at=datetime.now(timezone.utc),
                views=metrics.get("views"),
                clicks=metrics.get("clicks"),
                shares=metrics.get("shares"),
                saves=metrics.get("saves"),
                comments=metrics.get("comments"),
                likes=metrics.get("likes"),
                watch_time_s=metrics.get("watch_time_s"),
                ctr=metrics.get("ctr"),
                meta=metrics.get("meta", {}),
            ))
        await db.commit()


async def _fetch_platform_metrics(s: Schedule) -> dict | None:
    # Dispatch to integration package; returns None when not configured.
    try:
        from app.integrations import publish_registry
        adapter = publish_registry.get(getattr(s, "platform", None))
        if adapter and hasattr(adapter, "fetch_metrics"):
            return await adapter.fetch_metrics(s)
    except Exception:
        log.exception("metrics_adapter_failed")
    return None


async def _rollup(brand_id: UUID | None = None, window_hours: int = 24) -> int:
    """Window-rolled rollup writes weekly snapshots into agent inputs."""
    async with SessionLocal() as db:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
        q = select(
            AssetMetric.brand_id,
            AssetMetric.platform,
            func.sum(AssetMetric.views).label("views"),
            func.sum(AssetMetric.clicks).label("clicks"),
            func.sum(AssetMetric.shares).label("shares"),
            func.sum(AssetMetric.likes).label("likes"),
            func.avg(AssetMetric.ctr).label("ctr"),
        ).where(AssetMetric.collected_at > cutoff)
        if brand_id:
            q = q.where(AssetMetric.brand_id == brand_id)
        q = q.group_by(AssetMetric.brand_id, AssetMetric.platform)
        rows = (await db.execute(q)).all()
        return len(rows)


@celery_app.task(name="app.workers.tasks.analytics_tasks.collect_for_asset",
                 autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def collect_for_asset(asset_id: str) -> None:
    asyncio.run(_collect_for_asset(UUID(asset_id)))


@celery_app.task(name="app.workers.tasks.analytics_tasks.rollup_recent")
def rollup_recent() -> int:
    return asyncio.run(_rollup())
