"""Facebook Pages publisher — posts text + optional photo to a Page feed."""
from __future__ import annotations

import json

import httpx

from app.core.security import decrypt
from app.models.content import ContentAsset
from app.models.publishing import PublishChannel, Schedule


API = "https://graph.facebook.com/v18.0"


def _tokens(channel: PublishChannel) -> dict:
    blob = channel.oauth_blob if isinstance(channel.oauth_blob, str) else channel.oauth_blob.get("ct", "")
    return json.loads(decrypt(blob))


async def publish(channel: PublishChannel, asset: ContentAsset) -> dict:
    tokens = _tokens(channel)
    page_id = tokens.get("page_id") or channel.meta.get("page_id")
    page_token = tokens.get("page_access_token") or tokens.get("access_token")
    async with httpx.AsyncClient(timeout=20) as cx:
        r = await cx.post(
            f"{API}/{page_id}/feed",
            data={"message": asset.body or asset.title or "", "access_token": page_token},
        )
        r.raise_for_status()
        data = r.json()
    pid = data.get("id", "")
    return {"id": pid, "url": f"https://facebook.com/{pid}"}


async def fetch_metrics(schedule: Schedule) -> dict | None:
    if not schedule.external_id:
        return None
    return {"platform": "facebook", "meta": {"external_id": schedule.external_id}}
