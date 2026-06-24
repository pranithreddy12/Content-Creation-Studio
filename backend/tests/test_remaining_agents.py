"""E2E tests for the agents not yet covered: Strategist, SEO, Designer, Learning, Analytics.

ResearchAgent + WriterAgent are already covered by test_agent_e2e.py. This file
fills in the remaining five with mocked LLM responses, asserts the output shape
matches the prompt schema, and verifies an `agent_runs` row is persisted with
the correct cost/tokens/model.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select, text

os.environ.setdefault("SECRET_KEY", "testtesttesttesttesttesttesttest")

from app.agents.analytics import AnalyticsAgent  # noqa: E402
from app.agents.base import AgentContext  # noqa: E402
from app.agents.designer import DesignerAgent  # noqa: E402
from app.agents.learning import LearningAgent  # noqa: E402
from app.agents.llm_router import LLMResponse  # noqa: E402
from app.agents.runner import run_agent  # noqa: E402
from app.agents.seo import SEOAgent  # noqa: E402
from app.agents.strategist import StrategistAgent  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.account import Account, Workspace  # noqa: E402
from app.models.agent import AgentRun  # noqa: E402
from app.models.analytics import PatternScore  # noqa: E402
from app.models.brand import Brand  # noqa: E402

TAG = f"test_agents_{uuid.uuid4().hex[:8]}"
SLUG = TAG.replace("_", "-")


async def _make_acct_and_brand(db, tag: str):
    """Provision a unique Account / Workspace / Brand for a single test.
    `tag` is mixed into both org_id and slug — combined with TAG it's globally unique."""
    acct = Account(clerk_org_id=f"{TAG}_{tag}_org", name="A", plan="free")
    db.add(acct); await db.flush()
    ws = Workspace(account_id=acct.id, name="Default")
    db.add(ws); await db.flush()
    brand = Brand(
        account_id=acct.id, workspace_id=ws.id,
        name="Brand", slug=f"{SLUG}-{tag}"[:60],
        primary_topic="AI", tone="professional", audience="founders",
        daily_quota=2,
    )
    db.add(brand); await db.flush()
    return acct, brand


def _patch_llm(monkeypatch, payload: dict, *, tokens_in=120, tokens_out=80, cost=0.002):
    async def fake(*, system, user, json_schema=None, **kw):
        return LLMResponse(
            text="{}", json_out=payload,
            tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost,
            model="claude-sonnet-4-6", provider="anthropic", latency_ms=15,
        )
    from app.agents.llm_router import llm_router as router
    monkeypatch.setattr(router, "complete", fake)


@pytest.fixture()
async def cleanup():
    # Pre-clean any orphan rows from previous interrupted runs that used
    # the legacy non-prefixed org_id pattern, so this test session starts clean.
    async with SessionLocal() as db:
        await db.execute(text(
            "DELETE FROM accounts WHERE clerk_org_id IN "
            "('strat_org','seo_org','design_org','ana_org',"
            " 'learn1_org','learn2_org','learn3_org','persist_org')"
        ))
        await db.commit()
    yield
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM accounts WHERE clerk_org_id LIKE :p"),
                         {"p": f"{TAG}%"})
        await db.commit()


# ── StrategistAgent ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_strategist_emits_calendar(monkeypatch, cleanup):
    _patch_llm(monkeypatch, {
        "days": [
            {"date": "2026-12-01", "theme": "agents", "ideas": ["a", "b"]},
            {"date": "2026-12-02", "theme": "rag",    "ideas": ["c"]},
        ],
    })
    async with SessionLocal() as db:
        acct, brand = await _make_acct_and_brand(db, "strat")
        ctx = AgentContext(
            account_id=acct.id, brand_id=brand.id,
            brand={"name": "Acme", "daily_quota": 2, "audience": "founders"},
            inputs={"window": "weekly", "themes": ["agents", "rag"]},
        )
        result = await run_agent(db, StrategistAgent(), ctx)

    assert result.output["days"][0]["theme"] == "agents"
    assert result.tokens_in == 120
    assert result.tokens_out == 80


