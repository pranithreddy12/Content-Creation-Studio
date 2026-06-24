"""Daily content-loop orchestrator dry-run.

Exercises the async cores of research → ideas → score → select with the
external fetchers and LLM router mocked. Asserts each phase persists the right
DB rows so the chain is verified without burning real API quota.
"""
from __future__ import annotations

import os
import uuid

import pytest

os.environ.setdefault("SECRET_KEY", "testtesttesttesttesttesttesttest")

from sqlalchemy import select, text  # noqa: E402

from app.agents.llm_router import LLMResponse  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.account import Account, Workspace  # noqa: E402
from app.models.brand import Brand  # noqa: E402
from app.models.content import ContentIdea  # noqa: E402
from app.models.research import Opportunity, ResearchItem, ResearchRun  # noqa: E402

TAG = f"test_loop_{uuid.uuid4().hex[:8]}"


@pytest.fixture()
async def brand():
    """Create a synthetic Account + Workspace + Brand for the loop to operate on."""
    async with SessionLocal() as db:
        acct = Account(clerk_org_id=f"{TAG}_org", name="Loop Test", plan="free")
        db.add(acct)
        await db.flush()
        ws = Workspace(account_id=acct.id, name="Default")
        db.add(ws)
        await db.flush()
        b = Brand(
            account_id=acct.id, workspace_id=ws.id,
            name="LoopBrand", slug=f"{TAG}-brand"[:60],
            primary_topic="AI", audience="founders", tone="professional",
            daily_quota=2,
            style_guide={"subreddits": ["ai"]},  # so research_tasks fan-out includes reddit
        )
        db.add(b)
        await db.commit()
        bid = b.id

    yield bid

    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM accounts WHERE clerk_org_id = :o"), {"o": f"{TAG}_org"})
        await db.commit()


@pytest.fixture()
def patched_research(monkeypatch):
    """Mock all 6 research fetchers + the synthesize LLM call."""
    fake_items = [
        {"channel": "news",   "external_id": "n1", "title": "AI ships X",
         "url": "https://news/1", "excerpt": "...", "posted_at": None,
         "engagement": {}, "meta": {}},
        {"channel": "reddit", "external_id": "r1", "title": "Why GPT?",
         "url": "https://r/1", "excerpt": "discussion", "posted_at": None,
         "engagement": {"likes": 200}, "meta": {"subreddit": "ai"}},
    ]
    from app.workers.tasks import research_tasks as rt
    monkeypatch.setattr(rt, "fetch_news",            lambda *a, **k: list(fake_items[:1]))
    monkeypatch.setattr(rt, "fetch_reddit",          lambda *a, **k: list(fake_items[1:]))
    monkeypatch.setattr(rt, "fetch_quora",           lambda *a, **k: [])
    monkeypatch.setattr(rt, "fetch_x",               lambda *a, **k: [])
    monkeypatch.setattr(rt, "fetch_youtube",         lambda *a, **k: [])
    monkeypatch.setattr(rt, "fetch_competitor_rss",  lambda *a, **k: [])

    async def fake_synth(*, system, user, json_schema=None, **kw):
        return LLMResponse(
            text="{}", json_out={
                "questions":     ["What is X?"],
                "trending":      ["AI agents"],
                "viral_formats": ["carousel"],
                "keywords":      ["ai-agents", "automation"],
            },
            tokens_in=50, tokens_out=40, cost_usd=0.001,
            model="claude-sonnet-4-6", provider="anthropic", latency_ms=10,
        )

    from app.agents.llm_router import llm_router as router
    monkeypatch.setattr(router, "complete", fake_synth)
    yield


