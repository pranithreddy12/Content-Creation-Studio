"""X (Twitter) publisher — single tweet OR thread."""
from __future__ import annotations

import json

import httpx

from app.core.security import decrypt
from app.models.content import ContentAsset
from app.models.publishing import PublishChannel, Schedule


API = "https://api.twitter.com/2"


def _token(channel: PublishChannel) -> str:
    blob = channel.oauth_blob if isinstance(channel.oauth_blob, str) else channel.oauth_blob.get("ct", "")
    return json.loads(decrypt(blob))["access_token"]


async def _post(cx: httpx.AsyncClient, token: str, text: str, reply_to: str | None = None) -> dict:
    payload: dict = {"text": text}
    if reply_to:
        payload["reply"] = {"in_reply_to_tweet_id": reply_to}
    r = await cx.post(
        f"{API}/tweets",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )
    r.raise_for_status()
    return r.json().get("data", {})


async def publish(channel: PublishChannel, asset: ContentAsset) -> dict:
    token = _token(channel)
    tweets: list[str]
    if asset.format == "x_thread" and isinstance(asset.body_json, list):
        tweets = [str(t)[:280] for t in asset.body_json if t]
    else:
        tweets = [(asset.body or asset.title or "")[:280]]
    out_ids: list[str] = []
    async with httpx.AsyncClient(timeout=20) as cx:
        prev: str | None = None
        for text in tweets:
            data = await _post(cx, token, text, reply_to=prev)
            tid = data.get("id")
            if not tid:
                break
            out_ids.append(tid)
            prev = tid
    first = out_ids[0] if out_ids else ""
    return {"id": first, "url": f"https://x.com/i/web/status/{first}", "thread_ids": out_ids}


async def fetch_metrics(schedule: Schedule) -> dict | None:
    if not schedule.external_id:
        return None
    return {"platform": "x", "meta": {"external_id": schedule.external_id}}
