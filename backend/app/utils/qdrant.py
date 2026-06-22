from functools import lru_cache

from qdrant_client import AsyncQdrantClient, QdrantClient
from qdrant_client.http.models import Distance, VectorParams

from app.core.config import settings

VECTOR_SIZE = 1024  # Voyage 3 / OpenAI text-embedding-3-small (truncated)


@lru_cache
def client() -> QdrantClient:
    return QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)


@lru_cache
def aclient() -> AsyncQdrantClient:
    return AsyncQdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)


def ensure_collection(name: str, size: int = VECTOR_SIZE) -> None:
    c = client()
    if not c.collection_exists(name):
        c.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=size, distance=Distance.COSINE),
        )


def brand_sources(brand_id: str) -> str:
    return f"brand_{brand_id.replace('-', '')}_sources"


def brand_assets(brand_id: str) -> str:
    return f"brand_{brand_id.replace('-', '')}_assets"
