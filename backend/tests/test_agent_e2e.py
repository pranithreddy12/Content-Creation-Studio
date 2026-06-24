"""End-to-end agent execution test with the LLM router monkeypatched.

This is the deepest 'does the headline feature work' check: an agent receives
real brand context, calls a (mocked) LLM, and emits the structured output we
later persist + render. We assert the AgentResult shape, that tokens/cost are
recorded, and that the runner writes an `agent_runs` row.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://studio:studio@postgres:5432/studio"
)
os.environ.setdefault("REDIS_URL", "redis://redis:6379/0")
os.environ.setdefault("SECRET_KEY", "testtesttesttesttesttesttesttest")

from sqlalchemy import select, text  # noqa: E402

from app.agents.base import AgentContext  # noqa: E402
from app.agents.research import ResearchAgent  # noqa: E402
from app.agents.runner import run_agent  # noqa: E402
from app.agents.writer import WriterAgent  # noqa: E402
from app.agents.llm_router import LLMResponse  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.account import Account, Workspace  # noqa: E402
from app.models.agent import AgentRun  # noqa: E402
from app.models.brand import Brand  # noqa: E402

TEST_TAG = f"test_agent_{uuid.uuid4().hex[:8]}"


@pytest.fixture()
def patched_llm(monkeypatch):
    """Replace llm_router.complete with a canned async response."""

    async def fake_complete(*, system, user, json_schema=None, **kwargs):
        # Pretend the LLM returned a research-synthesis matching the schema.
        return LLMResponse(
            text='{"questions":["q1"],"trending":["t1"],"viral_formats":["carousel"],"keywords":["kw1"]}',
            json_out={
                "questions": ["q1"],
                "trending": ["t1"],
                "viral_formats": ["carousel"],
                "keywords": ["kw1"],
            },
            tokens_in=120,
            tokens_out=80,
            cost_usd=0.0023,
            model="claude-sonnet-4-6",
            provider="anthropic",
            latency_ms=42,
        )

    from app.agents.llm_router import llm_router as router_instance
    monkeypatch.setattr(router_instance, "complete", fake_complete)
    yield


@pytest.fixture()
def patched_llm_writer(monkeypatch):
    async def writer_complete(*, system, user, json_schema=None, **kwargs):
        return LLMResponse(
            text='{"title":"How AI…","slug":"how-ai","meta_description":"…","outline":[],"body_markdown":"# Title\\nBody."}',
            json_out={
                "title": "How AI Changes Marketing",
                "slug": "how-ai-changes-marketing",
                "meta_description": "An overview of AI-driven marketing.",
                "outline": [{"h2": "Intro", "h3": ["Why now"]}],
                "body_markdown": "# How AI Changes Marketing\n\nBody copy here.",
            },
            tokens_in=400, tokens_out=900, cost_usd=0.015,
            model="claude-sonnet-4-6", provider="anthropic", latency_ms=2100,
        )
    from app.agents.llm_router import llm_router as router_instance
    monkeypatch.setattr(router_instance, "complete", writer_complete)
    yield


async def _make_acct_and_brand(db, tag: str) -> tuple[Account, Brand]:
    acct = Account(clerk_org_id=f"{tag}_org", name="Test Acct", plan="free")
    db.add(acct)
    await db.flush()
    ws = Workspace(account_id=acct.id, name="Default")
    db.add(ws)
    await db.flush()
    brand = Brand(account_id=acct.id, workspace_id=ws.id, name="Acme",
                  slug=f"{tag}-acme"[:60], primary_topic="AI")
    db.add(brand)
    await db.flush()
    return acct, brand


@pytest.mark.asyncio
async def test_research_agent_runs_and_persists(patched_llm):
    async with SessionLocal() as db:
        acct, brand = await _make_acct_and_brand(db, TEST_TAG + "_r")
        ctx = AgentContext(
            account_id=acct.id,
            brand_id=brand.id,
            brand={"name": "Acme", "primary_topic": "AI", "audience": "founders"},
            inputs={"items": [
                {"channel": "news", "title": "OpenAI ships X", "url": "https://x", "excerpt": "..."},
                {"channel": "reddit", "title": "Why GPT-5?", "url": "https://r", "excerpt": "..."},
            ]},
        )
        result = await run_agent(db, ResearchAgent(), ctx)

        # Verify result shape
        assert result.output["questions"] == ["q1"]
        assert result.output["trending"] == ["t1"]
        assert "carousel" in result.output["viral_formats"]
        assert result.tokens_in == 120
        assert result.tokens_out == 80
        assert result.cost_usd == pytest.approx(0.0023)
        assert result.model == "claude-sonnet-4-6"

        # Verify agent_runs row got persisted with cost + tokens
        row = (await db.execute(
            select(AgentRun).where(AgentRun.account_id == acct.id).order_by(AgentRun.created_at.desc())
        )).scalars().first()
        assert row is not None
        assert row.agent_name == "research"
        assert row.status == "ok"
        assert row.tokens_in == 120
        assert row.tokens_out == 80
        assert float(row.cost_usd) == pytest.approx(0.0023)
        assert row.latency_ms is not None and row.latency_ms >= 0

        # Cleanup
        await db.execute(text("DELETE FROM agent_runs WHERE account_id = :a"), {"a": acct.id})
        await db.execute(text("DELETE FROM accounts WHERE id = :a"), {"a": acct.id})
        await db.commit()


@pytest.mark.asyncio
async def test_writer_agent_blog_format(patched_llm_writer):
    """Writer agent in blog mode should produce a body_markdown payload."""
    async with SessionLocal() as db:
        acct, brand = await _make_acct_and_brand(db, TEST_TAG + "_w")
        ctx = AgentContext(
            account_id=acct.id,
            brand_id=brand.id,
            brand={"name": "Acme", "tone": "expert", "audience": "founders",
                   "style_guide": {}, "messaging": {}},
            inputs={"format": "blog", "idea": {
                "title": "How AI Changes Marketing", "angle": "trend",
                "primary_keyword": "ai marketing",
            }, "notes": "Some notes."},
            retrieved=[],
        )
        result = await run_agent(db, WriterAgent(), ctx)
        assert result.output["title"]
        assert "Body copy" in result.output["body_markdown"]
        assert result.tokens_out == 900

        await db.execute(text("DELETE FROM agent_runs WHERE account_id = :a"), {"a": acct.id})
        await db.execute(text("DELETE FROM accounts WHERE id = :a"), {"a": acct.id})
        await db.commit()
