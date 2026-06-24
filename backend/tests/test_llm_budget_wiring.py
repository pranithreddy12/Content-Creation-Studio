"""Cost-containment wiring tests.

Covers the previously-dead budget system now wired to the LLM chokepoint:
  * budget exhausted → 402 at the API edge (/agents/chat)
  * Celery task fails terminally (no retry storm) on BudgetExceeded
  * CONCURRENCY: N parallel reservations near the cap overshoot by at most one
  * per-account rate limit → 429 after threshold, and FAILS CLOSED if Redis down
  * two tenants have independent budgets
  * workflow loop guard trips at the step and USD ceilings
  * complete() FAILS CLOSED when no billing account is in context
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

os.environ.setdefault("SECRET_KEY", "testtesttesttesttesttesttesttest")

from app.agents.llm_router import LLMResponse, llm_router, set_billing_context  # noqa: E402
from app.api.deps.auth import CurrentUser, current_user  # noqa: E402
from app.db.redis import redis  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models.billing import PlanLimit  # noqa: E402
from app.services.billing import budget  # noqa: E402
from app.services.provisioning import get_or_create_account  # noqa: E402

TAG = f"test_budget_{uuid.uuid4().hex[:8]}"


def _user(suffix: str = "") -> CurrentUser:
    return CurrentUser(
        clerk_user_id=f"{TAG}{suffix}_u",
        clerk_org_id=f"{TAG}{suffix}_o",
        email=f"b{suffix}@test.local",
        role="owner", raw={},
    )


@pytest.fixture()
def auth_as():
    def _set(u: CurrentUser):
        app.dependency_overrides[current_user] = lambda: u
    yield _set
    app.dependency_overrides.pop(current_user, None)


async def _make_capped_account(user: CurrentUser, cap_usd: float):
    """Provision an account on a UNIQUE plan with the given monthly LLM USD cap.

    Using a per-test plan name keeps us off the shared 'free'/'pro' rows so
    other tests aren't disturbed.
    """
    plan_name = f"{TAG}_{uuid.uuid4().hex[:6]}"
    async with SessionLocal() as db:
        acct = await get_or_create_account(db, user)
        await db.execute(text("UPDATE accounts SET plan = :p WHERE id = :a"),
                         {"p": plan_name, "a": acct.id})
        db.add(PlanLimit(plan=plan_name, monthly_llm_usd=cap_usd))
        await db.commit()
        acct_id = acct.id
    # purge any leftover spend key for determinism
    await redis.delete(f"llm_spend:{acct_id}:{datetime.now(timezone.utc).strftime('%Y%m')}")
    return acct_id, plan_name


@pytest.fixture()
async def cleanup():
    yield
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM plan_limits WHERE plan LIKE :p"), {"p": f"{TAG}%"})
        await db.execute(text("DELETE FROM accounts WHERE clerk_org_id LIKE :p"), {"p": f"{TAG}%"})
        await db.commit()


def _canned(cost: float = 0.001) -> LLMResponse:
    return LLMResponse(text="ok", json_out=None, tokens_in=10, tokens_out=10,
                       cost_usd=cost, model="claude-sonnet-4-6", provider="anthropic", latency_ms=5)


# ── 1. 402 at the API edge ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_chat_returns_402_when_budget_exhausted(auth_as, cleanup, monkeypatch):
    user = _user("_402")
    # Cap of 1 cent — the chat reservation (max_tokens=1500 priced as output) exceeds it.
    acct_id, _ = await _make_capped_account(user, 0.01)

    async def fake_call(*a, **k):
        return _canned()
    monkeypatch.setattr(llm_router, "_call", fake_call)

    auth_as(user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.post("/v1/agents/chat", json={"history": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 402, r.text
    assert "cap" in r.json()["detail"].lower()


# ── 2. Celery task is terminal on budget exhaustion ─────────────────

def test_generate_ideas_task_terminal_on_budget(monkeypatch):
    """BudgetExceeded inside the task must NOT propagate (else autoretry storms).

    Sync test on purpose: the task body calls asyncio.run(), which can't run
    inside pytest-asyncio's already-running loop.
    """
    from app.workers.tasks import ideas_tasks

    async def boom(*a, **k):
        raise budget.BudgetExceeded(uuid.uuid4(), 1.0, 99.0)
    monkeypatch.setattr(ideas_tasks, "_generate", boom)

    # Calling the task body directly runs synchronously; must return [] not raise.
    out = ideas_tasks.generate_ideas(str(uuid.uuid4()), str(uuid.uuid4()))
    assert out == []


# ── 3. Concurrency: overshoot by at most one reservation ────────────

@pytest.mark.asyncio
async def test_concurrent_reservations_overshoot_by_at_most_one(cleanup):
    user = _user("_conc")
    cap = 1.00
    estimate = 0.10
    acct_id, _ = await _make_capped_account(user, cap)

    async def one():
        try:
            await budget.reserve(acct_id, estimate)
            return True
        except budget.BudgetExceeded:
            return False

    results = await asyncio.gather(*[one() for _ in range(30)])
    successes = sum(1 for r in results if r)
    reserved_total = successes * estimate
    # Atomic check-and-incr ⇒ total reserved never exceeds cap by more than one unit.
    assert reserved_total <= cap + estimate + 1e-9, f"overshoot too large: {reserved_total}"
    # And it must actually have admitted ~cap/estimate, not zero or all.
    assert 9 <= successes <= 11, f"unexpected admit count {successes}"


# ── 4. Rate limit: 429 threshold + fail-closed ──────────────────────

@pytest.mark.asyncio
async def test_check_rate_raises_after_threshold(cleanup):
    user = _user("_rl")
    acct_id, plan = await _make_capped_account(user, 100.0)
    # default limit for an unknown plan is 10/min
    # clear any bucket
    # fire 10 OK, 11th rejected
    ok = 0
    rejected = False
    for _ in range(11):
        try:
            await budget.check_rate(acct_id, plan=plan)
            ok += 1
        except budget.RateLimited:
            rejected = True
    assert ok == 10
    assert rejected, "11th call must be rate limited"


@pytest.mark.asyncio
async def test_check_rate_fails_closed_when_redis_down(cleanup, monkeypatch):
    user = _user("_rlfail")
    acct_id, plan = await _make_capped_account(user, 100.0)

    async def boom(*a, **k):
        raise ConnectionError("redis down")
    monkeypatch.setattr(redis, "incr", boom)

    with pytest.raises(budget.RateLimited):
        await budget.check_rate(acct_id, plan=plan)


@pytest.mark.asyncio
async def test_rate_limited_maps_to_429_at_edge(auth_as, cleanup, monkeypatch):
    user = _user("_rl429")
    await _make_capped_account(user, 100.0)

    async def boom(*a, **k):
        raise budget.RateLimited(uuid.uuid4(), "rate limit exceeded")
    # Trip the gate regardless of count by making check_rate raise.
    monkeypatch.setattr("app.api.v1.endpoints.agents.check_rate", boom)

    auth_as(user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.post("/v1/agents/chat", json={"history": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 429


# ── 5. Two tenants independent ──────────────────────────────────────

@pytest.mark.asyncio
async def test_two_tenants_have_independent_budgets(cleanup):
    user_a = _user("_ta")
    user_b = _user("_tb")
    acct_a, _ = await _make_capped_account(user_a, 0.10)
    acct_b, _ = await _make_capped_account(user_b, 0.10)

    # Exhaust A
    await budget.reserve(acct_a, 0.10)
    with pytest.raises(budget.BudgetExceeded):
        await budget.reserve(acct_a, 0.10)

    # B is untouched
    reserved = await budget.reserve(acct_b, 0.10)
    assert reserved == 0.10


# ── 6. complete() fails closed without billing context ──────────────

@pytest.mark.asyncio
async def test_complete_fails_closed_without_account(monkeypatch):
    async def fake_call(*a, **k):
        return _canned()
    monkeypatch.setattr(llm_router, "_call", fake_call)
    # Ensure context is clear
    set_billing_context(None, None)
    with pytest.raises(budget.BudgetUnset):
        await llm_router.complete(system="s", user="u", max_tokens=10)


# ── 7. Workflow loop guard ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_workflow_cost_ceiling_trips(cleanup, monkeypatch):
    from app.services.workflow import runner
    from app.models.account import Account
    from app.models.workflow import Workflow

    monkeypatch.setattr(runner, "MAX_RUN_USD", 1.0)

    async def fake_exec(workflow_id, node, node_input):
        return {"ok": 1}, 5.0  # each node "costs" $5 → trips the $1 ceiling
    monkeypatch.setattr(runner, "_exec_node", fake_exec)

    async with SessionLocal() as db:
        acct = Account(clerk_org_id=f"{TAG}_wf_o", name="WF", plan="free")
        db.add(acct); await db.flush()
        wf = Workflow(
            account_id=acct.id, name="loopy",
            definition={"nodes": [{"id": "a", "kind": "agent.writer"}], "edges": []},
            trigger={"kind": "manual", "config": {}},
        )
        db.add(wf); await db.commit()
        wf_id = wf.id

    with pytest.raises(runner.WorkflowCeilingExceeded):
        await runner.run_workflow(wf_id, {})

    async with SessionLocal() as db:
        from app.models.workflow import WorkflowRun
        from sqlalchemy import select
        run = (await db.execute(
            select(WorkflowRun).where(WorkflowRun.workflow_id == wf_id)
        )).scalar_one()
        assert run.status == "failed"


@pytest.mark.asyncio
async def test_workflow_step_ceiling_trips(cleanup, monkeypatch):
    from app.services.workflow import runner
    from app.models.account import Account
    from app.models.workflow import Workflow

    monkeypatch.setattr(runner, "MAX_RUN_STEPS", 1)

    async def fake_exec(workflow_id, node, node_input):
        return {"ok": 1}, 0.0
    monkeypatch.setattr(runner, "_exec_node", fake_exec)

    async with SessionLocal() as db:
        acct = Account(clerk_org_id=f"{TAG}_wf2_o", name="WF2", plan="free")
        db.add(acct); await db.flush()
        # Two chained nodes → second step exceeds MAX_RUN_STEPS=1
        wf = Workflow(
            account_id=acct.id, name="twostep",
            definition={"nodes": [{"id": "a", "kind": "effect.publish"},
                                  {"id": "b", "kind": "effect.publish"}],
                        "edges": [{"source": "a", "target": "b"}]},
            trigger={"kind": "manual", "config": {}},
        )
        db.add(wf); await db.commit()
        wf_id = wf.id

    with pytest.raises(runner.WorkflowCeilingExceeded):
        await runner.run_workflow(wf_id, {})
