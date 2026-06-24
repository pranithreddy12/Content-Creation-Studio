"""Per-account LLM spend control: atomic reservation, reconciliation, rate limit.

Design (see cost-containment pass):
  * Caps come from `plan_limits.monthly_llm_usd` for the account's plan (DB).
  * A per-account, per-calendar-month Redis counter tracks *reserved* spend.
  * reserve() atomically INCRBYFLOAT a conservative estimate and checks the
    returned total against the cap in the same round trip — so N concurrent
    callers near the cap overshoot by at most one reservation, not N.
  * reconcile() adjusts the counter by (actual - estimate) after the call.
  * The durable source of truth stays `usage_events` (via usage.meter).

Rate limiting here is a per-account token bucket that FAILS CLOSED: if Redis is
unavailable the call is rejected, because these gates guard real money.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select

from app.core.logging import log
from app.db.redis import redis
from app.db.session import SessionLocal
from app.models.account import Account
from app.models.billing import PlanLimit
from app.services.billing.usage import current_usage, meter

# Spend keys live ~40 days so a month boundary always has a fresh key.
_SPEND_TTL_SEC = 40 * 24 * 3600

# Sentinel for platform-internal LLM usage that is NOT attributable to a tenant
# (e.g. the cross-tenant viral-pattern crawler). Treated as unlimited + unmetered.
# RESIDUAL RISK: platform-internal spend is not yet tracked against any ledger.
SYSTEM_ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000000")

# Per-minute request ceilings per plan for LLM-spend endpoints. (USD caps live
# in plan_limits; request-rate caps are operational, not billing, so they stay here.)
PLAN_RATE_PER_MIN: dict[str, int] = {"free": 10, "pro": 60, "agency": 240}
_DEFAULT_RATE_PER_MIN = 10


class BudgetError(Exception):
    """Base for cost-control failures — caught at API edge + Celery boundary."""


class BudgetUnset(BudgetError):
    """No billing account in context — fail closed, never proceed unmetered."""


class BudgetExceeded(BudgetError):
    def __init__(self, account_id: UUID, cap: float, attempted: float) -> None:
        self.account_id = account_id
        self.cap = cap
        self.attempted = attempted
        super().__init__(
            f"monthly LLM spend cap reached for account {account_id} "
            f"(${attempted:.4f} would exceed ${cap:.2f})"
        )


class RateLimited(BudgetError):
    def __init__(self, account_id: UUID, detail: str = "rate limited") -> None:
        self.account_id = account_id
        super().__init__(detail)


def _month() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m")


def _spend_key(account_id: UUID) -> str:
    return f"llm_spend:{account_id}:{_month()}"


async def cap_for_account(account_id: UUID) -> float | None:
    """Return the account's monthly USD cap, or None for unlimited / no plan row."""
    async with SessionLocal() as db:
        acct = (await db.execute(
            select(Account).where(Account.id == account_id)
        )).scalar_one_or_none()
        if acct is None:
            return None
        limits = (await db.execute(
            select(PlanLimit).where(PlanLimit.plan == acct.plan)
        )).scalar_one_or_none()
    if limits is None or limits.monthly_llm_usd is None:
        return None
    return float(limits.monthly_llm_usd)


async def _db_month_usage_usd(account_id: UUID) -> float:
    month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    async with SessionLocal() as db:
        usage = await current_usage(db, account_id, since=month_start)
    return float(usage.get("llm_usd", 0.0))


