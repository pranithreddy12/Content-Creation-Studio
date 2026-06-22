"""Reddit publisher — self-post submission via OAuth."""
from __future__ import annotations

import json

import httpx

from app.core.config import settings
from app.core.security import decrypt
from app.models.content import ContentAsset
from app.models.publishing import PublishChannel, Schedule


def _token(channel: PublishChannel) -> str:
    blob = channel.oauth_blob if isinstance(channel.oauth_blob, str) else channel.oauth_blob.get("ct", "")
    return json.loads(decrypt(blob))["access_token"]


async def publish(channel: PublishChannel, asset: ContentAsset) -> dict:
    tok = _token(channel)
    body_json = asset.body_json or {}
    title = body_json.get("title") or asset.title or ""
    body = body_json.get("body") or asset.body or ""
    subreddit = (channel.meta or {}).get("subreddit") or body_json.get("subreddit")
    if not subreddit:
        raise RuntimeError("reddit channel needs meta.subreddit")
    async with httpx.AsyncClient(timeout=20) as cx:
        r = await cx.post(
            "https://oauth.reddit.com/api/submit",
            headers={"Authorization": f"Bearer {tok}", "User-Agent": settings.reddit_user_agent},
            data={"sr": subreddit, "kind": "self", "title": title[:300],
                  "text": body[:40000], "api_type": "json"},
        )
        r.raise_for_status()
        data = r.json().get("json", {}).get("data", {})
    return {"id": data.get("id"), "url": data.get("url")}


async def fetch_metrics(schedule: Schedule) -> dict | None:
    return None if not schedule.external_id else {"platform": "reddit"}
