"""Rate-limit middleware test.

Pre-seeds the Redis bucket key just below the limit, then verifies an
additional request flips the bucket over the threshold and the middleware
returns HTTP 429.

Health endpoint is exempt (EXEMPT_PREFIXES), so we hit /v1/brands which is
gated. An unauth call would normally return 401, but rate-limit middleware
sits OUTSIDE auth — its 429 wins.
"""
from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("SECRET_KEY", "testtesttesttesttesttesttesttest")

from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.api.middleware.rate_limit import LIMIT_PER_MIN, WINDOW_SEC  # noqa: E402
from app.db.redis import redis  # noqa: E402


@pytest.mark.asyncio
async def test_rate_limit_returns_429_when_exceeded():
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Pre-seed the bucket to just below the limit. The middleware computes the
        # IP as request.client.host; ASGITransport uses "127.0.0.1".
        window = int(time.time() // WINDOW_SEC)
        bucket = f"rl:127.0.0.1:{window}"
        await redis.set(bucket, str(LIMIT_PER_MIN - 1), ex=WINDOW_SEC)

        # First call brings the counter to LIMIT_PER_MIN — still allowed.
        r1 = await client.get("/v1/brands")
        assert r1.status_code != 429, f"expected pass-through, got {r1.status_code}"

        # Second call exceeds — must 429.
        r2 = await client.get("/v1/brands")
        assert r2.status_code == 429, f"expected 429, got {r2.status_code}"
        body = r2.json()
        assert body == {"error": "rate_limited"}

        # Cleanup so we don't poison subsequent tests.
        await redis.delete(bucket)


@pytest.mark.asyncio
async def test_health_is_exempt_from_rate_limit():
    """Even when the bucket would say 'rate_limited', /health must always answer 200."""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        window = int(time.time() // WINDOW_SEC)
        bucket = f"rl:127.0.0.1:{window}"
        await redis.set(bucket, str(LIMIT_PER_MIN * 10), ex=WINDOW_SEC)
        try:
            r = await client.get("/health")
            assert r.status_code == 200
            assert r.json()["ok"] is True
        finally:
            await redis.delete(bucket)
