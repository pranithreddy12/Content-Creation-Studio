"""Pytest fixtures — async test client + in-memory dependency overrides."""
from __future__ import annotations

import os
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://studio:studio@localhost:5432/studio_test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("SECRET_KEY", "testtesttesttesttesttesttesttest")


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest_asyncio.fixture()
async def app_client():
    from app.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
