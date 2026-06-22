import pytest


@pytest.mark.asyncio
async def test_health(app_client):
    r = await app_client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["service"] == "ai-content-studio"


@pytest.mark.asyncio
async def test_root(app_client):
    r = await app_client.get("/")
    assert r.status_code == 200
    assert r.json()["name"] == "ai-content-studio"


@pytest.mark.asyncio
async def test_unauthenticated_returns_401(app_client):
    r = await app_client.get("/v1/brands")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_security_headers(app_client):
    r = await app_client.get("/health")
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("X-Frame-Options") == "DENY"
    assert "Content-Security-Policy" in r.headers
