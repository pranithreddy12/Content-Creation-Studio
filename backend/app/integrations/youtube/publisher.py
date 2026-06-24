"""YouTube uploader — resumable upload + analytics via Data API v3."""
from __future__ import annotations

import json

import httpx
from sqlalchemy import select

from app.core.security import decrypt
from app.db.session import SessionLocal
from app.models.content import ContentAsset, MediaAsset
from app.models.publishing import PublishChannel, Schedule
from app.utils.storage import s3

API = "https://www.googleapis.com"


def _tokens(channel: PublishChannel) -> dict:
    blob = channel.oauth_blob if isinstance(channel.oauth_blob, str) else channel.oauth_blob.get("ct", "")
    return json.loads(decrypt(blob))


async def publish(channel: PublishChannel, asset: ContentAsset) -> dict:
    tok = _tokens(channel)["access_token"]
    async with SessionLocal() as db:
        m = (await db.execute(
            select(MediaAsset).where(MediaAsset.asset_id == asset.id, MediaAsset.kind == "video")
        )).scalars().first()
    if not m:
        raise RuntimeError("no rendered video for youtube upload")

    meta = {
        "snippet": {
            "title": (asset.title or "")[:100],
            "description": (asset.body or "")[:5000],
            "tags": (asset.seo or {}).get("secondary_keywords", [])[:20],
            "categoryId": "22",
        },
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
    }
    async with httpx.AsyncClient(timeout=60) as cx:
        # 1. start resumable session
        init = await cx.post(
            f"{API}/upload/youtube/v3/videos",
            params={"uploadType": "resumable", "part": "snippet,status"},
            headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
            json=meta,
        )
        init.raise_for_status()
        session_url = init.headers["Location"]
        # 2. stream the file
        obj = s3().get_object(Bucket=__import__("app.core.config", fromlist=["settings"]).settings.s3_bucket, Key=m.storage_key)
        body = obj["Body"].read()
        up = await cx.put(session_url, content=body)
        up.raise_for_status()
        vid = up.json()["id"]
    return {"id": vid, "url": f"https://youtu.be/{vid}"}


async def fetch_metrics(schedule: Schedule) -> dict | None:
    if not schedule.external_id:
        return None
    return {"platform": "youtube", "meta": {"external_id": schedule.external_id}}
