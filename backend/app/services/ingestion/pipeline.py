"""End-to-end ingest: extract → chunk → embed → persist (Postgres) + upsert (Qdrant)."""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import log
from app.models.source import Source, SourceChunk
from app.services.ingestion.chunker import chunk_text
from app.services.ingestion.embedder import embed_chunks, upsert_to_qdrant
from app.services.ingestion.extractor import extract
from app.utils.qdrant import brand_sources


async def ingest_source(db: AsyncSession, source_id: UUID) -> dict:
    res = await db.execute(select(Source).where(Source.id == source_id))
    src: Source | None = res.scalar_one_or_none()
    if not src:
        raise LookupError(f"source {source_id} not found")
    src.status = "extracting"
    await db.commit()
    try:
        extracted = extract(
            src.kind,
            url=src.url,
            text=src.raw_text,
            storage_key=src.storage_key,
        )
        src.title = src.title or extracted.title
        src.raw_text = extracted.text
        src.meta = {**(src.meta or {}), **extracted.meta}
        await db.commit()

        chunks = chunk_text(extracted.text)
        if not chunks:
            src.status = "embedded"
            src.meta = {**(src.meta or {}), "chunks": 0}
            await db.commit()
            return {"source_id": str(src.id), "chunks": 0}

        vectors = embed_chunks([c.text for c in chunks])
        collection = brand_sources(str(src.brand_id))
        qdrant_ids = upsert_to_qdrant(
            collection,
            chunks,
            vectors,
            payload_base={
                "source_id": str(src.id),
                "brand_id": str(src.brand_id),
                "kind": src.kind,
                "title": src.title,
            },
        )
        for chunk, qid in zip(chunks, qdrant_ids):
            db.add(
                SourceChunk(
                    source_id=src.id,
                    brand_id=src.brand_id,
                    ord=chunk.ord,
                    text=chunk.text,
                    tokens=chunk.tokens,
                    qdrant_id=qid,
                )
            )
        src.status = "embedded"
        src.meta = {**(src.meta or {}), "chunks": len(chunks), "collection": collection}
        await db.commit()
        log.info("ingest_done", source_id=str(src.id), chunks=len(chunks))
        return {"source_id": str(src.id), "chunks": len(chunks)}
    except Exception as exc:
        log.exception("ingest_failed", source_id=str(src.id))
        src.status = "failed"
        src.error = str(exc)[:1000]
        await db.commit()
        raise
