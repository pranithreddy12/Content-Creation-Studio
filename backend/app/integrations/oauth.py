"""Generic OAuth helpers — start a flow and exchange a code for tokens.

Each platform declares its config in PLATFORM_OAUTH. The HTTP endpoints in
`api/v1/endpoints/publishing.py` call `build_auth_url` and `exchange_code`.
"""
from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode

import httpx

from app.core.config import settings
from app.core.security import encrypt
from app.db.redis import redis


@dataclass
class OAuthConfig:
    platform: str
    authorize_url: str
    token_url: str
    scopes: list[str]
    client_id_attr: str
    client_secret_attr: str
    extra_authorize_params: dict | None = None
    extra_token_params: dict | None = None


PLATFORM_OAUTH = {
    "linkedin": OAuthConfig(
        platform="linkedin",
        authorize_url="https://www.linkedin.com/oauth/v2/authorization",
        token_url="https://www.linkedin.com/oauth/v2/accessToken",
        scopes=["openid", "profile", "email", "w_member_social"],
        client_id_attr="linkedin_client_id",
        client_secret_attr="linkedin_client_secret",
    ),
    "x": OAuthConfig(
        platform="x",
        authorize_url="https://twitter.com/i/oauth2/authorize",
        token_url="https://api.twitter.com/2/oauth2/token",
        scopes=["tweet.read", "tweet.write", "users.read", "offline.access"],
        client_id_attr="x_client_id",
        client_secret_attr="x_client_secret",
        extra_authorize_params={"code_challenge_method": "plain"},
    ),
    "facebook": OAuthConfig(
        platform="facebook",
        authorize_url="https://www.facebook.com/v18.0/dialog/oauth",
        token_url="https://graph.facebook.com/v18.0/oauth/access_token",
        scopes=["pages_manage_posts", "pages_read_engagement", "publish_video"],
        client_id_attr="facebook_app_id",
        client_secret_attr="facebook_app_secret",
    ),
    "instagram": OAuthConfig(
        platform="instagram",
        authorize_url="https://www.facebook.com/v18.0/dialog/oauth",
        token_url="https://graph.facebook.com/v18.0/oauth/access_token",
        scopes=["instagram_basic", "instagram_content_publish", "pages_read_engagement"],
        client_id_attr="facebook_app_id",
        client_secret_attr="facebook_app_secret",
    ),
    "tiktok": OAuthConfig(
        platform="tiktok",
        authorize_url="https://www.tiktok.com/v2/auth/authorize/",
        token_url="https://open.tiktokapis.com/v2/oauth/token/",
        scopes=["user.info.basic", "video.upload", "video.publish"],
        client_id_attr="tiktok_client_key",
        client_secret_attr="tiktok_client_secret",
    ),
    "youtube": OAuthConfig(
        platform="youtube",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/youtube.upload",
                "https://www.googleapis.com/auth/youtube.readonly"],
        client_id_attr="youtube_client_id",
        client_secret_attr="youtube_client_secret",
        extra_authorize_params={"access_type": "offline", "prompt": "consent"},
    ),
    "reddit": OAuthConfig(
        platform="reddit",
        authorize_url="https://www.reddit.com/api/v1/authorize",
        token_url="https://www.reddit.com/api/v1/access_token",
        scopes=["identity", "submit", "read"],
        client_id_attr="reddit_client_id",
        client_secret_attr="reddit_client_secret",
        extra_authorize_params={"duration": "permanent"},
    ),
}


def _cfg_attr(cfg: OAuthConfig, name: str, override: str | None = None) -> str:
    if override:
        return override
    val = getattr(settings, name, None)
    if not val:
        raise RuntimeError(f"missing setting {name} for {cfg.platform}")
    return val


async def build_auth_url(
    platform: str,
    brand_id: str,
    redirect_uri: str,
    *,
    client_id: str | None = None,
    client_secret: str | None = None,
) -> dict:
    cfg = PLATFORM_OAUTH[platform]
    state = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(64)
    await redis.set(
        f"oauth:state:{state}",
        json.dumps({
            "platform": platform,
            "brand_id": brand_id,
            "verifier": code_verifier,
            "client_id": client_id,
            "client_secret": client_secret,
        }),
        ex=600,
    )
    params: dict = {
        "client_id": _cfg_attr(cfg, cfg.client_id_attr, client_id),
        "redirect_uri": redirect_uri,
        "scope": " ".join(cfg.scopes),
        "response_type": "code",
        "state": state,
    }
    if cfg.platform == "x":
        # PKCE plain
        params["code_challenge"] = code_verifier
    if cfg.extra_authorize_params:
        params.update(cfg.extra_authorize_params)
    return {"url": f"{cfg.authorize_url}?{urlencode(params)}", "state": state}


async def exchange_code(platform: str, code: str, state: str, redirect_uri: str) -> dict:
    cfg = PLATFORM_OAUTH[platform]
    raw = await redis.get(f"oauth:state:{state}")
    if not raw:
        raise RuntimeError("oauth state expired or invalid")
    saved = json.loads(raw)
    cid = saved.get("client_id")
    csec = saved.get("client_secret")
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": _cfg_attr(cfg, cfg.client_id_attr, cid),
        "client_secret": _cfg_attr(cfg, cfg.client_secret_attr, csec),
    }
    if cfg.platform == "x":
        data["code_verifier"] = saved["verifier"]
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if cfg.platform == "reddit":
        # Reddit requires basic auth
        import base64
        creds = f"{_cfg_attr(cfg, cfg.client_id_attr, cid)}:{_cfg_attr(cfg, cfg.client_secret_attr, csec)}"
        headers["Authorization"] = f"Basic {base64.b64encode(creds.encode()).decode()}"
        data.pop("client_id", None)
        data.pop("client_secret", None)
    async with httpx.AsyncClient(timeout=20) as cx:
        r = await cx.post(cfg.token_url, data=data, headers=headers)
        r.raise_for_status()
        tokens = r.json()
    await redis.delete(f"oauth:state:{state}")
    # Encrypt and return
    return {
        "brand_id": saved["brand_id"],
        "platform": platform,
        "oauth_blob": encrypt(json.dumps(tokens)),
    }
