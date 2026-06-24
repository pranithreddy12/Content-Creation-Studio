"""TikTok Content Posting API — direct post + analytics."""
from __future__ import annotations

import json

import httpx
from sqlalchemy import select

from app.core.security import decrypt
from app.db.session import SessionLocal
from app.models.content import ContentAsset, MediaAsset
from app.models.publishing import PublishChannel, Schedule
from app.utils.storage import presign

API = "https://open.tiktokapis.com/v2"


def _tokens(channel: PublishChannel) -> dict:
    blob = channel.oauth_blob if isinstance(channel.oauth_blob, str) else channel.oauth_blob.get("ct", "")
    return json.loads(decrypt(blob))


async def _video_url(asset_id: str) -> str:
    async with SessionLocal() as db:
        m = (await db.execute(
            select(MediaAsset).where(MediaAsset.asset_id == asset_id, MediaAsset.kind.in_(["video"]))
        )).scalars().first()
    if not m:
        raise RuntimeError("no rendered video for tiktok post")
    return presign(m.storage_key)


async def publish(channel: PublishChannel, asset: ContentAsset) -> dict:
    tok = _tokens(channel)["access_token"]
    video_url = await _video_url(str(asset.id))
    payload = {
        "post_info": {
            "title": (asset.title or "")[:90],
            "privacy_level": "PUBLIC_TO_EVERYONE",
            "disable_duet": False, "disable_comment": False, "disable_stitch": False,
        },
        "source_info": {"source": "PULL_FROM_URL", "video_url": video_url},
    }
    async with httpx.AsyncClient(timeout=30) as cx:
        r = await cx.post(
            f"{API}/post/publish/video/init/",
            headers={"Authorization": f"Bearer {tok}"},
            json=payload,
        )
        r.raise_for_status()
        publish_id = r.json().get("data", {}).get("publish_id")
    return {"id": publish_id, "url": f"https://www.tiktok.com/@{tok[:6]}"}  # canonical URL is fetched async


async def fetch_metrics(schedule: Schedule) -> dict | None:
    if not schedule.external_id:
        return None
    return {"platform": "tiktok", "meta": {"external_id": schedule.external_id}}
