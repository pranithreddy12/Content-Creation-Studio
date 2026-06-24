"""End-to-end ingest pipeline test.

Mocks `extract` (so no real URL is fetched) and `embed_chunks` (so no LLM is
called), then runs the full `services.ingestion.pipeline.ingest_source(...)`
inside a real Postgres + Qdrant container pair. Asserts:

  * Source row transitions pending → extracting → embedded
  * SourceChunk rows materialize with deterministic ord + tokens
  * Each chunk got upserted into Qdrant with the right brand-scoped collection
  * Failure path: extractor raising marks Source.status = failed with error
"""
from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone

import pytest

os.environ.setdefault("SECRET_KEY", "testtesttesttesttesttesttesttest")
os.environ.setdefault("QDRANT_URL", "http://qdrant:6333")

from qdrant_client.http.exceptions import UnexpectedResponse  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.account import Account, Workspace  # noqa: E402
from app.models.brand import Brand  # noqa: E402
from app.models.source import Source, SourceChunk  # noqa: E402
from app.services.ingestion.extractor import Extracted  # noqa: E402
from app.services.ingestion.pipeline import ingest_source  # noqa: E402
from app.utils.qdrant import VECTOR_SIZE, brand_sources, client as qdrant_client  # noqa: E402

TAG = f"test_ingest_{uuid.uuid4().hex[:8]}"


def _vec(seed: str) -> list[float]:
    """Deterministic 1024-dim normalized vector."""
    digest = hashlib.sha256(seed.encode()).digest()
    raw = [(b - 128) / 128.0 for b in digest]
    vec = (raw * ((VECTOR_SIZE // len(raw)) + 1))[:VECTOR_SIZE]
    n = (sum(v * v for v in vec)) ** 0.5 or 1.0
    return [v / n for v in vec]


@pytest.fixture()
async def source_row():
    """Provision Account / Workspace / Brand / Source."""
    async with SessionLocal() as db:
        acct = Account(clerk_org_id=f"{TAG}_org", name="Ing", plan="free")
        db.add(acct); await db.flush()
        ws = Workspace(account_id=acct.id, name="Default")
        db.add(ws); await db.flush()
        brand = Brand(
            account_id=acct.id, workspace_id=ws.id,
            name="IngBrand", slug=f"{TAG}-i"[:60], primary_topic="AI",
        )
        db.add(brand); await db.flush()
        src = Source(
            account_id=acct.id, brand_id=brand.id, kind="topic",
            raw_text="placeholder", status="pending",
        )
        db.add(src); await db.commit()
        ctx = {"source_id": src.id, "brand_id": brand.id}

    yield ctx

    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM accounts WHERE clerk_org_id = :o"), {"o": f"{TAG}_org"})
        await db.commit()
    # Also drop the brand's Qdrant collection
    try:
        qdrant_client().delete_collection(brand_sources(str(ctx["brand_id"])))
    except Exception:
        pass


@pytest.fixture()
def patched_pipeline(monkeypatch):
    """Mock the extractor and embedder so the test doesn't hit external services."""
    from app.services.ingestion import pipeline as pipe_mod

    def fake_extract(kind, *, url=None, text=None, storage_key=None):
        # The pipeline expects an Extracted with `text` long enough to chunk into 3+ pieces.
        sentences = [
            "AI agents are reshaping how teams build software.",
            "Vector search lets retrieval scale to billions of chunks.",
            "Prompt versioning is essential for reproducibility.",
            "Observability turns LLM costs into a manageable line item.",
            "Background workers decouple slow generation from request latency.",
            "Cross-tenant isolation must be enforced in every query.",
            "Caching brand memory cuts redundant embedding calls.",
            "Quality gates reduce hallucination in production output.",
        ]
        return Extracted(title="Mock title", text=" ".join(sentences), meta={"mock": True})

    monkeypatch.setattr(pipe_mod, "extract", fake_extract)

    def fake_embed_chunks(texts):
        return [_vec(t) for t in texts]

    monkeypatch.setattr(pipe_mod, "embed_chunks", fake_embed_chunks)
    yield


@pytest.mark.asyncio
async def test_ingest_persists_chunks_and_upserts_to_qdrant(source_row, patched_pipeline):
    async with SessionLocal() as db:
        result = await ingest_source(db, source_row["source_id"])

    assert result["source_id"] == str(source_row["source_id"])
    assert result["chunks"] >= 1

    async with SessionLocal() as db:
        src = (await db.execute(select(Source).where(Source.id == source_row["source_id"]))).scalar_one()
        assert src.status == "embedded"
        assert src.title == "Mock title"
        assert src.meta.get("chunks") == result["chunks"]
        assert src.meta.get("collection") == brand_sources(str(source_row["brand_id"]))

        chunks = (await db.execute(
            select(SourceChunk).where(SourceChunk.source_id == source_row["source_id"])
            .order_by(SourceChunk.ord.asc())
        )).scalars().all()
        assert len(chunks) == result["chunks"]
        assert [c.ord for c in chunks] == list(range(len(chunks)))
        assert all(c.tokens > 0 for c in chunks)
        assert all(c.qdrant_id is not None for c in chunks)

    # Confirm the points landed in Qdrant.
    collection = brand_sources(str(source_row["brand_id"]))
    res = qdrant_client().search(
        collection_name=collection,
        query_vector=_vec("AI agents are reshaping how teams build software."),
        limit=1, with_payload=True,
    )
    assert res, "qdrant search returned nothing"
    assert res[0].payload["source_id"] == str(source_row["source_id"])
    assert res[0].payload["brand_id"] == str(source_row["brand_id"])
    assert res[0].payload["kind"] == "topic"


@pytest.mark.asyncio
async def test_ingest_marks_source_failed_when_extractor_raises(source_row, monkeypatch):
    from app.services.ingestion import pipeline as pipe_mod

    def boom(kind, **kw):
        raise ValueError("network kaboom")

    monkeypatch.setattr(pipe_mod, "extract", boom)

    async with SessionLocal() as db:
        with pytest.raises(ValueError, match="network kaboom"):
            await ingest_source(db, source_row["source_id"])

    async with SessionLocal() as db:
        src = (await db.execute(select(Source).where(Source.id == source_row["source_id"]))).scalar_one()
        assert src.status == "failed"
        assert "network kaboom" in (src.error or "")


@pytest.mark.asyncio
async def test_ingest_short_text_records_zero_chunks(source_row, monkeypatch):
    from app.services.ingestion import pipeline as pipe_mod

    monkeypatch.setattr(pipe_mod, "extract",
                        lambda *a, **k: Extracted(title="t", text="", meta={}))

    async with SessionLocal() as db:
        result = await ingest_source(db, source_row["source_id"])
    assert result["chunks"] == 0

    async with SessionLocal() as db:
        src = (await db.execute(select(Source).where(Source.id == source_row["source_id"]))).scalar_one()
        assert src.status == "embedded"
        assert src.meta.get("chunks") == 0
