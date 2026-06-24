from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import make_asgi_app

from app.api.middleware.logging import LoggingMiddleware
from app.api.middleware.metrics import MetricsMiddleware
from app.api.middleware.rate_limit import RateLimitMiddleware
from app.api.middleware.request_id import RequestIDMiddleware
from app.api.middleware.security_headers import SecurityHeadersMiddleware
from app.api.v1 import router as v1_router
from app.core import metrics  # noqa: F401  -- registers business metrics
from app.core.config import settings
from app.core.logging import configure_logging
from app.core.logging import log as _log
from app.core.observability import init_sentry
from app.db.session import dispose_engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    init_sentry()
    try:
        from app.services.billing import seed_plan_limits
        await seed_plan_limits()  # idempotent; ensures spend caps exist for every plan tier
    except Exception as exc:  # never block startup on seed (e.g. mid-migration)
        _log.warning("plan_limits_seed_skipped", err=str(exc)[:200])
    yield
    await dispose_engine()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url=None,
)

# Starlette wraps middleware in REVERSE-add order: the last added is outermost.
# RequestIDMiddleware must be OUTER of LoggingMiddleware so request.state.request_id
# is set before LoggingMiddleware reads it.
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(LoggingMiddleware)
app.add_middleware(MetricsMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,  # we use bearer JWT in Authorization header, not cookies
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["x-request-id"],
)

app.include_router(v1_router, prefix="/v1")
app.mount("/metrics", make_asgi_app())


from app.services.billing import BudgetExceeded, RateLimited  # noqa: E402


@app.exception_handler(BudgetExceeded)
async def budget_exceeded_handler(_: Request, exc: BudgetExceeded):
    return JSONResponse({"detail": str(exc)}, status_code=402)


@app.exception_handler(RateLimited)
async def rate_limited_handler(_: Request, exc: RateLimited):
    return JSONResponse({"detail": str(exc)}, status_code=429)


@app.exception_handler(Exception)
async def unhandled_exception(_: Request, exc: Exception):
    _log.exception("unhandled_exception", err=str(exc)[:300])
    return JSONResponse({"detail": "internal server error", "error": str(exc)[:300]}, status_code=500)


@app.get("/health", tags=["meta"])
async def health():
    return {"ok": True, "service": settings.app_name, "env": settings.app_env}


@app.get("/", tags=["meta"])
async def root():
    return {"name": settings.app_name, "version": "0.1.0", "docs": "/docs"}
