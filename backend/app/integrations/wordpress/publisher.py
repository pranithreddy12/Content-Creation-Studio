"""WordPress publisher — application-password basic auth, posts via REST."""
from __future__ import annotations

import base64
import json

import httpx
import markdown as mdlib

from app.core.security import decrypt
from app.models.content import ContentAsset
from app.models.publishing import PublishChannel, Schedule


def _creds(channel: PublishChannel) -> tuple[str, str, str]:
    blob = channel.oauth_blob if isinstance(channel.oauth_blob, str) else channel.oauth_blob.get("ct", "")
    data = json.loads(decrypt(blob))
    return data["site"], data["username"], data["app_password"]


async def publish(channel: PublishChannel, asset: ContentAsset) -> dict:
    site, user, pw = _creds(channel)
    site = site.rstrip("/")
    html = mdlib.markdown(asset.body or "", extensions=["fenced_code", "tables", "toc"])
    seo = asset.seo or {}
    payload = {
        "title": seo.get("title") or asset.title or "Untitled",
        "slug": seo.get("slug"),
        "status": "publish",
        "content": html,
        "excerpt": seo.get("meta_description"),
    }
    auth = base64.b64encode(f"{user}:{pw}".encode()).decode()
    async with httpx.AsyncClient(timeout=30) as cx:
        r = await cx.post(
            f"{site}/wp-json/wp/v2/posts",
            headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
            json=payload,
        )
        r.raise_for_status()
        data = r.json()
    return {"id": str(data["id"]), "url": data.get("link") or f"{site}/?p={data['id']}"}


async def fetch_metrics(schedule: Schedule) -> dict | None:
    if not schedule.external_id:
        return None
    return {"platform": "wordpress", "meta": {"external_id": schedule.external_id}}
