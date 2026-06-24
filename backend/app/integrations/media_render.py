"""Materializes MediaAsset rows whose `storage_key` still starts with `_pending/`
by calling the image generator with the stored prompt and overwriting the row.
"""
from __future__ import annotations

import asyncio
from uuid import UUID

from sqlalchemy import select

from app.db.session import SessionLocal
from app.integrations.image_gen import generate_image
from app.models.content import MediaAsset
from app.workers.celery_app import celery_app


async def _render(media_id: UUID) -> dict:
    async with SessionLocal() as db:
        m = (await db.execute(select(MediaAsset).where(MediaAsset.id == media_id))).scalar_one()
        if not m.storage_key.startswith("_pending/"):
            return {"id": str(media_id), "skipped": True}
        slot = (m.meta or {}).get("slot", "hero")
        key, provider = generate_image(
            m.prompt or "branded illustration", str(m.brand_id), str(m.asset_id), slot
        )
        m.storage_key = key
        m.provider = provider
        m.mime_type = "image/png"
        await db.commit()
    return {"id": str(media_id), "key": key, "provider": provider}


@celery_app.task(name="app.integrations.media_render.render_pending_media",
                 autoretry_for=(Exception,), retry_backoff=True, max_retries=2,
                 queue="writing")
def render_pending_media(media_id: str) -> dict:
    return asyncio.run(_render(UUID(media_id)))
