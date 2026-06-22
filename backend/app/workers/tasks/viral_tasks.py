"""Viral content engine — crawl per-platform, extract patterns, embed into Qdrant."""
from __future__ import annotations

import asyncio
import hashlib
import uuid as uuidlib
from datetime import datetime, timezone
from uuid import UUID

from qdrant_client.http.models import PointStruct
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.llm_router import llm_router
from app.agents.prompt_registry import prompts
from app.core.logging import log
from app.db.session import SessionLocal
from app.models.viral import ViralPattern, ViralPost
from app.services.research.fetchers import fetch_reddit, fetch_x
from app.services.ingestion.embedder import embed_chunks
from app.utils.qdrant import client as qdrant_client, ensure_collection
from app.workers.celery_app import celery_app


VIRAL_THRESHOLDS = {
    "x":         {"likes": 5_000, "views": 100_000},
    "reddit":    {"likes": 5_000},
}
VIRAL_COLLECTION = "viral_patterns"


def _is_viral(channel: str, eng: dict) -> bool:
    th = VIRAL_THRESHOLDS.get(channel, {})
    return any(int(eng.get(k) or 0) >= v for k, v in th.items())


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def _ingest_platform(channel: str, items: list[dict]) -> int:
    ensure_collection(VIRAL_COLLECTION, size=1024)
    saved = 0
    async with SessionLocal() as db:
        for it in items:
            if not _is_viral(channel, it.get("engagement", {})):
                continue
            raw = it.get("excerpt") or it.get("title") or ""
            if not raw or len(raw) < 30:
                continue
            h = _hash(raw)
            existing = (await db.execute(
                select(ViralPost).where(ViralPost.platform == channel,
                                        ViralPost.external_id == it.get("external_id"))
            )).scalar_one_or_none()
            if existing:
                continue
            vp = ViralPost(
                platform=channel,
                external_id=it.get("external_id"),
                url=it.get("url"),
                raw=raw,
                metrics=it.get("engagement", {}),
                posted_at=_parse_dt(it.get("posted_at")),
                crawled_at=datetime.now(timezone.utc),
                hash=h,
            )
            db.add(vp)
            await db.flush()

            # Extract structured pattern via LLM
            p = prompts.get("viral.extract")
            resp = await llm_router.complete(
                system="You distill what makes content go viral. JSON only.",
                user=p.template.format(platform=channel, raw=raw[:2000]),
                json_schema=p.schema,
                temperature=0.2,
            )
            pat = resp.json_out or {}
            qid = str(uuidlib.uuid4())
            try:
                [vec] = embed_chunks([raw[:2000]])
            except Exception:
                vec = None
            if vec:
                qdrant_client().upsert(
                    collection_name=VIRAL_COLLECTION,
                    points=[PointStruct(
                        id=qid,
                        vector=vec,
                        payload={
                            "platform": channel,
                            "viral_post_id": str(vp.id),
                            "hook": pat.get("hook"),
                            "structure": pat.get("structure"),
                            "cta": pat.get("cta"),
                            "emotion": pat.get("emotion"),
                        },
                    )],
                    wait=False,
                )
            db.add(ViralPattern(
                viral_post_id=vp.id,
                platform=channel,
                hook=pat.get("hook"),
                structure=pat.get("structure"),
                cta=pat.get("cta"),
                emotion=pat.get("emotion"),
                embedding_id=qid if vec else None,
                meta={"source_url": it.get("url")},
                created_at=datetime.now(timezone.utc),
            ))
            saved += 1
        await db.commit()
    log.info("viral_ingest_done", platform=channel, saved=saved)
    return saved


def _parse_dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


VIRAL_QUERIES = {
    "x": ["AI", "marketing", "startup", "product"],
    "reddit_subs": ["marketing", "startups", "Entrepreneur", "ContentMarketing"],
}


@celery_app.task(name="app.workers.tasks.viral_tasks.crawl_all_platforms")
def crawl_all_platforms() -> dict:
    return asyncio.run(_crawl())


async def _crawl() -> dict:
    loop = asyncio.get_running_loop()
    x_batches = await asyncio.gather(
        *[loop.run_in_executor(None, lambda q=q: fetch_x(q, count=40)) for q in VIRAL_QUERIES["x"]]
    )
    x_items = [it for batch in x_batches for it in batch]
    reddit_items = await loop.run_in_executor(
        None, lambda: fetch_reddit(VIRAL_QUERIES["reddit_subs"], listing="top", count=25)
    )
    x_saved = await _ingest_platform("x", x_items)
    r_saved = await _ingest_platform("reddit", reddit_items)
    return {"x": x_saved, "reddit": r_saved}


@celery_app.task(name="app.workers.tasks.viral_tasks.crawl_platform")
def crawl_platform(platform: str, query: str) -> int:
    loop = asyncio.new_event_loop()
    try:
        items = fetch_x(query) if platform == "x" else fetch_reddit([query])
        return loop.run_until_complete(_ingest_platform(platform, items))
    finally:
        loop.close()
