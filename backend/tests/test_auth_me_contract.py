"""Contract for `/v1/auth/me` — the endpoint mobile + dashboard hit to seed user state."""
from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("SECRET_KEY", "testtesttesttesttesttesttesttest")

from app.main import app  # noqa: E402


@pytest.mark.asyncio
async def test_auth_me_returns_all_canonical_fields(monkeypatch):
    """The response must include exactly these keys: clerk_user_id, clerk_org_id, email, role."""
    from app.api.deps import auth as auth_mod
    monkeypatch.setattr(auth_mod, "verify_clerk_jwt", lambda t: {
        "sub": "user_canonical",
        "org_id": "org_canonical",
        "email": "canonical@test.com",
        "org_role": "admin",
    })

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get("/v1/auth/me", headers={"Authorization": "Bearer fake"})
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"clerk_user_id", "clerk_org_id", "email", "role"}
    assert body["clerk_user_id"] == "user_canonical"
    assert body["clerk_org_id"] == "org_canonical"
    assert body["email"] == "canonical@test.com"
    assert body["role"] == "admin"


@pytest.mark.asyncio
async def test_auth_me_returns_nulls_for_personal_clerk_users(monkeypatch):
    """A Clerk user with no active org should still get a valid response with nulls."""
    from app.api.deps import auth as auth_mod
    monkeypatch.setattr(auth_mod, "verify_clerk_jwt", lambda t: {"sub": "user_solo"})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get("/v1/auth/me", headers={"Authorization": "Bearer fake"})
    assert r.status_code == 200
    body = r.json()
    assert body["clerk_user_id"] == "user_solo"
    assert body["clerk_org_id"] is None
    assert body["email"] is None
    assert body["role"] is None


@pytest.mark.asyncio
async def test_auth_me_works_with_lowercase_bearer(monkeypatch):
    """The bearer-prefix check must be case-insensitive (mobile clients sometimes send `bearer`)."""
    from app.api.deps import auth as auth_mod
    monkeypatch.setattr(auth_mod, "verify_clerk_jwt", lambda t: {"sub": "u"})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get("/v1/auth/me", headers={"Authorization": "bearer fake"})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_auth_me_emits_cors_headers_for_browser_clients():
    """A request with an Origin header should get CORS headers back even when auth fails."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get(
            "/v1/auth/me",
            headers={"Origin": "http://localhost:3000"},
        )
    assert r.status_code == 401
    assert r.headers.get("access-control-allow-origin") == "*"
