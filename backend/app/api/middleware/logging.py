import time

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

log = structlog.get_logger()


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        rid = getattr(request.state, "request_id", "-")
        structlog.contextvars.bind_contextvars(request_id=rid, path=request.url.path, method=request.method)
        try:
            response = await call_next(request)
        except Exception:
            log.exception("request_failed")
            raise
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        log.info("request", status=response.status_code, duration_ms=duration_ms)
        structlog.contextvars.clear_contextvars()
        return response