async def reserve(account_id: UUID, estimate_usd: float) -> float:
    """Atomically reserve `estimate_usd` against the monthly cap.

    Returns the reserved amount (0.0 if the account is unlimited). Raises
    BudgetExceeded if the reservation would cross the cap — and rolls the
    reservation back so a rejected call doesn't permanently consume budget.
    """
    if account_id == SYSTEM_ACCOUNT_ID:
        return 0.0  # platform-internal usage — unmetered by design
    cap = await cap_for_account(account_id)
    if cap is None:
        return 0.0  # unlimited plan — nothing to reserve
    key = _spend_key(account_id)
    # Seed the counter from durable usage the first time we touch this month's key
    # so a Redis restart mid-month doesn't reset a tenant's spend to zero.
    if not await redis.exists(key):
        seed = await _db_month_usage_usd(account_id)
        await redis.set(key, seed, nx=True, ex=_SPEND_TTL_SEC)
    new_total = float(await redis.incrbyfloat(key, estimate_usd))
    await redis.expire(key, _SPEND_TTL_SEC)
    if new_total > cap:
        await redis.incrbyfloat(key, -estimate_usd)  # roll back the rejected reservation
        raise BudgetExceeded(account_id, cap, new_total)
    return estimate_usd


async def reconcile(account_id: UUID, estimate_usd: float, actual_usd: float) -> None:
    """Adjust the reserved counter by the estimate↔actual delta after a call."""
    cap = await cap_for_account(account_id)
    if cap is None:
        return
    delta = actual_usd - estimate_usd
    if delta:
        try:
            await redis.incrbyfloat(_spend_key(account_id), delta)
        except Exception:  # reconciliation is best-effort; durable meter is source of truth
            log.warning("budget_reconcile_failed", account_id=str(account_id))


async def record_actual(account_id: UUID, brand_id: UUID | None, actual_usd: float) -> None:
    """Write the durable UsageEvent for an LLM call (source of truth for billing)."""
    if account_id == SYSTEM_ACCOUNT_ID:
        return  # platform-internal usage — no tenant ledger entry
    async with SessionLocal() as db:
        await meter(db, account_id=account_id, brand_id=brand_id, kind="llm_usd", amount=actual_usd)


async def check_rate(account_id: UUID, *, plan: str | None = None, window_sec: int = 60) -> None:
    """Per-account token bucket. FAILS CLOSED: Redis errors reject the request."""
    limit = PLAN_RATE_PER_MIN.get(plan or "", _DEFAULT_RATE_PER_MIN)
    bucket = f"rl:acct:{account_id}:{int(time.time() // window_sec)}"
    try:
        count = await redis.incr(bucket)
        if count == 1:
            await redis.expire(bucket, window_sec)
    except Exception as exc:
        # These gates guard real spend — if we can't count, we don't allow.
        raise RateLimited(account_id, "rate limiter unavailable") from exc
    if count > limit:
        raise RateLimited(account_id, f"rate limit exceeded ({limit}/min)")


# ── plan_limits seed (idempotent) ───────────────────────────────────
# Defaults established for this pass — plan_limits had no seed anywhere.
DEFAULT_PLAN_LIMITS = {
    "free":   {"max_brands": 1,  "max_workspaces": 1,  "monthly_assets": 30,
               "monthly_video_minutes": 5,    "monthly_publish_ops": 60,    "monthly_llm_usd": 5.00},
    "pro":    {"max_brands": 5,  "max_workspaces": 3,  "monthly_assets": 500,
               "monthly_video_minutes": 120,  "monthly_publish_ops": 2000,  "monthly_llm_usd": 100.00},
    "agency": {"max_brands": 50, "max_workspaces": 20, "monthly_assets": 5000,
               "monthly_video_minutes": 1200, "monthly_publish_ops": 20000, "monthly_llm_usd": 500.00},
}


async def seed_plan_limits() -> None:
    """Insert default plan_limits rows for any plan tier missing one. Idempotent."""
    from sqlalchemy import select
    async with SessionLocal() as db:
        for plan, vals in DEFAULT_PLAN_LIMITS.items():
            exists = (await db.execute(
                select(PlanLimit).where(PlanLimit.plan == plan)
            )).scalar_one_or_none()
            if exists is None:
                db.add(PlanLimit(plan=plan, **vals))
        await db.commit()
