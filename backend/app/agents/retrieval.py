"""RAG helpers — retrieve brand sources + viral patterns from Qdrant."""
from __future__ import annotations

from qdrant_client.http.models import Filter, FieldCondition, MatchValue

from app.services.ingestion.embedder import embed_chunks
from app.utils.qdrant import aclient, brand_sources


async def retrieve_brand_context(brand_id: str, query: str, top_k: int = 12) -> list[dict]:
    if not query:
        return []
    [vec] = embed_chunks([query])
    res = await aclient().search(
        collection_name=brand_sources(brand_id),
        query_vector=vec,
        limit=top_k,
        score_threshold=0.30,
        with_payload=True,
    )
    return [{"score": p.score, **(p.payload or {})} for p in res]


async def retrieve_viral_patterns(query: str, platform: str | None = None, top_k: int = 8) -> list[dict]:
    if not query:
        return []
    [vec] = embed_chunks([query])
    flt: Filter | None = None
    if platform:
        flt = Filter(must=[FieldCondition(key="platform", match=MatchValue(value=platform))])
    res = await aclient().search(
        collection_name="viral_patterns",
        query_vector=vec,
        limit=top_k,
        score_threshold=0.30,
        with_payload=True,
        query_filter=flt,
    )
    return [{"score": p.score, **(p.payload or {})} for p in res]
