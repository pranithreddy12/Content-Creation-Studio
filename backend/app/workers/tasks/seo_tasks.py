"""SEO optimization tasks."""
from __future__ import annotations

import asyncio
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import AgentContext
from app.agents.runner import run_agent
from app.agents.seo import SEOAgent
from app.db.session import SessionLocal
from app.models.brand import Brand
from app.models.content import ContentAsset
from app.workers.celery_app import celery_app


async def _optimize(asset_id: UUID) -> None:
    async with SessionLocal() as db:
        asset = (await db.execute(select(ContentAsset).where(ContentAsset.id == asset_id))).scalar_one()
        brand = (await db.execute(select(Brand).where(Brand.id == asset.brand_id))).scalar_one()
        ctx = AgentContext(
            account_id=brand.account_id,
            brand_id=brand.id,
            brand={"name": brand.name, "tone": brand.tone, "audience": brand.audience},
            inputs={"asset": {
                "title": asset.title,
                "format": asset.format,
                "body": (asset.body or "")[:8000],
                "body_json": asset.body_json,
            }},
        )
        result = await run_agent(db, SEOAgent(), ctx)
        asset.seo = result.output or {}
        if asset.seo.get("title"):
            asset.title = asset.seo["title"]
        await db.commit()


@celery_app.task(name="app.workers.tasks.seo_tasks.optimize_asset",
                 autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def optimize_asset(asset_id: str) -> None:
    asyncio.run(_optimize(UUID(asset_id)))
