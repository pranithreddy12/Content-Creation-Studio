"""Email publisher — sends newsletters via the brand's configured provider.

Supports Brevo (Sendinblue) as the default. Additional providers (Mailchimp,
Resend) can register via `app.integrations.publish_registry` overrides.
"""
from __future__ import annotations

import json

import httpx
import markdown as mdlib

from app.core.security import decrypt
from app.models.content import ContentAsset
from app.models.publishing import PublishChannel, Schedule

BREVO_API = "https://api.brevo.com/v3"


def _creds(channel: PublishChannel) -> dict:
    blob = channel.oauth_blob if isinstance(channel.oauth_blob, str) else channel.oauth_blob.get("ct", "")
    return json.loads(decrypt(blob))


async def publish(channel: PublishChannel, asset: ContentAsset) -> dict:
    cfg = _creds(channel)
    api_key = cfg.get("api_key")
    sender = cfg.get("sender", {"name": channel.display_name or "Studio", "email": cfg.get("sender_email")})
    list_ids = cfg.get("list_ids", [])
    body_json = asset.body_json or {}
    subject = body_json.get("subject") or asset.title or "Newsletter"
    md_body = body_json.get("body_markdown") or asset.body or ""
    html_body = mdlib.markdown(md_body, extensions=["fenced_code", "tables"])

    async with httpx.AsyncClient(timeout=30) as cx:
        r = await cx.post(
            f"{BREVO_API}/emailCampaigns",
            headers={"api-key": api_key, "Content-Type": "application/json"},
            json={
                "name": subject[:120],
                "subject": subject[:200],
                "sender": sender,
                "type": "classic",
                "htmlContent": html_body,
                "recipients": {"listIds": list_ids} if list_ids else {},
            },
        )
        r.raise_for_status()
        campaign_id = r.json()["id"]
        send = await cx.post(
            f"{BREVO_API}/emailCampaigns/{campaign_id}/sendNow",
            headers={"api-key": api_key},
        )
        send.raise_for_status()
    return {"id": str(campaign_id), "url": f"brevo://campaign/{campaign_id}"}


async def fetch_metrics(schedule: Schedule) -> dict | None:
    return None if not schedule.external_id else {"platform": "email"}
