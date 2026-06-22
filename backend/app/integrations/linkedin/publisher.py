"""LinkedIn publisher — posts a UGC post as the authenticated member."""
from __future__ import annotations

import json
from typing import Any

import httpx

from app.core.security import decrypt
from app.models.content import ContentAsset
from app.models.publishing import PublishChannel, Schedule


API = "https://api.linkedin.com/v2"


def _tokens(channel: PublishChannel) -> dict[str, Any]:
    return json.loads(decrypt(channel.oauth_blob if isinstance(channel.oauth_blob, str) else channel.oauth_blob.get("ct", "")))


async def _author_urn(token: str) -> str:
    async with httpx.AsyncClient(timeout=15) as cx:
        r = await cx.get(f"{API}/userinfo", headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()
        sub = r.json().get("sub")
    return f"urn:li:person:{sub}"


async def publish(channel: PublishChannel, asset: ContentAsset) -> dict:
    tok = _tokens(channel)["access_token"]
    author = await _author_urn(tok)
    payload = {
        "author": author,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": asset.body or asset.title or ""},
                "shareMediaCategory": "NONE",
            }
        },
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
    }
    async with httpx.AsyncClient(timeout=20) as cx:
        r = await cx.post(
            f"{API}/ugcPosts",
            headers={
                "Authorization": f"Bearer {tok}",
                "X-Restli-Protocol-Version": "2.0.0",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        r.raise_for_status()
        post_id = r.headers.get("x-restli-id") or r.json().get("id")
    return {"id": post_id, "url": f"https://www.linkedin.com/feed/update/{post_id}"}


async def fetch_metrics(schedule: Schedule) -> dict | None:
    if not schedule.external_id:
        return None
    return {"platform": "linkedin", "meta": {"external_id": schedule.external_id}}
