"""Media tasks — designer agent + image generation; full implementations land in M7."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import AgentContext
from app.agents.designer import DesignerAgent
from app.agents.runner import run_agent
from app.db.session import SessionLocal
from app.models.brand import Brand
from app.models.content import ContentAsset, MediaAsset
from app.workers.celery_app import celery_app


async def _generate(_prev, asset_id: UUID) -> list[UUID]:
    """Generate image prompts via DesignerAgent.

    Renders the actual bitmaps in M7 via app.integrations.image_gen.
    For now we persist the prompts as MediaAsset rows with provider='prompt-only'.
    """
    async with SessionLocal() as db:
        asset = (await db.execute(select(ContentAsset).where(ContentAsset.id == asset_id))).scalar_one()
        brand = (await db.execute(select(Brand).where(Brand.id == asset.brand_id))).scalar_one()
        ctx = AgentContext(
            account_id=brand.account_id,
            brand_id=brand.id,
            brand={"name": brand.name, "tone": brand.tone, "audience": brand.audience},
            inputs={"title": asset.title or asset.format},
        )
        result = await run_agent(db, DesignerAgent(), ctx)
        out = result.output or {}
        created: list[UUID] = []
        slot_to_kind = {
            "hero_image": "image",
            "infographic": "infographic",
            "thumbnail": "thumbnail",
            "social_graphic": "social_graphic",
        }
        from app.integrations.media_render import render_pending_media
        for slot, kind in slot_to_kind.items():
            prompt = out.get(slot)
            if not prompt:
                continue
            m = MediaAsset(
                account_id=brand.account_id,
                brand_id=brand.id,
                asset_id=asset.id,
                kind=kind,
                storage_key=f"_pending/{asset.id}/{slot}.png",
                prompt=prompt if isinstance(prompt, str) else str(prompt),
                provider="prompt-only",
                meta={"slot": slot},
                created_at=datetime.now(timezone.utc),
            )
            db.add(m)
            await db.flush()
            created.append(m.id)
            render_pending_media.delay(str(m.id))
        for i, slide_prompt in enumerate(out.get("carousel_slides") or [], start=1):
            m = MediaAsset(
                account_id=brand.account_id,
                brand_id=brand.id,
                asset_id=asset.id,
                kind="social_graphic",
                storage_key=f"_pending/{asset.id}/carousel-{i}.png",
                prompt=slide_prompt if isinstance(slide_prompt, str) else str(slide_prompt),
                provider="prompt-only",
                meta={"slot": f"carousel:{i}"},
                created_at=datetime.now(timezone.utc),
            )
            db.add(m)
            await db.flush()
            created.append(m.id)
            render_pending_media.delay(str(m.id))
        await db.commit()
        return created


@celery_app.task(name="app.workers.tasks.media_tasks.generate_media",
                 autoretry_for=(Exception,), retry_backoff=True, max_retries=2)
def generate_media(prev_result, asset_id: str) -> list[str]:
    return [str(i) for i in asyncio.run(_generate(prev_result, UUID(asset_id)))]