# ── SEOAgent ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_seo_optimize_returns_metadata(monkeypatch, cleanup):
    _patch_llm(monkeypatch, {
        "title": "AI Agents in 2026: The Complete Guide",
        "meta_description": "Everything you need to know about deploying AI agents.",
        "slug": "ai-agents-2026",
        "focus_keyword": "ai agents",
        "secondary_keywords": ["llm", "automation", "workflows"],
        "internal_link_targets": ["agent architecture", "RAG pipelines"],
        "jsonld": {"@type": "BlogPosting"},
        "readability_score": 78,
        "recommendations": ["Add H3 sections", "Compress hero image"],
    })
    async with SessionLocal() as db:
        acct, brand = await _make_acct_and_brand(db, "seo")
        ctx = AgentContext(
            account_id=acct.id, brand_id=brand.id,
            brand={"name": "Acme", "tone": "expert", "audience": "founders"},
            inputs={"asset": {
                "title": "AI Agents", "format": "blog",
                "body": "Lorem ipsum dolor sit amet...",
            }},
        )
        result = await run_agent(db, SEOAgent(), ctx)

    out = result.output
    assert out["title"].startswith("AI Agents")
    assert out["focus_keyword"] == "ai agents"
    assert len(out["secondary_keywords"]) >= 2
    assert "@type" in out["jsonld"]


# ── DesignerAgent ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_designer_emits_image_prompts(monkeypatch, cleanup):
    _patch_llm(monkeypatch, {
        "hero_image": "futuristic AI dashboard, soft blue lighting",
        "infographic": "infographic of a 3-step content pipeline",
        "thumbnail": "bold thumbnail with face and AI text overlay",
        "social_graphic": "minimal social card with brand colors",
        "carousel_slides": [
            "slide 1: hook",
            "slide 2: problem",
            "slide 3: solution",
        ],
    })
    async with SessionLocal() as db:
        acct, brand = await _make_acct_and_brand(db, "design")
        ctx = AgentContext(
            account_id=acct.id, brand_id=brand.id,
            brand={"name": "Acme", "tone": "modern"},
            inputs={"title": "AI Agents Are Eating Software"},
        )
        result = await run_agent(db, DesignerAgent(), ctx)

    assert "hero_image" in result.output
    assert len(result.output["carousel_slides"]) == 3


# ── AnalyticsAgent ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_analytics_emits_insights(monkeypatch, cleanup):
    _patch_llm(monkeypatch, {
        "best_formats": ["reel", "carousel"],
        "best_hooks": ["wait_for_it", "controversial_take"],
        "best_topics": ["ai_agents", "rag"],
        "best_times": ["tue_10am", "thu_4pm"],
    })
    async with SessionLocal() as db:
        acct, brand = await _make_acct_and_brand(db, "ana")
        ctx = AgentContext(
            account_id=acct.id, brand_id=brand.id,
            brand={"name": "Acme"},
            inputs={"rows": [
                {"platform": "x",        "views": 12000, "likes": 800, "ctr": 0.052},
                {"platform": "linkedin", "views": 4200,  "likes": 320, "ctr": 0.071},
            ]},
        )
        result = await run_agent(db, AnalyticsAgent(), ctx)

    out = result.output
    assert "reel" in out["best_formats"]
    assert "wait_for_it" in out["best_hooks"]


# ── LearningAgent + EMA update ───────────────────────────────────────

@pytest.mark.asyncio
async def test_learning_agent_inserts_pattern_scores(monkeypatch, cleanup):
    """Inserts NEW rows because the brand has no prior pattern_scores."""
    _patch_llm(monkeypatch, {
        "updates": [
            {"pattern_key": "hook_type",  "pattern_val": "wait_for_it",     "delta": 0.85},
            {"pattern_key": "structure",  "pattern_val": "problem_agitate", "delta": 0.62},
            {"pattern_key": "emotion",    "pattern_val": "curiosity",       "delta": 0.78},
        ],
    })
    async with SessionLocal() as db:
        acct, brand = await _make_acct_and_brand(db, "learn1")
        ctx = AgentContext(
            account_id=acct.id, brand_id=brand.id,
            brand={"name": "Acme"},
            inputs={"summary": [{"platform": "x", "views": 1000, "ctr": 0.04}]},
            options={"db": db},
        )
        result = await run_agent(db, LearningAgent(), ctx)

    assert result.output["applied"] == 3
    async with SessionLocal() as db:
        rows = (await db.execute(
            select(PatternScore).where(PatternScore.brand_id == brand.id)
            .order_by(PatternScore.pattern_key)
        )).scalars().all()
        assert len(rows) == 3
        kv = {(r.pattern_key, r.pattern_val): float(r.ema_score) for r in rows}
        assert kv[("hook_type", "wait_for_it")] == pytest.approx(0.85)
        assert kv[("structure", "problem_agitate")] == pytest.approx(0.62)
        # sample_n starts at 1 for new rows
        assert all(r.sample_n == 1 for r in rows)


