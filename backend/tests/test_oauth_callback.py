"""OAuth callback full roundtrip.

Simulates the start → exchange handoff for LinkedIn:
  1. build_auth_url stores state + per-tenant client_id/secret in Redis
  2. exchange_code reads that state, hits the token endpoint (mocked), and
     returns an encrypted oauth_blob
  3. The encrypted blob decrypts back to the original token payload
"""
from __future__ import annotations

import json
import os
import uuid

import pytest

os.environ.setdefault("SECRET_KEY", "testtesttesttesttesttesttesttest")
os.environ.setdefault("LINKEDIN_CLIENT_ID", "")
os.environ.setdefault("LINKEDIN_CLIENT_SECRET", "")

from app.core.security import decrypt  # noqa: E402
from app.db.redis import redis  # noqa: E402
from app.integrations.oauth import build_auth_url, exchange_code  # noqa: E402


@pytest.mark.asyncio
async def test_byo_oauth_roundtrip_linkedin(monkeypatch):
    brand_id = str(uuid.uuid4())
    redirect = f"http://localhost:3000/dashboard/channels/callback?b={brand_id}"

    # 1. Start the flow with BYO client_id/secret (admin .env not configured).
    start = await build_auth_url(
        "linkedin", brand_id, redirect,
        client_id="USER_PROVIDED_CLIENT_ID",
        client_secret="USER_PROVIDED_CLIENT_SECRET",
    )
    state = start["state"]
    assert "client_id=USER_PROVIDED_CLIENT_ID" in start["url"]
    assert "linkedin.com/oauth/v2/authorization" in start["url"]
    assert f"state={state}" in start["url"]

    # Confirm Redis stash contains the user-provided secret.
    raw = await redis.get(f"oauth:state:{state}")
    assert raw is not None
    saved = json.loads(raw)
    assert saved["client_id"] == "USER_PROVIDED_CLIENT_ID"
    assert saved["client_secret"] == "USER_PROVIDED_CLIENT_SECRET"
    assert saved["brand_id"] == brand_id

    # 2. Mock the token exchange.
    captured = {}

    class FakeResp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {
                "access_token": "AT_LI",
                "refresh_token": "RT_LI",
                "expires_in": 3600,
                "scope": "openid profile email w_member_social",
            }

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, url, **kw):
            captured["url"] = url
            captured["data"] = kw.get("data", {})
            captured["headers"] = kw.get("headers", {})
            return FakeResp()

    from app.integrations import oauth as oauth_mod
    monkeypatch.setattr(oauth_mod.httpx, "AsyncClient", FakeClient)

    result = await exchange_code("linkedin", code="AUTH_CODE_XYZ", state=state, redirect_uri=redirect)

    # 3. Token endpoint was called with the BYO credentials.
    assert captured["url"] == "https://www.linkedin.com/oauth/v2/accessToken"
    assert captured["data"]["code"] == "AUTH_CODE_XYZ"
    assert captured["data"]["client_id"] == "USER_PROVIDED_CLIENT_ID"
    assert captured["data"]["client_secret"] == "USER_PROVIDED_CLIENT_SECRET"
    assert captured["data"]["grant_type"] == "authorization_code"
    assert captured["data"]["redirect_uri"] == redirect

    # 4. State key was cleaned up.
    assert (await redis.get(f"oauth:state:{state}")) is None

    # 5. Returned blob is encrypted but decrypts back to the issued tokens.
    assert result["brand_id"] == brand_id
    assert result["platform"] == "linkedin"
    decrypted = json.loads(decrypt(result["oauth_blob"]))
    assert decrypted["access_token"] == "AT_LI"
    assert decrypted["refresh_token"] == "RT_LI"
    assert decrypted["expires_in"] == 3600


@pytest.mark.asyncio
async def test_exchange_with_expired_state_fails():
    """If the state was never stored (or expired in Redis), exchange must refuse."""
    with pytest.raises(RuntimeError, match="oauth state"):
        await exchange_code("linkedin", code="x", state="nonexistent-state", redirect_uri="http://x")


@pytest.mark.asyncio
async def test_reddit_uses_basic_auth_not_client_id_in_body(monkeypatch):
    """Reddit requires basic-auth — confirm that branch fires when platform=reddit."""
    brand_id = str(uuid.uuid4())
    start = await build_auth_url(
        "reddit", brand_id, "http://x",
        client_id="REDDIT_CID", client_secret="REDDIT_SECRET",
    )
    state = start["state"]

    captured = {}

    class FakeResp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"access_token": "TR", "refresh_token": "RR", "expires_in": 3600}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, url, **kw):
            captured["url"] = url
            captured["headers"] = kw.get("headers", {})
            captured["data"] = kw.get("data", {})
            return FakeResp()

    from app.integrations import oauth as oauth_mod
    monkeypatch.setattr(oauth_mod.httpx, "AsyncClient", FakeClient)
    await exchange_code("reddit", code="C", state=state, redirect_uri="http://x")

    # Basic auth header set, client_id/secret stripped from body.
    auth = captured["headers"].get("Authorization", "")
    assert auth.startswith("Basic "), f"missing basic auth: {captured['headers']}"
    import base64
    decoded = base64.b64decode(auth.split(" ", 1)[1]).decode()
    assert decoded == "REDDIT_CID:REDDIT_SECRET"
    assert "client_id" not in captured["data"]
    assert "client_secret" not in captured["data"]
