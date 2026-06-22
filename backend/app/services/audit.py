"""Append-only audit log writer.

Every security-sensitive write (key rotation, OAuth connect/disconnect,
plan change, publish, role change) calls record(...).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog


async def record(
    db: AsyncSession,
    *,
    action: str,
    target: str | None = None,
    account_id: UUID | None = None,
    user_id: UUID | None = None,
    brand_id: UUID | None = None,
    data: dict[str, Any] | None = None,
    ip: str | None = None,
    ua: str | None = None,
) -> None:
    db.add(AuditLog(
        account_id=account_id,
        user_id=user_id,
        brand_id=brand_id,
        action=action,
        target=target,
        data=data or {},
        ip=ip,
        ua=ua,
        occurred_at=datetime.now(timezone.utc),
    ))
    await db.commit()
