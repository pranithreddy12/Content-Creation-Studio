"""Viral content engine tests.

Mocks the fetchers (X/Reddit search), the LLM (viral.extract prompt), and the
embedder/Qdrant client, then verifies:

  * virality threshold filtering — sub-threshold items are skipped
  * hash dedupe — re-running with the same payload doesn't insert again
  * happy path — viral_posts + viral_patterns rows persist with the LLM-extracted
    hook/structure/cta/emotion fields
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select, text

os.environ.setdefault("SECRET_KEY", "testtesttesttesttesttesttesttest")

from app.agents.llm_router import LLMResponse  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.viral import ViralPattern, ViralPost  # noqa: E402

TAG = f"test_viral_{uuid.uuid4().hex[:8]}"


@pytest.fixture()
def patched_llm(monkeypatch):
    """Canned response for the viral.extract prompt."""
    async def fake(*, system, user, json_schema=None, **kw):
        # Each call returns a different pattern so we can tell calls apart later.
        idx = fake.calls
        fake.calls += 1
        return LLMResponse(
            text="{}", json_out={
                "hook":      f"hook-{idx}",
                "structure": "problem-agitate-solve",
                "cta":       "follow for more",
                "emotion":   "curiosity",
            },
            tokens_in=80, tokens_out=40, cost_usd=0.0009,
            model="claude-sonnet-4-6", provider="anthropic", latency_ms=12,
        )
    fake.calls = 0

    from app.agents.llm_router import llm_router as router
    monkeypatch.setattr(router, "complete", fake)
    return fake


@pytest.fixture()
def patched_embedder_and_qdrant(monkeypatch):
    """No real embedder + Qdrant — return deterministic vectors, record upserts."""
    from app.workers.tasks import viral_tasks as vt

    upserts = []

    def fake_embed(texts):
        return [[0.0] * 1024 for _ in texts]

    class FakeQdrant:
        def upsert(self, *, collection_name, points, wait=False):
            upserts.append({"collection": collection_name,
                            "n_points": len(points),
                            "ids": [p.id for p in points]})

    monkeypatch.setattr(vt, "embed_chunks", fake_embed)
    monkeypatch.setattr(vt, "qdrant_client", lambda: FakeQdrant())
    monkeypatch.setattr(vt, "ensure_collection", lambda *a, **kw: None)
    return upserts


@pytest.fixture()
async def cleanup():
    yield
    async with SessionLocal() as db:
        await db.execute(text(
            "DELETE FROM viral_posts WHERE external_id LIKE :p"
        ), {"p": f"{TAG}%"})
        await db.commit()


# ── helpers ──────────────────────────────────────────────────────────

def _viral_x_item(idx: int, *, likes: int, body_len: int = 200) -> dict:
    body = "x" * body_len  # ensures > 30 chars
    return {
        "channel": "x",
        "external_id": f"{TAG}_x_{idx}",
        "title": f"viral tweet {idx}",
        "url": f"https://x.com/i/web/status/{TAG}_{idx}",
        "excerpt": body,
        "posted_at": None,
        "engagement": {"likes": likes, "views": likes * 30},
        "meta": {},
    }


# ── threshold filtering ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_below_threshold_items_are_skipped(patched_llm, patched_embedder_and_qdrant, cleanup):
    from app.workers.tasks.viral_tasks import _ingest_platform

    # X threshold = likes >= 5000 OR views >= 100000
    items = [
        _viral_x_item(1, likes=100),     # < threshold (3000 views)
        _viral_x_item(2, likes=4999),    # likes < 5000, views = 149970 → >= 100000 → VIRAL
        _viral_x_item(3, likes=10000),   # clearly viral
    ]
    saved = await _ingest_platform("x", items)
    assert saved == 2, f"expected 2 viral items, got {saved}"

    async with SessionLocal() as db:
        posts = (await db.execute(
            select(ViralPost).where(ViralPost.external_id.like(f"{TAG}%"))
        )).scalars().all()
        assert len(posts) == 2
        ext_ids = {p.external_id for p in posts}
        assert f"{TAG}_x_1" not in ext_ids, "sub-threshold item leaked through"


@pytest.mark.asyncio
async def test_too_short_raw_text_is_skipped(patched_llm, patched_embedder_and_qdrant, cleanup):
    """Items with raw text < 30 chars are filtered out — they're not interesting patterns."""
    from app.workers.tasks.viral_tasks import _ingest_platform

    short = _viral_x_item(1, likes=10000, body_len=10)  # body=10 chars
    long  = _viral_x_item(2, likes=10000, body_len=200)
    saved = await _ingest_platform("x", [short, long])
    assert saved == 1


