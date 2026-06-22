"""Quora has no first-party publishing API.

Quora content is queued in `pending_quora_drafts` and surfaced to humans for manual
posting via the dashboard's Approvals queue. The adapter records the draft and
returns the in-app URL; status is moved to 'published' only after a human marks it.
"""
from __future__ import annotations

from app.models.content import ContentAsset
from app.models.publishing import PublishChannel, Schedule


async def publish(channel: PublishChannel, asset: ContentAsset) -> dict:
    return {"id": f"draft-{asset.id}", "url": f"/dashboard/approvals/{asset.id}"}


async def fetch_metrics(schedule: Schedule) -> dict | None:
    return None
