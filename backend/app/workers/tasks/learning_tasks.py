"""Learning — feeds analytics rollups into the LearningAgent to update pattern_scores."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select

from app.agents.base import AgentContext
from app.agents.learning import LearningAgent
from app.agents.runner import run_agent
from app.db.session import SessionLocal
from app.models.analytics import AssetMetric
from app.models.brand import Brand
from app.workers.celery_app import celery_app


async def _update_brand(brand_id: UUID) -> dict:
    async with SessionLocal() as db:
        brand = (await db.execute(select(Brand).where(Brand.id == brand_id))).scalar_one()
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        rows = (await db.execute(
            select(
                AssetMetric.platform,
                func.sum(AssetMetric.views).label("views"),
                func.sum(AssetMetric.likes).label("likes"),
                func.sum(AssetMetric.shares).label("shares"),
                func.avg(AssetMetric.ctr).label("ctr"),
            )
            .where(AssetMetric.brand_id == brand_id, AssetMetric.collected_at > cutoff)
            .group_by(AssetMetric.platform)
        )).all()
        summary = [
            {"platform": r.platform, "views": int(r.views or 0), "likes": int(r.likes or 0),
             "shares": int(r.shares or 0), "ctr": float(r.ctr or 0)}
            for r in rows
        ]
        ctx = AgentContext(
            account_id=brand.account_id,
            brand_id=brand_id,
            brand={"name": brand.name, "tone": brand.tone, "audience": brand.audience},
            inputs={"summary": summary},
            options={"db": db},
        )
        result = await run_agent(db, LearningAgent(), ctx)
        return result.output


@celery_app.task(name="app.workers.tasks.learning_tasks.update_brand",
                 autoretry_for=(Exception,), retry_backoff=True, max_retries=2)
def update_brand(brand_id: str) -> dict:
    return asyncio.run(_update_brand(UUID(brand_id)))
