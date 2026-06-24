"""baseline schema

Revision ID: 0001_baseline
Revises:
Create Date: 2026-06-23 18:30:00

The single baseline migration. Subsequent migrations build on this. The ORM is
the source of truth: we install the required Postgres extensions and then let
SQLAlchemy create every table declared on `Base.metadata`.

If you alter the ORM, generate a new revision with `alembic revision --autogenerate`.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

import app.models  # noqa: F401  -- side-effect: registers every model on Base.metadata
from app.db.base import Base

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # Required extensions. Idempotent.
    for ext in ("pgcrypto", "pg_trgm", "vector"):
        bind.execute(text(f'CREATE EXTENSION IF NOT EXISTS "{ext}"'))

    # Create every table declared on the metadata.
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    # Drop in reverse dependency order via metadata.
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
