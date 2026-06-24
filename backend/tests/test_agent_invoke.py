"""POST /v1/agents/invoke — on-demand single-agent runs.

Covers:
  - happy path with a stubbed agent (no real LLM call)
  - unknown agent name → 400 (production bug #6 — was 500 via uncaught KeyError)
  - brand cross-tenant addressing → 404 (no existence side channel)
  - missing brand → 404
  - unauthenticated → 401
  - agent_runs row is persisted after success
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text

os.environ.setdefault("SECRET_KEY", "testtesttesttesttesttesttesttest")

from app.agents.base import AgentContext, AgentResult  # noqa: E402
from app.api.deps.auth import CurrentUser, current_user  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models.account import Account, Workspace  # noqa: E402
from app.models.agent import AgentRun  # noqa: E402
from app.models.brand import Brand  # noqa: E402

TAG = f"test_invoke_{uuid.uuid4().hex[:8]}"
SLUG = TAG.replace("_", "-")


def _user(suffix: str = "") -> CurrentUser:
    return CurrentUser(
        clerk_user_id=f"{TAG}{suffix}_u",
        clerk_org_id=f"{TAG}{suffix}_o",
        email=f"i{suffix}@test.local",
        role="owner", raw={},
    )


@pytest.fixture()
def auth_as():
    def _set(u: CurrentUser):
        app.dependency_overrides[current_user] = lambda: u
    yield _set
    app.dependency_overrides.pop(current_user, None)


@pytest.fixture()
async def cleanup():
    yield
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM accounts WHERE clerk_org_id LIKE :p"),
                         {"p": f"{TAG}%"})
        await db.commit()


@pytest.fixture()
async def brand_for_user():
    """Create an account + brand owned by the default user, returning brand_id."""
    user = _user()
    from app.services.provisioning import get_or_create_account
    async with SessionLocal() as db:
        acct = await get_or_create_account(db, user)
        ws_id = (await db.execute(
            text("SELECT id FROM workspaces WHERE account_id = :a LIMIT 1"),
            {"a": acct.id},
        )).scalar_one()
        brand = Brand(account_id=acct.id, workspace_id=ws_id,
                      name="MyBrand", slug=f"{SLUG}-b"[:60], primary_topic="AI")
        db.add(brand); await db.commit()
        yield {"brand_id": brand.id, "account_id": acct.id}


@pytest.fixture()
def stub_writer(monkeypatch):
    """Replace WriterAgent.run with a no-LLM stub."""
    from app.agents import writer as writer_mod

    async def fake_run(self, ctx: AgentContext) -> AgentResult:
        return AgentResult(
            output={"draft": "Stubbed body text.", "title": "Stub title"},
            tokens_in=10, tokens_out=20, cost_usd=0.0001,
            model="claude-sonnet-4-6", provider="anthropic",
        )
    monkeypatch.setattr(writer_mod.WriterAgent, "run", fake_run)


@pytest.mark.asyncio
async def test_invoke_writer_returns_provenance_and_output(auth_as, cleanup, brand_for_user, stub_writer):
    auth_as(_user())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.post("/v1/agents/invoke", json={
            "agent": "writer",
            "brand_id": str(brand_for_user["brand_id"]),
            "inputs": {"prompt": "draft a tweet"},
        })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["agent"] == "writer"
    assert body["output"]["title"] == "Stub title"
    assert body["model"] == "claude-sonnet-4-6"
    assert body["provider"] == "anthropic"
    assert body["tokens_in"] == 10
    assert body["tokens_out"] == 20
    assert body["cost_usd"] == 0.0001


@pytest.mark.asyncio
async def test_invoke_persists_agent_run_row(auth_as, cleanup, brand_for_user, stub_writer):
    """After a successful invoke, an AgentRun row should exist for this account."""
    auth_as(_user())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        await cx.post("/v1/agents/invoke", json={
            "agent": "writer",
            "brand_id": str(brand_for_user["brand_id"]),
            "inputs": {},
        })

    async with SessionLocal() as db:
        count = (await db.execute(
            select(func.count()).select_from(AgentRun).where(
                AgentRun.account_id == brand_for_user["account_id"],
                AgentRun.agent_name == "writer",
            )
        )).scalar()
    assert count == 1, f"expected 1 agent_runs row, got {count}"


@pytest.mark.asyncio
async def test_invoke_unknown_agent_name_returns_400(auth_as, cleanup, brand_for_user):
    """Production-bug coverage: previously raised KeyError → 500. Must now be 400."""
    auth_as(_user())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.post("/v1/agents/invoke", json={
            "agent": "nonexistent_agent",
            "brand_id": str(brand_for_user["brand_id"]),
            "inputs": {},
        })
    assert r.status_code == 400
    assert "unknown agent" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_invoke_missing_brand_returns_404(auth_as, cleanup):
    user = _user("_nobrand")
    auth_as(user)
    fake_brand = uuid.uuid4()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.post("/v1/agents/invoke", json={
            "agent": "writer",
            "brand_id": str(fake_brand),
            "inputs": {},
        })
    assert r.status_code == 404
    assert "brand not found" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_invoke_cross_tenant_brand_returns_404(auth_as, cleanup, brand_for_user, stub_writer):
    """User B trying to invoke an agent against User A's brand_id must NOT succeed."""
    other = _user("_other")
    async with SessionLocal() as db:
        acct_b = Account(clerk_org_id=other.clerk_org_id, name="OB", plan="free")
        db.add(acct_b); await db.commit()

    auth_as(other)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.post("/v1/agents/invoke", json={
            "agent": "writer",
            "brand_id": str(brand_for_user["brand_id"]),
            "inputs": {},
        })
    assert r.status_code == 404, "cross-tenant brand access must not leak the agent run"


@pytest.mark.asyncio
async def test_invoke_unauthenticated_returns_401(cleanup):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.post("/v1/agents/invoke", json={
            "agent": "writer",
            "brand_id": str(uuid.uuid4()),
            "inputs": {},
        })
    assert r.status_code == 401
