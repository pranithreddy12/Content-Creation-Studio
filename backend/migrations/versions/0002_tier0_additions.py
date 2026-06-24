"""Tier-0 additions: processed_stripe_events + schedules.claimed_at

Revision ID: 0002_tier0_additions
Revises: 0001_baseline
Create Date: 2026-06-24

The 0001 baseline uses ``Base.metadata.create_all``, so a *fresh* install already
has these objects (create_all reflects the current models). But an environment
provisioned BEFORE these models existed ran 0001 when they were absent — and
``create_all`` only creates missing tables at the moment it runs, so it never
back-fills them. autogenerate cannot see this drift (a freshly-upgraded DB
matches the models exactly), which is exactly why this explicit migration exists.

It is written idempotently (IF [NOT] EXISTS) so it is a safe no-op on a fresh
install and an additive upgrade on a pre-existing one.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

from app.core.logging import log

revision = "0002_tier0_additions"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # Stripe webhook idempotency ledger (added with dedup work).
    bind.execute(text("""
        CREATE TABLE IF NOT EXISTS processed_stripe_events (
            event_id    VARCHAR PRIMARY KEY,
            type        VARCHAR NOT NULL,
            received_at TIMESTAMPTZ NOT NULL
        )
    """))
    # Publish-claim timestamp the reaper measures abandonment from.
    bind.execute(text(
        "ALTER TABLE schedules ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ"
    ))


def downgrade() -> None:
    # Intentional no-op. The 0001 baseline is create_all-based: on any environment
    # that ran 0001 *after* these models existed, `processed_stripe_events` and
    # `schedules.claimed_at` were created BY 0001's create_all, not exclusively by
    # this migration. A naive drop here would therefore destroy objects that
    # logically belong to the baseline — and `processed_stripe_events` holds the
    # Stripe idempotency ledger, so dropping it on a `downgrade 0002` would silently
    # lose dedup history and risk re-applying past webhook events.
    #
    # Because ownership of these objects is ambiguous under a create_all baseline,
    # the safe choice is to NOT drop on downgrade. (When the baseline is eventually
    # frozen into explicit create_table ops, this can become a real reversal.)
    log.warning(
        "migration_0002_downgrade_noop",
        detail="not dropping processed_stripe_events / schedules.claimed_at — "
               "create_all baseline makes ownership ambiguous; dropping could lose data",
    )