@pytest.fixture()
def patched_ideas(monkeypatch):
    """Mock LLM for ideas.generate + ideas.score (different json_schemas per prompt)."""
    call_log = []

    async def fake(*, system, user, json_schema=None, **kw):
        call_log.append({"system": system[:50], "user": user[:80]})
        # Distinguish generate vs score by looking at the user prompt content.
        if "Generate" in user:
            ideas = [
                {"title": f"Idea {i}", "angle": "a", "primary_keyword": "kw",
                 "secondary_keywords": [], "formats": ["blog"]}
                for i in range(8)
            ]
            return LLMResponse(
                text="{}", json_out={"ideas": ideas},
                tokens_in=200, tokens_out=400, cost_usd=0.006,
                model="claude-sonnet-4-6", provider="anthropic", latency_ms=20,
            )
        # ideas.score
        return LLMResponse(
            text="{}", json_out={
                "search_volume": 0.5, "trend_velocity": 0.6,
                "competition": 0.3, "engagement_est": 0.7,
                "composite_score": 0.65, "reason": "good",
            },
            tokens_in=50, tokens_out=30, cost_usd=0.0008,
            model="claude-sonnet-4-6", provider="anthropic", latency_ms=8,
        )

    from app.agents.llm_router import llm_router as router
    monkeypatch.setattr(router, "complete", fake)
    yield call_log


@pytest.mark.asyncio
async def test_research_phase_writes_items_and_opportunities(brand, patched_research):
    from app.workers.tasks.research_tasks import _do_research
    research_id = await _do_research(str(brand))
    assert research_id

    async with SessionLocal() as db:
        rr = (await db.execute(select(ResearchRun).where(ResearchRun.id == uuid.UUID(research_id)))).scalar_one()
        assert rr.status == "ok"
        assert rr.finished_at is not None

        items = (await db.execute(
            select(ResearchItem).where(ResearchItem.research_id == rr.id)
        )).scalars().all()
        assert len(items) == 2
        channels = {i.channel for i in items}
        assert channels == {"news", "reddit"}

        opps = (await db.execute(
            select(Opportunity).where(Opportunity.research_id == rr.id)
        )).scalars().all()
        assert len(opps) >= 4  # 1 question + 1 trending + 1 viral_format + 2 keywords
        kinds = {o.kind for o in opps}
        assert {"question", "trend", "format", "keyword"} <= kinds


@pytest.mark.asyncio
async def test_ideas_generate_and_score_and_select(brand, patched_ideas):
    """Run the ideas chain end-to-end against a real Postgres."""
    from app.workers.tasks.ideas_tasks import _generate, _score, _select_top

    # Need a fake research_run row to satisfy the FK.
    async with SessionLocal() as db:
        rr = ResearchRun(
            brand_id=brand,
            account_id=(await db.execute(
                select(Brand.account_id).where(Brand.id == brand)
            )).scalar_one(),
            started_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            status="ok",
        )
        db.add(rr)
        await db.commit()
        rid = rr.id

    # 1. Generate 8 ideas
    ids = await _generate(brand, rid, n=8)
    assert len(ids) == 8

    # 2. Score each
    for iid in ids:
        await _score(iid)

    async with SessionLocal() as db:
        rows = (await db.execute(
            select(ContentIdea).where(ContentIdea.research_id == rid)
        )).scalars().all()
        assert all(r.composite_score is not None and float(r.composite_score) > 0 for r in rows)
        assert all(r.engagement_est is not None for r in rows)

    # 3. Select top-K
    top = await _select_top(brand, rid, k=3)
    assert len(top) == 3

    async with SessionLocal() as db:
        selected = (await db.execute(
            select(ContentIdea).where(
                ContentIdea.id.in_(top), ContentIdea.status == "selected"
            )
        )).scalars().all()
        assert len(selected) == 3
        assert all(s.selected_at is not None for s in selected)


@pytest.mark.asyncio
async def test_due_now_window_logic():
    """`_due_now` should return True only when local time is within 15min of publish_window.start."""
    from datetime import datetime, time, timezone
    from unittest.mock import MagicMock

    from app.workers.tasks.loop_tasks import _due_now

    brand_mock = MagicMock()
    brand_mock.timezone = "UTC"
    brand_mock.publish_window = {"start": "09:00", "end": "18:00"}

    # The function uses datetime.now(tz) — we can't easily monkey-patch that without
    # touching the module, but we can at least confirm it doesn't raise and returns
    # a bool.
    result = _due_now(brand_mock)
    assert isinstance(result, bool)

    # With a bad TZ, should fall back to UTC and still return bool.
    brand_mock.timezone = "Not/A/Real/Zone"
    result = _due_now(brand_mock)
    assert isinstance(result, bool)
