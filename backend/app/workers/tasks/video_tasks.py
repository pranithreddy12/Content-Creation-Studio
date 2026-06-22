"""Video tasks — script via VideoAgent + render via M7 pipeline."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import AgentContext
from app.agents.retrieval import retrieve_viral_patterns
from app.agents.runner import run_agent
from app.agents.video import VideoAgent
from app.db.session import SessionLocal
from app.models.brand import Brand
from app.models.content import ContentAsset, VideoRender
from app.workers.celery_app import celery_app


VIDEO_FORMATS = {"reel": 30, "short": 45, "tiktok": 45, "yt_script": 480}


async def _render(idea_id: UUID, asset_id: UUID) -> str | None:
    async with SessionLocal() as db:
        asset = (await db.execute(select(ContentAsset).where(ContentAsset.id == asset_id))).scalar_one()
        if asset.format not in VIDEO_FORMATS:
            return None
        brand = (await db.execute(select(Brand).where(Brand.id == asset.brand_id))).scalar_one()
        patterns = await retrieve_viral_patterns(asset.title or "", top_k=5)
        ctx = AgentContext(
            account_id=brand.account_id,
            brand_id=brand.id,
            brand={"name": brand.name, "tone": brand.tone},
            inputs={
                "format": asset.format,
                "title": asset.title,
                "duration": VIDEO_FORMATS[asset.format],
            },
            retrieved=patterns,
        )
        result = await run_agent(db, VideoAgent(), ctx)
        script = result.output or {}
        vr = VideoRender(
            asset_id=asset.id,
            brand_id=brand.id,
            format=asset.format,
            script_json=script,
            storyboard=None,
            status="queued",
            cost_usd=result.cost_usd,
            created_at=datetime.now(timezone.utc),
        )
        db.add(vr)
        await db.flush()
        await db.commit()
        # Actual TTS + b-roll + ffmpeg render dispatched to the dedicated `video` queue
        # (see app/integrations/video_render.py in M7).
        from app.integrations.video_render import enqueue_render
        try:
            enqueue_render(str(vr.id))
        except Exception:
            pass
        return str(vr.id)


@celery_app.task(name="app.workers.tasks.video_tasks.render_video_for_idea",
                 autoretry_for=(Exception,), retry_backoff=True, max_retries=2)
def render_video_for_idea(idea_id: str, asset_id: str) -> str | None:
    return asyncio.run(_render(UUID(idea_id), UUID(asset_id)))
