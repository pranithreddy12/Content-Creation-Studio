"""Agent chat endpoint (`POST /v1/agents/chat`) — used by mobile + dashboard assistant."""
from __future__ import annotations

import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

os.environ.setdefault("SECRET_KEY", "testtesttesttesttesttesttesttest")

from app.agents.llm_router import LLMResponse  # noqa: E402
from app.api.deps.auth import CurrentUser, current_user  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402

TAG = f"test_chat_{uuid.uuid4().hex[:8]}"


def _user() -> CurrentUser:
    return CurrentUser(
        clerk_user_id=f"{TAG}_u",
        clerk_org_id=f"{TAG}_o",
        email="c@test.local",
        role="owner", raw={},
    )


@pytest.fixture()
def auth_as_user():
    u = _user()
    app.dependency_overrides[current_user] = lambda: u
    yield u
    app.dependency_overrides.pop(current_user, None)


@pytest.fixture()
async def cleanup():
    yield
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM accounts WHERE clerk_org_id = :o"), {"o": f"{TAG}_o"})
        await db.commit()


@pytest.fixture()
def patched_llm(monkeypatch):
    """Capture every call to llm_router.complete + return a canned reply."""
    calls = []

    async def fake(*, system, user, **kw):
        calls.append({"system": system, "user": user, "kw": kw})
        return LLMResponse(
            text="That's a great question. Here are three angles to consider …",
            json_out=None,
            tokens_in=42, tokens_out=88, cost_usd=0.0015,
            model="claude-sonnet-4-6", provider="anthropic", latency_ms=180,
        )

    from app.agents.llm_router import llm_router as router
    monkeypatch.setattr(router, "complete", fake)
    return calls


@pytest.mark.asyncio
async def test_chat_happy_path_returns_reply_with_provenance(auth_as_user, patched_llm, cleanup):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.post("/v1/agents/chat", json={
            "history": [
                {"role": "user", "content": "What should I post next week about AI agents?"},
            ],
        })
    assert r.status_code == 200
    body = r.json()
    assert "great question" in body["reply"]
    assert body["model"] == "claude-sonnet-4-6"
    assert body["provider"] == "anthropic"


@pytest.mark.asyncio
async def test_chat_passes_last_12_messages_into_user_prompt(auth_as_user, patched_llm, cleanup):
    """The endpoint should slice history to the last 12 messages and join with role tags."""
    history = [
        {"role": "user", "content": f"msg {i}"} for i in range(20)
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.post("/v1/agents/chat", json={"history": history})
    assert r.status_code == 200
    user_prompt = patched_llm[0]["user"]
    # Only the last 12 messages should appear; "msg 8" and later are inside the slice.
    assert "msg 19" in user_prompt
    assert "msg 8" in user_prompt
    assert "msg 7" not in user_prompt, "earlier messages should be dropped from the window"


@pytest.mark.asyncio
async def test_chat_rejects_empty_history(auth_as_user, cleanup):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.post("/v1/agents/chat", json={"history": []})
    assert r.status_code == 400
    assert "empty history" in r.json()["detail"]


@pytest.mark.asyncio
async def test_chat_rejects_history_without_any_user_message(auth_as_user, cleanup):
    """Only assistant or system messages → 400, the endpoint can't find a user turn."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.post("/v1/agents/chat", json={"history": [
            {"role": "assistant", "content": "Hi there."},
        ]})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_chat_returns_502_when_llm_fails(auth_as_user, monkeypatch, cleanup):
    async def boom(**kw):
        raise RuntimeError("all providers down")

    from app.agents.llm_router import llm_router as router
    monkeypatch.setattr(router, "complete", boom)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.post("/v1/agents/chat", json={"history": [
            {"role": "user", "content": "hi"},
        ]})
    assert r.status_code == 502
    assert "LLM call failed" in r.json()["detail"]


@pytest.mark.asyncio
async def test_chat_auto_provisions_account(auth_as_user, patched_llm, cleanup):
    """First-touch /chat must create the Account so other endpoints work afterwards."""
    from sqlalchemy import func, select
    from app.models.account import Account

    async with SessionLocal() as db:
        before = (await db.execute(
            select(func.count()).select_from(Account)
            .where(Account.clerk_org_id == f"{TAG}_o")
        )).scalar()
    assert before == 0

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        await cx.post("/v1/agents/chat", json={"history": [{"role": "user", "content": "hi"}]})

    async with SessionLocal() as db:
        after = (await db.execute(
            select(func.count()).select_from(Account)
            .where(Account.clerk_org_id == f"{TAG}_o")
        )).scalar()
    assert after == 1


@pytest.mark.asyncio
async def test_chat_ignores_system_role_messages_in_history_when_searching_for_user(auth_as_user, patched_llm, cleanup):
    """history contains a system message + user message — the user is found."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.post("/v1/agents/chat", json={"history": [
            {"role": "system", "content": "you are a helpful assistant"},
            {"role": "user",   "content": "draft a tweet"},
        ]})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_list_agents_endpoint(auth_as_user, cleanup):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get("/v1/agents")
    assert r.status_code == 200
    body = r.json()
    expected = {"research", "strategist", "writer", "seo",
                "designer", "video", "publisher", "analytics", "learning"}
    assert expected <= set(body["agents"])
