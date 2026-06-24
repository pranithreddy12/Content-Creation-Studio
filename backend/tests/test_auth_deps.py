"""Auth dependency unit tests (current_user, require_account, require_brand_access).

Exercises the FastAPI dependency layer directly + via HTTP. We monkey-patch
`verify_clerk_jwt` to control the decoded claims so the tests don't need a
running Clerk JWKS endpoint.
"""
from __future__ import annotations

import os
import uuid

import jwt
import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("SECRET_KEY", "testtesttesttesttesttesttesttest")

from app.api.deps.auth import (  # noqa: E402
    CurrentUser, current_user, require_account, require_brand_access,
)
from app.main import app  # noqa: E402


# ── current_user dependency ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_current_user_rejects_missing_authorization():
    with pytest.raises(HTTPException) as exc:
        await current_user(authorization=None)
    assert exc.value.status_code == 401
    assert "missing bearer token" in exc.value.detail


@pytest.mark.asyncio
async def test_current_user_rejects_non_bearer_scheme():
    with pytest.raises(HTTPException) as exc:
        await current_user(authorization="Basic abc:def")
    assert exc.value.status_code == 401
    assert "missing bearer token" in exc.value.detail


@pytest.mark.asyncio
async def test_current_user_accepts_lowercase_bearer():
    """`bearer` (lowercase) should also work — the check is case-insensitive."""
    # Force verify to return a valid claim set
    from app.api.deps import auth as auth_mod
    orig = auth_mod.verify_clerk_jwt
    try:
        auth_mod.verify_clerk_jwt = lambda t: {
            "sub": "u_xyz", "org_id": "org_xyz", "email": "x@y.com",
            "org_role": "admin",
        }
        u = await current_user(authorization="bearer abc.def.ghi")
    finally:
        auth_mod.verify_clerk_jwt = orig
    assert u.clerk_user_id == "u_xyz"
    assert u.clerk_org_id == "org_xyz"
    assert u.email == "x@y.com"
    assert u.role == "admin"


@pytest.mark.asyncio
async def test_current_user_rejects_invalid_token():
    from app.api.deps import auth as auth_mod
    orig = auth_mod.verify_clerk_jwt
    try:
        def boom(_t): raise jwt.InvalidTokenError("kid not found")
        auth_mod.verify_clerk_jwt = boom
        with pytest.raises(HTTPException) as exc:
            await current_user(authorization="Bearer abc.def.ghi")
    finally:
        auth_mod.verify_clerk_jwt = orig
    assert exc.value.status_code == 401
    assert "invalid token" in exc.value.detail


@pytest.mark.asyncio
async def test_current_user_returns_partial_when_optional_claims_missing():
    from app.api.deps import auth as auth_mod
    orig = auth_mod.verify_clerk_jwt
    try:
        # Only sub present — everything else missing
        auth_mod.verify_clerk_jwt = lambda t: {"sub": "u_only"}
        u = await current_user(authorization="Bearer t")
    finally:
        auth_mod.verify_clerk_jwt = orig
    assert u.clerk_user_id == "u_only"
    assert u.clerk_org_id is None
    assert u.email is None
    assert u.role is None


# ── require_account role gating ──────────────────────────────────────

@pytest.mark.asyncio
async def test_require_account_allows_listed_role():
    dep = require_account(allowed_roles=("admin", "editor"))
    user = CurrentUser("u1", "org1", "x@y", role="editor", raw={})
    result = await dep(user)
    assert result is user


@pytest.mark.asyncio
async def test_require_account_rejects_unlisted_role():
    dep = require_account(allowed_roles=("admin",))
    user = CurrentUser("u1", "org1", "x@y", role="viewer", raw={})
    with pytest.raises(HTTPException) as exc:
        await dep(user)
    assert exc.value.status_code == 403
    assert "insufficient role" in exc.value.detail


@pytest.mark.asyncio
async def test_require_account_allows_when_user_has_no_role():
    """Per the dep: if `user.role` is None, the check is skipped (Clerk users without an org)."""
    dep = require_account(allowed_roles=("admin",))
    user = CurrentUser("u1", clerk_org_id=None, email=None, role=None, raw={})
    result = await dep(user)
    assert result is user


@pytest.mark.asyncio
async def test_require_account_default_accepts_all_known_roles():
    dep = require_account()  # default: owner, admin, editor, viewer
    for role in ("owner", "admin", "editor", "viewer"):
        u = CurrentUser("u", "o", "x@y", role=role, raw={})
        assert (await dep(u)) is u


# ── require_brand_access dep ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_require_brand_access_passes_through_brand_id():
    """The dep just authenticates and forwards the path arg — auth proper happens in services."""
    bid = uuid.uuid4()
    user = CurrentUser("u1", "org1", "x@y", role="owner", raw={})
    result = await require_brand_access(brand_id=bid, user=user)
    assert result == bid


# ── Full HTTP integration with verify mocked ─────────────────────────

@pytest.mark.asyncio
async def test_real_request_with_mocked_jwt_passes_through(monkeypatch):
    """Hit /v1/auth/me with a mocked JWT verifier; assert the decoded claims surface."""
    from app.api.deps import auth as auth_mod
    monkeypatch.setattr(auth_mod, "verify_clerk_jwt", lambda t: {
        "sub": "user_integration_test",
        "org_id": "org_integration_test",
        "email": "i@test.com",
        "org_role": "owner",
    })

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get("/v1/auth/me", headers={"Authorization": "Bearer fake.jwt.token"})
    assert r.status_code == 200
    body = r.json()
    assert body["clerk_user_id"] == "user_integration_test"
    assert body["clerk_org_id"] == "org_integration_test"
    assert body["email"] == "i@test.com"
    assert body["role"] == "owner"


@pytest.mark.asyncio
async def test_real_request_without_token_returns_401():
    """No Authorization header → 401 from current_user dep."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get("/v1/auth/me")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_health_endpoint_does_not_require_auth():
    """Sanity: /health is open and never demands a token."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True
