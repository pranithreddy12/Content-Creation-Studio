"""deletion_jobs table (resumable account purge ledger)

Revision ID: 0003_deletion_jobs
Revises: 0002_tier0_additions
Create Date: 2026-06-24

Adds the deletion_jobs table backing hard account deletion. Idempotent
(CREATE TABLE IF NOT EXISTS) so it is a safe no-op on a fresh install where the
0001 baseline's create_all already built it, and an additive upgrade on a
pre-existing environment. Downgrade is an intentional no-op, consistent with
0002: under a create_all baseline the table's ownership is ambiguous and a
deletion-audit ledger should not be dropped on a routine downgrade.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

from app.core.logging import log

revision = "0003_deletion_jobs"
down_revision = "0002_tier0_additions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(text("""
        CREATE TABLE IF NOT EXISTS deletion_jobs (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            account_id    UUID NOT NULL,
            status        VARCHAR NOT NULL DEFAULT 'pending',
            brand_ids     JSONB DEFAULT '[]'::jsonb,
            qdrant_done   BOOLEAN NOT NULL DEFAULT false,
            minio_done    BOOLEAN NOT NULL DEFAULT false,
            redis_done    BOOLEAN NOT NULL DEFAULT false,
            postgres_done BOOLEAN NOT NULL DEFAULT false,
            error         TEXT,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))
    bind.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_deletion_jobs_account_id ON deletion_jobs (account_id)"
    ))


def downgrade() -> None:
    log.warning(
        "migration_0003_downgrade_noop",
        detail="not dropping deletion_jobs — create_all baseline makes ownership "
               "ambiguous and the purge-audit ledger should survive a downgrade",
    )
