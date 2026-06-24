import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.db.redis import redis

WINDOW_SEC = 60
# Coarse global per-IP ceiling, fails OPEN if Redis is down (acceptable for cheap
# endpoints). Per-account, fail-CLOSED spend gates for LLM paths live in
# app.services.billing.budget.check_rate and are applied in the agent endpoints.
LIMIT_PER_MIN = 600
EXEMPT_PREFIXES = ("/health", "/metrics", "/docs", "/openapi", "/")


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in EXEMPT_PREFIXES or any(path.startswith(p) for p in ("/metrics",)):
            return await call_next(request)
        ip = request.client.host if request.client else "anon"
        bucket = f"rl:{ip}:{int(time.time() // WINDOW_SEC)}"
        try:
            count = await redis.incr(bucket)
            if count == 1:
                await redis.expire(bucket, WINDOW_SEC)
            if count > LIMIT_PER_MIN:
                return JSONResponse({"error": "rate_limited"}, status_code=429)
        except Exception:
            pass
        return await call_next(request)