# ── happy path & dedupe ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_viral_post_and_pattern_persist(patched_llm, patched_embedder_and_qdrant, cleanup):
    from app.workers.tasks.viral_tasks import _ingest_platform

    item = _viral_x_item(99, likes=20000)
    saved = await _ingest_platform("x", [item])
    assert saved == 1

    async with SessionLocal() as db:
        vp = (await db.execute(
            select(ViralPost).where(ViralPost.external_id == f"{TAG}_x_99")
        )).scalar_one()
        assert vp.platform == "x"
        assert vp.metrics["likes"] == 20000
        assert vp.hash, "hash must be set"
        assert vp.crawled_at is not None
        assert vp.raw.startswith("x")

        pat = (await db.execute(
            select(ViralPattern).where(ViralPattern.viral_post_id == vp.id)
        )).scalar_one()
        assert pat.hook == "hook-0"
        assert pat.structure == "problem-agitate-solve"
        assert pat.cta == "follow for more"
        assert pat.emotion == "curiosity"
        assert pat.embedding_id is not None  # Qdrant point id stored

    # Qdrant upsert recorded
    assert len(patched_embedder_and_qdrant) >= 1


@pytest.mark.asyncio
async def test_rerun_skips_already_known_external_id(patched_llm, patched_embedder_and_qdrant, cleanup):
    """Two runs of the same item should only insert once (dedupe by platform+external_id)."""
    from app.workers.tasks.viral_tasks import _ingest_platform

    item = _viral_x_item(42, likes=15000)
    s1 = await _ingest_platform("x", [item])
    s2 = await _ingest_platform("x", [item])
    assert s1 == 1
    assert s2 == 0, "second pass should not re-insert"

    async with SessionLocal() as db:
        count = (await db.execute(
            select(ViralPost).where(ViralPost.external_id == f"{TAG}_x_42")
        )).scalars().all()
        assert len(count) == 1


@pytest.mark.asyncio
async def test_llm_called_once_per_viral_post(patched_llm, patched_embedder_and_qdrant, cleanup):
    from app.workers.tasks.viral_tasks import _ingest_platform

    items = [_viral_x_item(i, likes=10000) for i in range(3)]
    await _ingest_platform("x", items)
    assert patched_llm.calls == 3


@pytest.mark.asyncio
async def test_reddit_uses_its_own_threshold(patched_llm, patched_embedder_and_qdrant, cleanup):
    """Reddit threshold is likes>=5000; with views unused."""
    from app.workers.tasks.viral_tasks import _ingest_platform

    reddit_below = {
        "channel": "reddit", "external_id": f"{TAG}_r_1",
        "title": "low-key thread", "url": "https://r/1",
        "excerpt": "x" * 200, "posted_at": None,
        "engagement": {"likes": 100, "comments": 30}, "meta": {},
    }
    reddit_above = {
        "channel": "reddit", "external_id": f"{TAG}_r_2",
        "title": "blowing up", "url": "https://r/2",
        "excerpt": "y" * 200, "posted_at": None,
        "engagement": {"likes": 9000, "comments": 600}, "meta": {},
    }
    saved = await _ingest_platform("reddit", [reddit_below, reddit_above])
    assert saved == 1

    async with SessionLocal() as db:
        posts = (await db.execute(
            select(ViralPost).where(ViralPost.external_id.like(f"{TAG}_r_%"))
        )).scalars().all()
    assert {p.external_id for p in posts} == {f"{TAG}_r_2"}


@pytest.mark.asyncio
async def test_unknown_platform_threshold_means_nothing_viral(patched_llm, patched_embedder_and_qdrant, cleanup):
    """A platform not in VIRAL_THRESHOLDS has no thresholds → `any()` over empty = False."""
    from app.workers.tasks.viral_tasks import _ingest_platform

    item = {
        "channel": "mastodon", "external_id": f"{TAG}_m_1",
        "title": "huh", "url": "https://m/1",
        "excerpt": "x" * 200, "posted_at": None,
        "engagement": {"likes": 999_999_999}, "meta": {},
    }
    saved = await _ingest_platform("mastodon", [item])
    assert saved == 0, "platforms with no thresholds are never viral"
