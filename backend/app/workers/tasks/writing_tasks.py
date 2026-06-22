"""Writer tasks — fan out an idea across all configured content formats."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import AgentContext
from app.agents.retrieval import retrieve_brand_context, retrieve_viral_patterns
from app.agents.runner import run_agent
from app.agents.writer import WriterAgent
from app.core.logging import log
from app.db.session import SessionLocal
from app.models.brand import Brand
from app.models.content import ContentAsset, ContentIdea
from app.workers.celery_app import celery_app


SOCIAL_FORMATS = [
    "linkedin", "x_thread", "instagram", "carousel",
    "reel", "short", "tiktok",
    "email_newsletter", "sales_email",
    "landing", "ad", "facebook",
    "reddit", "quora", "yt_script",
]
ALL_FORMATS = ["blog"] + SOCIAL_FORMATS


def _brand_dict(brand: Brand) -> dict:
    return {
        "name": brand.name,
        "tone": brand.tone,
        "audience": brand.audience,
        "primary_topic": brand.primary_topic,
        "style_guide": brand.style_guide,
        "messaging": brand.messaging,
    }


async def _generate_blog(idea_id: UUID) -> UUID:
    async with SessionLocal() as db:
        idea = (await db.execute(select(ContentIdea).where(ContentIdea.id == idea_id))).scalar_one()
        brand = (await db.execute(select(Brand).where(Brand.id == idea.brand_id))).scalar_one()
        patterns = await retrieve_viral_patterns(idea.title, platform="x", top_k=5)
        notes = await retrieve_brand_context(str(brand.id), idea.title, top_k=10)
        notes_text = "\n".join(n.get("text", "")[:400] for n in notes)

        ctx = AgentContext(
            account_id=brand.account_id,
            brand_id=brand.id,
            brand=_brand_dict(brand),
            inputs={
                "format": "blog",
                "idea": {
                    "title": idea.title,
                    "angle": idea.angle,
                    "primary_keyword": (idea.keywords or [""])[0],
                },
                "notes": notes_text,
            },
            retrieved=patterns,
        )
        result = await run_agent(db, WriterAgent(), ctx)
        out = result.output or {}
        asset = ContentAsset(
            account_id=brand.account_id,
            brand_id=brand.id,
            idea_id=idea.id,
            format="blog",
            title=out.get("title") or idea.title,
            body=out.get("body_markdown"),
            body_json=out,
            word_count=len((out.get("body_markdown") or "").split()),
            status="draft",
        )
        db.add(asset)
        await db.commit()
        await db.refresh(asset)
        return asset.id


async def _generate_social_bundle(idea_id: UUID) -> list[UUID]:
    async with SessionLocal() as db:
        idea = (await db.execute(select(ContentIdea).where(ContentIdea.id == idea_id))).scalar_one()
        brand = (await db.execute(select(Brand).where(Brand.id == idea.brand_id))).scalar_one()
        patterns = await retrieve_viral_patterns(idea.title, top_k=8)
        notes = await retrieve_brand_context(str(brand.id), idea.title, top_k=8)
        notes_text = "\n".join(n.get("text", "")[:300] for n in notes)
        ctx = AgentContext(
            account_id=brand.account_id,
            brand_id=brand.id,
            brand=_brand_dict(brand),
            inputs={
                "format": "social",
                "idea": {"title": idea.title, "angle": idea.angle,
                         "primary_keyword": (idea.keywords or [""])[0]},
                "notes": notes_text,
            },
            retrieved=patterns,
        )
        result = await run_agent(db, WriterAgent(), ctx)
        out = result.output or {}
        created: list[UUID] = []
        for fmt in SOCIAL_FORMATS:
            payload = out.get(fmt)
            if payload is None:
                continue
            body_text = payload if isinstance(payload, str) else _to_text(payload)
            asset = ContentAsset(
                account_id=brand.account_id,
                brand_id=brand.id,
                idea_id=idea.id,
                format=fmt,
                title=idea.title,
                body=body_text,
                body_json=payload if not isinstance(payload, str) else None,
                word_count=len((body_text or "").split()),
                status="draft",
            )
            db.add(asset)
            await db.flush()
            created.append(asset.id)
        idea.status = "generated"
        await db.commit()
        log.info("social_bundle_done", idea=str(idea_id), n=len(created))
        return created


def _to_text(payload) -> str:
    if isinstance(payload, list):
        return "\n\n".join(_to_text(x) for x in payload)
    if isinstance(payload, dict):
        if "body_markdown" in payload:
            return payload["body_markdown"]
        if "narration" in payload and "on_screen" in payload:
            return f"{payload.get('narration', '')}\n[on-screen: {payload.get('on_screen', '')}]"
        return "\n".join(f"{k}: {v}" for k, v in payload.items() if isinstance(v, (str, int)))
    return str(payload)


@celery_app.task(name="app.workers.tasks.writing_tasks.generate_blog",
                 autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def generate_blog(idea_id: str) -> str:
    return str(asyncio.run(_generate_blog(UUID(idea_id))))


@celery_app.task(name="app.workers.tasks.writing_tasks.generate_social_bundle",
                 autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def generate_social_bundle(idea_id: str) -> list[str]:
    return [str(i) for i in asyncio.run(_generate_social_bundle(UUID(idea_id)))]
