from fastapi import APIRouter

from app.api.v1.endpoints import (
    agents,
    analytics,
    assets,
    auth,
    billing,
    brands,
    calendar,
    ideas,
    media,
    notifications,
    publishing,
    schedules,
    sources,
    webhooks,
    workflows,
)

router = APIRouter()
router.include_router(auth.router,        prefix="/auth",        tags=["auth"])
router.include_router(brands.router,      prefix="/brands",      tags=["brands"])
router.include_router(sources.router,     prefix="/sources",     tags=["sources"])
router.include_router(ideas.router,       prefix="/ideas",       tags=["ideas"])
router.include_router(assets.router,      prefix="/assets",      tags=["assets"])
router.include_router(media.router,       prefix="/media",       tags=["media"])
router.include_router(schedules.router,   prefix="/schedules",   tags=["schedules"])
router.include_router(publishing.router,  prefix="/publishing",  tags=["publishing"])
router.include_router(workflows.router,   prefix="/workflows",   tags=["workflows"])
router.include_router(analytics.router,   prefix="/analytics",   tags=["analytics"])
router.include_router(calendar.router,    prefix="/calendar",    tags=["calendar"])
router.include_router(webhooks.router,    prefix="/webhooks",    tags=["webhooks"])
router.include_router(agents.router,      prefix="/agents",      tags=["agents"])
router.include_router(billing.router,     prefix="/billing",     tags=["billing"])
router.include_router(notifications.router, prefix="/notifications", tags=["notifications"])
