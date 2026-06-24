"""Embedding pipeline — Voyage primary, OpenAI fallback. Upserts to Qdrant."""
from __future__ import annotations

import uuid
from collections.abc import Iterable

import voyageai
from openai import OpenAI
from qdrant_client.http.models import PointStruct

from app.core.config import settings
from app.services.ingestion.chunker import Chunk
from app.utils.qdrant import VECTOR_SIZE, ensure_collection
from app.utils.qdrant import client as qdrant_client


def _voyage(texts: list[str]) -> list[list[float]]:
    vo = voyageai.Client(api_key=settings.voyage_api_key)
    res = vo.embed(texts, model="voyage-3", input_type="document")
    return res.embeddings


def _openai(texts: list[str]) -> list[list[float]]:
    client = OpenAI(api_key=settings.openai_api_key)
    res = client.embeddings.create(model="text-embedding-3-small", input=texts, dimensions=VECTOR_SIZE)
    return [d.embedding for d in res.data]


def embed_chunks(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    try:
        if settings.voyage_api_key:
            return _voyage(texts)
    except Exception:
        pass
    if settings.openai_api_key:
        return _openai(texts)
    raise RuntimeError("no embedding provider configured")


def upsert_to_qdrant(
    collection: str,
    chunks: Iterable[Chunk],
    vectors: list[list[float]],
    payload_base: dict,
) -> list[str]:
    ensure_collection(collection, size=VECTOR_SIZE)
    points: list[PointStruct] = []
    qdrant_ids: list[str] = []
    for chunk, vec in zip(chunks, vectors, strict=False):
        qid = str(uuid.uuid4())
        qdrant_ids.append(qid)
        payload = {**payload_base, "ord": chunk.ord, "text": chunk.text, "tokens": chunk.tokens}
        points.append(PointStruct(id=qid, vector=vec, payload=payload))
    if points:
        qdrant_client().upsert(collection_name=collection, points=points, wait=True)
    return qdrant_ids
