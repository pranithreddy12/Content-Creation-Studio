"""Publisher dry-run tests.

Each platform adapter is wired up with a fake encrypted OAuth blob and the
outbound httpx calls are mocked. We assert the request shape (URL, headers,
body) — the goal is to catch regressions in the encrypt→decrypt→request path,
NOT to hit real social-platform APIs.
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass

import httpx
import pytest

os.environ.setdefault("SECRET_KEY", "testtesttesttesttesttesttesttest")

from app.core.security import encrypt  # noqa: E402


@dataclass
class FakeChannel:
    oauth_blob: dict
    display_name: str = "test"
    meta: dict | None = None


@dataclass
class FakeAsset:
    id: uuid.UUID
    title: str | None
    body: str | None
    body_json: object | None = None
    format: str = "linkedin"
    seo: dict | None = None


def _channel(token_payload: dict) -> FakeChannel:
    return FakeChannel(oauth_blob={"ct": encrypt(json.dumps(token_payload))})


# ─── LinkedIn ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_linkedin_publish_constructs_ugc_post(monkeypatch):
    from app.integrations.linkedin import publisher

    captured = {}

    class FakeResp:
        status_code = 201
        headers = {"x-restli-id": "urn:li:share:7000000000000000000"}
        def raise_for_status(self): pass
        def json(self): return {}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, url, **kw):
            class R:
                status_code = 200
                def raise_for_status(self): pass
                def json(self): return {"sub": "abc123"}
            return R()
        async def post(self, url, **kw):
            captured["url"] = url
            captured["headers"] = kw.get("headers", {})
            captured["json"] = kw.get("json", {})
            return FakeResp()

    monkeypatch.setattr(publisher.httpx, "AsyncClient", FakeClient)

    channel = _channel({"access_token": "TOKEN_LI"})
    asset = FakeAsset(id=uuid.uuid4(), title="LI Post", body="hello linkedin")
    result = await publisher.publish(channel, asset)

    assert captured["url"].endswith("/ugcPosts")
    assert captured["headers"].get("Authorization") == "Bearer TOKEN_LI"
    assert captured["headers"].get("X-Restli-Protocol-Version") == "2.0.0"
    payload = captured["json"]
    assert payload["author"] == "urn:li:person:abc123"
    assert payload["lifecycleState"] == "PUBLISHED"
    assert payload["specificContent"]["com.linkedin.ugc.ShareContent"]["shareCommentary"]["text"] == "hello linkedin"
    assert result["id"]
    assert result["url"].startswith("https://www.linkedin.com/feed/update/")


# ─── X (Twitter) ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_x_publish_threads_when_body_json_is_list(monkeypatch):
    from app.integrations.twitter import publisher

    calls: list[dict] = []
    counter = {"i": 0}

    class FakeResp:
        status_code = 201
        def raise_for_status(self): pass
        def json(self):
            counter["i"] += 1
            return {"data": {"id": f"100{counter['i']}"}}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, url, **kw):
            calls.append({"url": url, "headers": kw.get("headers"), "json": kw.get("json")})
            return FakeResp()

    monkeypatch.setattr(publisher.httpx, "AsyncClient", FakeClient)

    channel = _channel({"access_token": "TOKEN_X"})
    tweets = ["Thread tweet one.", "Thread tweet two.", "Thread tweet three."]
    asset = FakeAsset(id=uuid.uuid4(), title="t", body=None, body_json=tweets, format="x_thread")
    result = await publisher.publish(channel, asset)

    assert len(calls) == 3, f"expected 3 tweets posted, got {len(calls)}"
    assert all(c["url"].endswith("/tweets") for c in calls)
    assert all(c["headers"]["Authorization"] == "Bearer TOKEN_X" for c in calls)
    # First tweet has no reply field; subsequent ones reply to the previous id.
    assert "reply" not in calls[0]["json"]
    assert calls[1]["json"]["reply"]["in_reply_to_tweet_id"] == "1001"
    assert calls[2]["json"]["reply"]["in_reply_to_tweet_id"] == "1002"
    assert result["thread_ids"] == ["1001", "1002", "1003"]


@pytest.mark.asyncio
async def test_x_publish_truncates_each_tweet_to_280(monkeypatch):
    from app.integrations.twitter import publisher

    posted: list[str] = []

    class FakeResp:
        status_code = 201
        def raise_for_status(self): pass
        def json(self): return {"data": {"id": "1"}}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, url, **kw):
            posted.append(kw["json"]["text"])
            return FakeResp()

    monkeypatch.setattr(publisher.httpx, "AsyncClient", FakeClient)
    long_text = "x" * 1000
    asset = FakeAsset(id=uuid.uuid4(), title=None, body=long_text, format="linkedin")
    await publisher.publish(_channel({"access_token": "T"}), asset)
    assert len(posted) == 1
    assert len(posted[0]) <= 280


# ─── WordPress ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_wordpress_publish_basic_auth_and_html(monkeypatch):
    from app.integrations.wordpress import publisher

    captured = {}

    class FakeResp:
        status_code = 201
        def raise_for_status(self): pass
        def json(self): return {"id": 1234, "link": "https://blog.example.com/?p=1234"}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, url, **kw):
            captured["url"] = url
            captured["headers"] = kw.get("headers", {})
            captured["json"] = kw.get("json", {})
            return FakeResp()

    monkeypatch.setattr(publisher.httpx, "AsyncClient", FakeClient)

    channel = _channel({
        "site": "https://blog.example.com",
        "username": "admin",
        "app_password": "abcd 1234 efgh 5678",
    })
    asset = FakeAsset(
        id=uuid.uuid4(),
        title="My Post",
        body="# heading\n\nbody _italic_",
        seo={"title": "SEO Title", "slug": "my-post", "meta_description": "desc"},
        format="blog",
    )
    res = await publisher.publish(channel, asset)

    assert captured["url"] == "https://blog.example.com/wp-json/wp/v2/posts"
    assert captured["headers"]["Authorization"].startswith("Basic ")
    assert captured["headers"]["Content-Type"] == "application/json"
    j = captured["json"]
    assert j["title"] == "SEO Title"  # SEO override
    assert j["slug"] == "my-post"
    assert j["status"] == "publish"
    assert ">heading</h1>" in j["content"]      # markdown rendered (toc ext adds id="")
    assert "<em>italic</em>" in j["content"]
    assert res["id"] == "1234"


# ─── Reddit ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reddit_publish_self_post(monkeypatch):
    from app.integrations.reddit import publisher

    captured = {}

    class FakeResp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {"json": {"data": {"id": "abc123", "url": "https://reddit.com/r/test/comments/abc123"}}}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, url, **kw):
            captured["url"] = url
            captured["headers"] = kw.get("headers", {})
            captured["data"] = kw.get("data", {})
            return FakeResp()

    monkeypatch.setattr(publisher.httpx, "AsyncClient", FakeClient)

    channel = _channel({"access_token": "TOKEN_REDDIT"})
    channel.meta = {"subreddit": "test"}
    asset = FakeAsset(
        id=uuid.uuid4(),
        title="Test Post Title",
        body="Test post body.",
        body_json={"title": "Override Title", "body": "Override Body", "subreddit": "test"},
        format="reddit",
    )
    res = await publisher.publish(channel, asset)

    assert captured["url"].endswith("/api/submit")
    assert captured["headers"]["Authorization"] == "Bearer TOKEN_REDDIT"
    assert captured["data"]["sr"] == "test"
    assert captured["data"]["kind"] == "self"
    assert captured["data"]["title"] == "Override Title"
    assert captured["data"]["text"] == "Override Body"
    assert res["id"] == "abc123"


# ─── Encrypt / decrypt round-trip on the OAuth blob ────────────────────────

def test_oauth_blob_roundtrips_through_aes_gcm():
    """Every publisher decrypts channel.oauth_blob['ct'] — make sure that path actually works."""
    from app.core.security import decrypt

    secret = {"access_token": "T", "refresh_token": "R", "expires_at": 1234567890}
    blob = encrypt(json.dumps(secret))
    decrypted = json.loads(decrypt(blob))
    assert decrypted == secret
