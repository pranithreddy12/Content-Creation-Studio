"""Instagram Graph API publisher — image, carousel, reel.

Requires a first-party IG Business / Creator account linked to a FB Page.
Two-step publish: create media container → publish container.
"""
from __future__ import annotations

import json

import httpx
from sqlalchemy import select

from app.core.security import decrypt
from app.db.session import SessionLocal
from app.models.content import ContentAsset, MediaAsset
from app.models.publishing import PublishChannel, Schedule
from app.utils.storage import presign

API = "https://graph.facebook.com/v18.0"


def _tokens(channel: PublishChannel) -> dict:
    blob = channel.oauth_blob if isinstance(channel.oauth_blob, str) else channel.oauth_blob.get("ct", "")
    return json.loads(decrypt(blob))


async def _ig_user_id(token: str) -> str:
    async with httpx.AsyncClient(timeout=15) as cx:
        r = await cx.get(f"{API}/me/accounts", params={"access_token": token})
        r.raise_for_status()
        pages = r.json().get("data", [])
        if not pages:
            raise RuntimeError("no FB pages bound")
        page_id = pages[0]["id"]
        page_token = pages[0]["access_token"]
        r2 = await cx.get(f"{API}/{page_id}", params={"fields": "instagram_business_account",
                                                       "access_token": page_token})
        ig = r2.json().get("instagram_business_account") or {}
        if not ig.get("id"):
            raise RuntimeError("no IG business account linked")
        return ig["id"]


async def _media_urls(asset_id: str) -> list[str]:
    async with SessionLocal() as db:
        rows = (await db.execute(
            select(MediaAsset).where(MediaAsset.asset_id == asset_id)
        )).scalars().all()
    return [presign(m.storage_key) for m in rows if m.storage_key]


async def publish(channel: PublishChannel, asset: ContentAsset) -> dict:
    tok = _tokens(channel)["access_token"]
    ig_user = await _ig_user_id(tok)
    media = await _media_urls(str(asset.id))
    caption = asset.body or asset.title or ""

    async with httpx.AsyncClient(timeout=30) as cx:
        if asset.format == "carousel" and len(media) >= 2:
            children: list[str] = []
            for url in media[:10]:
                r = await cx.post(
                    f"{API}/{ig_user}/media",
                    data={"image_url": url, "is_carousel_item": "true", "access_token": tok},
                )
                r.raise_for_status()
                children.append(r.json()["id"])
            r = await cx.post(
                f"{API}/{ig_user}/media",
                data={"media_type": "CAROUSEL", "caption": caption,
                      "children": ",".join(children), "access_token": tok},
            )
        elif asset.format in {"reel", "short", "tiktok"} and media:
            r = await cx.post(
                f"{API}/{ig_user}/media",
                data={"media_type": "REELS", "video_url": media[0],
                      "caption": caption, "access_token": tok},
            )
        else:
            if not media:
                raise RuntimeError("no media generated yet for IG post")
            r = await cx.post(
                f"{API}/{ig_user}/media",
                data={"image_url": media[0], "caption": caption, "access_token": tok},
            )
        r.raise_for_status()
        container_id = r.json()["id"]
        pub = await cx.post(
            f"{API}/{ig_user}/media_publish",
            data={"creation_id": container_id, "access_token": tok},
        )
        pub.raise_for_status()
        post_id = pub.json()["id"]
    return {"id": post_id, "url": f"https://www.instagram.com/p/{post_id}"}


async def fetch_metrics(schedule: Schedule) -> dict | None:
    if not schedule.external_id:
        return None
    return {"platform": "instagram", "meta": {"external_id": schedule.external_id}}
