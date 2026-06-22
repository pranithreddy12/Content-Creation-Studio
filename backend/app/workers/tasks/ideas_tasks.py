"""Ideas generation + scoring + top-K selection."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import AgentContext
from app.agents.llm_router import llm_router
from app.agents.prompt_registry import prompts
from app.core.logging import log
from app.db.session import SessionLocal
from app.models.brand import Brand
from app.models.content import ContentIdea
from app.models.research import Opportunity, ResearchRun
from app.workers.celery_app import celery_app


async def _generate(brand_id: UUID, research_id: UUID, n: int = 100) -> list[UUID]:
    async with SessionLocal() as db:
        brand = (await db.execute(select(Brand).where(Brand.id == brand_id))).scalar_one()
        opps = (await db.execute(
            select(Opportunity).where(Opportunity.research_id == research_id).limit(120)
        )).scalars().all()
        rendered = "\n".join(f"- [{o.kind}] {o.text}" for o in opps) or "(no opportunities)"

        p = prompts.get("ideas.generate")
        resp = await llm_router.complete(
            system="You produce unique, brand-aligned content ideas. Strict JSON.",
            user=p.template.format(
                n=n,
                brand_name=brand.name,
                tone=brand.tone or "professional",
                audience=brand.audience or "",
                opportunities=rendered,
            ),
            json_schema=p.schema,
            temperature=0.9,
            max_tokens=8000,
        )
        ideas = ((resp.json_out or {}).get("ideas") or [])[:n]
        ids: list[UUID] = []
        for raw in ideas:
            idea = ContentIdea(
                account_id=brand.account_id,
                brand_id=brand.id,
                research_id=research_id,
                title=raw.get("title") or "Untitled",
                angle=raw.get("angle"),
                keywords=([raw.get("primary_keyword")] if raw.get("primary_keyword") else []) +
                         list(raw.get("secondary_keywords") or []),
                audience=raw.get("audience") or brand.audience,
                format_hints=list(raw.get("formats") or []),
                status="new",
                created_at=datetime.now(timezone.utc),
            )
            db.add(idea)
            await db.flush()
            ids.append(idea.id)
        await db.commit()
        log.info("ideas_generated", brand=str(brand_id), n=len(ids))
        return ids


async def _score(idea_id: UUID) -> None:
    async with SessionLocal() as db:
        idea = (await db.execute(select(ContentIdea).where(ContentIdea.id == idea_id))).scalar_one()
        brand = (await db.execute(select(Brand).where(Brand.id == idea.brand_id))).scalar_one()
        p = prompts.get("ideas.score")
        resp = await llm_router.complete(
            system="Score ideas crisply. Strict JSON.",
            user=p.template.format(
                brand_name=brand.name,
                idea=json.dumps({
                    "title": idea.title,
                    "angle": idea.angle,
                    "keywords": idea.keywords,
                    "formats": idea.format_hints,
                }),
            ),
            json_schema=p.schema,
            temperature=0.2,
        )
        s = resp.json_out or {}
        idea.search_volume = int(round((s.get("search_volume") or 0) * 1000))
        idea.trend_velocity = float(s.get("trend_velocity") or 0)
        idea.competition = float(s.get("competition") or 0)
        idea.engagement_est = float(s.get("engagement_est") or 0)
        idea.composite_score = float(s.get("composite_score") or 0)
        await db.commit()


async def _select_top(brand_id: UUID, research_id: UUID, k: int) -> list[UUID]:
    async with SessionLocal() as db:
        rows = (await db.execute(
            select(ContentIdea)
            .where(ContentIdea.brand_id == brand_id, ContentIdea.research_id == research_id,
                   ContentIdea.status == "new")
            .order_by(ContentIdea.composite_score.desc().nulls_last())
            .limit(k)
        )).scalars().all()
        ids: list[UUID] = []
        for idea in rows:
            idea.status = "selected"
            idea.selected_at = datetime.now(timezone.utc)
            ids.append(idea.id)
        await db.commit()
        return ids


@celery_app.task(name="app.workers.tasks.ideas_tasks.generate_ideas",
                 autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def generate_ideas(brand_id: str, research_id: str, n: int = 100) -> list[str]:
    ids = asyncio.run(_generate(UUID(brand_id), UUID(research_id), n))
    return [str(i) for i in ids]


@celery_app.task(name="app.workers.tasks.ideas_tasks.score_idea",
                 autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def score_idea(idea_id: str) -> None:
    asyncio.run(_score(UUID(idea_id)))


@celery_app.task(name="app.workers.tasks.ideas_tasks.select_top_ideas")
def select_top_ideas(brand_id: str, research_id: str, k: int) -> list[str]:
    return [str(i) for i in asyncio.run(_select_top(UUID(brand_id), UUID(research_id), k))]
