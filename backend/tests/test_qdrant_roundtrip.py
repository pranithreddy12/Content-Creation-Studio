"""Integration test: full Qdrant round-trip without depending on an external LLM.

We construct deterministic 1024-dim "embeddings" by hashing the input text into
a normalized float vector, push them via `ensure_collection` + `upsert`, then
search and assert the right point comes back.

If the Qdrant container isn't reachable, the test is skipped (so the suite
still passes in environments where infra is partially up).
"""
from __future__ import annotations

import hashlib
import os
import uuid
from typing import Iterable

import pytest

os.environ.setdefault("SECRET_KEY", "testtesttesttesttesttesttesttest")
os.environ.setdefault("QDRANT_URL", "http://qdrant:6333")

from qdrant_client.http.exceptions import UnexpectedResponse  # noqa: E402
from qdrant_client.http.models import PointStruct  # noqa: E402

from app.utils.qdrant import VECTOR_SIZE, client as qdrant_client, ensure_collection  # noqa: E402


def _fake_vector(text: str, dim: int = VECTOR_SIZE) -> list[float]:
    """Deterministic vector built from SHA-256 digest, scaled to [-1, 1]."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    # Tile the 32-byte digest up to `dim` floats and L2-normalize.
    raw = [(b - 128) / 128.0 for b in digest]
    vec = (raw * ((dim // len(raw)) + 1))[:dim]
    norm = (sum(v * v for v in vec)) ** 0.5 or 1.0
    return [v / norm for v in vec]


@pytest.fixture(scope="module")
def collection() -> Iterable[str]:
    name = f"test_qdrant_{uuid.uuid4().hex[:8]}"
    try:
        ensure_collection(name)
    except Exception as exc:
        pytest.skip(f"Qdrant unreachable: {exc}")
    yield name
    try:
        qdrant_client().delete_collection(name)
    except Exception:
        pass


def test_upsert_then_search_returns_self(collection: str) -> None:
    docs = {
        "a": "the cat sat on the mat",
        "b": "kubernetes is a container orchestrator",
        "c": "neural networks learn from data",
    }
    points = [
        PointStruct(id=str(uuid.uuid4()), vector=_fake_vector(text),
                    payload={"key": k, "text": text})
        for k, text in docs.items()
    ]
    qdrant_client().upsert(collection_name=collection, points=points, wait=True)

    # Searching for the exact "b" vector should rank the "b" document first.
    res = qdrant_client().search(
        collection_name=collection,
        query_vector=_fake_vector(docs["b"]),
        limit=3,
        with_payload=True,
    )
    assert len(res) >= 1
    assert res[0].payload["key"] == "b", f"top hit was {res[0].payload}"
    # And the exact match should have a near-1 cosine score.
    assert res[0].score > 0.99


def test_search_with_score_threshold(collection: str) -> None:
    res = qdrant_client().search(
        collection_name=collection,
        query_vector=_fake_vector("the cat sat on the mat"),
        limit=5,
        score_threshold=0.99,
        with_payload=True,
    )
    keys = {p.payload["key"] for p in res}
    assert "a" in keys


def test_collection_isolation(collection: str) -> None:
    """A point added to a different collection must not appear when searching `collection`."""
    other = f"test_qdrant_other_{uuid.uuid4().hex[:8]}"
    ensure_collection(other)
    try:
        qdrant_client().upsert(
            collection_name=other,
            points=[PointStruct(id=str(uuid.uuid4()),
                                vector=_fake_vector("isolated"),
                                payload={"key": "iso"})],
            wait=True,
        )
        res = qdrant_client().search(
            collection_name=collection,
            query_vector=_fake_vector("isolated"),
            limit=5,
            with_payload=True,
        )
        assert "iso" not in {p.payload.get("key") for p in res}
    finally:
        try:
            qdrant_client().delete_collection(other)
        except Exception:
            pass
