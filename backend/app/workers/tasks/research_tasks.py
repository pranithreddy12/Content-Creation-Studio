"""Research worker tasks — pull signal from all configured channels for a brand."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select

from app.agents.base import AgentContext
from app.agents.research import ResearchAgent
from app.agents.runner import run_agent
from app.core.logging import log
from app.db.session import SessionLocal
from app.models.brand import Brand
from app.models.research import Opportunity, ResearchItem, ResearchRun
from app.services.research.fetchers import (
    fetch_competitor_rss,
    fetch_news,
    fetch_quora,
    fetch_reddit,
    fetch_x,
    fetch_youtube,
)
from app.workers.celery_app import celery_app


async def _do_research(brand_id: str) -> str:
    async with SessionLocal() as db:
        brand = (await db.execute(select(Brand).where(Brand.id == UUID(brand_id)))).scalar_one()
        run = ResearchRun(
            brand_id=brand.id,
            account_id=brand.account_id,
            started_at=datetime.now(timezone.utc),
            status="running",
        )
        db.add(run)
        await db.flush()

        topic = brand.primary_topic or brand.name
        subs = list((brand.style_guide or {}).get("subreddits", [])) or []
        competitor_rss = (brand.meta or {}).get("rss_feeds", []) if hasattr(brand, "meta") else []

        # Fan out across channels (sync fetchers, run them concurrently in threads).
        loop = asyncio.get_running_loop()
        gathered = await asyncio.gather(
            loop.run_in_executor(None, lambda: fetch_news(topic)),
            loop.run_in_executor(None, lambda: fetch_reddit(subs)) if subs else _empty(),
            loop.run_in_executor(None, lambda: fetch_quora(topic)),
            loop.run_in_executor(None, lambda: fetch_x(topic)),
            loop.run_in_executor(None, lambda: fetch_youtube(topic)),
            *[loop.run_in_executor(None, lambda u=u: fetch_competitor_rss(u)) for u in competitor_rss],
            return_exceptions=True,
        )
        items: list[dict] = []
        for batch in gathered:
            if isinstance(batch, list):
                items.extend(batch)

        for it in items[:300]:
            db.add(ResearchItem(
                research_id=run.id,
                brand_id=brand.id,
                channel=it["channel"],
                external_id=it.get("external_id"),
                title=it.get("title"),
                url=it.get("url"),
                excerpt=it.get("excerpt"),
                engagement=it.get("engagement", {}),
                meta=it.get("meta", {}),
            ))
        await db.commit()

        # Synthesize opportunities via Research agent.
        agent = ResearchAgent()
        ctx = AgentContext(
            account_id=brand.account_id,
            brand_id=brand.id,
            brand={
                "name": brand.name,
                "primary_topic": brand.primary_topic,
                "audience": brand.audience,
            },
            inputs={"items": items[:80]},
        )
        result = await run_agent(db, agent, ctx)
        synth = result.output or {}
        for kind in ("questions", "trending", "viral_formats", "keywords"):
            for text in (synth.get(kind) or [])[:50]:
                db.add(Opportunity(
                    brand_id=brand.id,
                    research_id=run.id,
                    kind="question" if kind == "questions" else (
                        "trend" if kind == "trending" else (
                            "format" if kind == "viral_formats" else "keyword"
                        )
                    ),
                    text=text,
                    score=0,
                ))
        run.status = "ok"
        run.finished_at = datetime.now(timezone.utc)
        run.meta = {"items": len(items), "synth_keys": list(synth.keys())}
        await db.commit()
        log.info("research_run_done", brand=brand_id, items=len(items))
        return str(run.id)


async def _empty():  # awaitable returning []
    return []


@celery_app.task(
    name="app.workers.tasks.research_tasks.research_brand",
    bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3,
)
def research_brand(self, brand_id: str) -> str:
    return asyncio.run(_do_research(brand_id))