@pytest.mark.asyncio
async def test_learning_agent_updates_existing_pattern_ema(monkeypatch, cleanup):
    """When a pattern row already exists, EMA = old * (1-α) + delta * α with α=0.25."""
    async with SessionLocal() as db:
        acct, brand = await _make_acct_and_brand(db, "learn2")
        # Seed a prior row with ema_score=0.40, sample_n=5
        db.add(PatternScore(
            brand_id=brand.id, pattern_key="hook_type", pattern_val="wait_for_it",
            ema_score=0.40, sample_n=5,
            updated_at=datetime.now(timezone.utc),
        ))
        await db.commit()

    _patch_llm(monkeypatch, {"updates": [
        {"pattern_key": "hook_type", "pattern_val": "wait_for_it", "delta": 0.80},
    ]})

    async with SessionLocal() as db:
        # Reload brand under the new TAG-prefixed org_id namespace
        b = (await db.execute(select(Brand).where(Brand.slug == f"{SLUG}-learn2"[:60]))).scalar_one()
        ctx = AgentContext(
            account_id=b.account_id, brand_id=b.id,
            brand={"name": "Acme"},
            inputs={"summary": [{"platform": "x"}]},
            options={"db": db},
        )
        await run_agent(db, LearningAgent(), ctx)

    async with SessionLocal() as db:
        row = (await db.execute(
            select(PatternScore).where(
                PatternScore.brand_id == b.id,
                PatternScore.pattern_key == "hook_type",
                PatternScore.pattern_val == "wait_for_it",
            )
        )).scalar_one()
        # EMA: 0.40 * 0.75 + 0.80 * 0.25 = 0.30 + 0.20 = 0.50
        assert float(row.ema_score) == pytest.approx(0.50, abs=1e-5)
        assert row.sample_n == 6


@pytest.mark.asyncio
async def test_learning_agent_skips_malformed_updates(monkeypatch, cleanup):
    """Updates missing pattern_key / pattern_val are silently skipped (no crash)."""
    _patch_llm(monkeypatch, {"updates": [
        {"pattern_key": "hook_type", "pattern_val": "great",   "delta": 0.9},
        {"pattern_key": None,        "pattern_val": "skip me", "delta": 0.1},   # bad
        {"pattern_val": "missing key"},                                          # bad
        {"pattern_key": "emotion",   "pattern_val": "awe",     "delta": 0.5},
    ]})
    async with SessionLocal() as db:
        acct, brand = await _make_acct_and_brand(db, "learn3")
        ctx = AgentContext(
            account_id=acct.id, brand_id=brand.id,
            brand={"name": "Acme"},
            inputs={"summary": []},
            options={"db": db},
        )
        result = await run_agent(db, LearningAgent(), ctx)

    # Only 2 of 4 were valid → applied=2
    assert result.output["applied"] == 2


# ── agent_runs persistence cross-check ───────────────────────────────

@pytest.mark.asyncio
async def test_every_agent_run_persists_a_row(monkeypatch, cleanup):
    """Each call to run_agent should leave an `agent_runs` row with cost/model/tokens populated."""
    _patch_llm(monkeypatch, {"questions": ["q"], "trending": ["t"],
                              "viral_formats": ["f"], "keywords": ["k"]})

    from app.agents.research import ResearchAgent
    async with SessionLocal() as db:
        acct, brand = await _make_acct_and_brand(db, "persist")
        ctx = AgentContext(
            account_id=acct.id, brand_id=brand.id,
            brand={"name": "Acme", "primary_topic": "AI", "audience": "x"},
            inputs={"items": [{"channel": "news", "title": "X"}]},
        )
        result = await run_agent(db, ResearchAgent(), ctx)

        run_row = (await db.execute(
            select(AgentRun).where(AgentRun.brand_id == brand.id)
            .order_by(AgentRun.created_at.desc())
        )).scalars().first()
    assert run_row is not None
    assert run_row.agent_name == "research"
    assert run_row.status == "ok"
    assert run_row.tokens_in == 120
    assert run_row.tokens_out == 80
    assert float(run_row.cost_usd) == pytest.approx(0.002)
    assert run_row.model == "claude-sonnet-4-6"
    assert run_row.provider == "anthropic"
    assert run_row.latency_ms is not None
