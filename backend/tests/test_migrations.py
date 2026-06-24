"""Incremental migration safety: 0001 → 0002 up / down / up.

The 0001 baseline is create_all-based, so autogenerate can't detect drift for
already-provisioned environments. 0002 is the first real incremental migration
(adds processed_stripe_events + schedules.claimed_at) and must survive a full
upgrade→downgrade→upgrade cycle on a throwaway DB.
"""
from __future__ import annotations

import os
import subprocess
import uuid

from sqlalchemy import create_engine, text

ADMIN_URL = "postgresql://studio:studio@postgres:5432/postgres"
ASYNC_TMPL = "postgresql+asyncpg://studio:studio@postgres:5432/{db}"
HEAD = "0002_tier0_additions"


def _admin_exec(sql: str) -> None:
    eng = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        conn.execute(text(sql))
    eng.dispose()


def _alembic(env, *args) -> subprocess.CompletedProcess:
    return subprocess.run(["alembic", *args], cwd="/app", env=env,
                          capture_output=True, text=True, timeout=90)


def _column_exists(url: str, table: str, column: str) -> bool:
    eng = create_engine(url.replace("+asyncpg", ""))
    try:
        with eng.connect() as conn:
            return conn.execute(text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name=:t AND column_name=:c"
            ), {"t": table, "c": column}).first() is not None
    finally:
        eng.dispose()


def _table_exists(url: str, table: str) -> bool:
    eng = create_engine(url.replace("+asyncpg", ""))
    try:
        with eng.connect() as conn:
            return conn.execute(text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_name=:t AND table_schema='public'"
            ), {"t": table}).first() is not None
    finally:
        eng.dispose()


def _version(url: str) -> str:
    eng = create_engine(url.replace("+asyncpg", ""))
    try:
        with eng.connect() as conn:
            return conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    finally:
        eng.dispose()


def test_0002_stepwise_up_down_up_on_preexisting_environment():
    """The REAL pre-existing-environment scenario, stepping the migration commands
    one at a time (not a create_all shortcut that makes 0002 a trivial no-op):

      (a) empty DB
      (b) upgrade 0001_baseline      → create_all builds the full schema, including
                                        processed_stripe_events + schedules.claimed_at
      (c) upgrade 0002               → IF NOT EXISTS path: a clean no-op, version→0002
      (d) downgrade 0001_baseline    → 0002 downgrade is an intentional no-op; the
                                        Stripe idempotency ledger and its data SURVIVE
      (e) upgrade 0002 again         → clean, version→0002

    Crucially asserts that a row written to processed_stripe_events survives the
    downgrade — proving the downgrade no longer destroys real data.
    """
    db = f"studio_mig_{uuid.uuid4().hex[:8]}"
    url = ASYNC_TMPL.format(db=db)
    env = {**os.environ, "DATABASE_URL": url}
    try:
        _admin_exec(f"CREATE DATABASE {db}")

        # (b) baseline only
        b = _alembic(env, "upgrade", "0001_baseline")
        assert b.returncode == 0, b.stderr
        assert _version(url) == "0001_baseline"
        assert _table_exists(url, "processed_stripe_events"), "create_all builds it at baseline"
        assert _column_exists(url, "schedules", "claimed_at")

        # Seed a real ledger row to prove the downgrade preserves data.
        eng = create_engine(url.replace("+asyncpg", ""))
        with eng.begin() as conn:
            conn.execute(text(
                "INSERT INTO processed_stripe_events (event_id, type, received_at) "
                "VALUES ('evt_survive', 'customer.subscription.updated', now())"
            ))
        eng.dispose()

        # (c) upgrade to 0002 — must be a clean no-op
        c = _alembic(env, "upgrade", "0002_tier0_additions")
        assert c.returncode == 0, c.stderr
        assert _version(url) == HEAD
        assert _table_exists(url, "processed_stripe_events")
        assert _column_exists(url, "schedules", "claimed_at")

        # (d) downgrade to baseline — no-op for the objects; DATA MUST SURVIVE
        d = _alembic(env, "downgrade", "0001_baseline")
        assert d.returncode == 0, d.stderr
        assert _version(url) == "0001_baseline"
        assert _table_exists(url, "processed_stripe_events"), \
            "downgrade must NOT drop the idempotency ledger (create_all owns it)"
        assert _column_exists(url, "schedules", "claimed_at")
        eng = create_engine(url.replace("+asyncpg", ""))
        with eng.connect() as conn:
            surviving = conn.execute(text(
                "SELECT count(*) FROM processed_stripe_events WHERE event_id='evt_survive'"
            )).scalar_one()
        eng.dispose()
        assert surviving == 1, "downgrade silently destroyed ledger data — data-loss bug"

        # (e) re-upgrade
        e = _alembic(env, "upgrade", "0002_tier0_additions")
        assert e.returncode == 0, e.stderr
        assert _version(url) == HEAD
    finally:
        try:
            _admin_exec(f"DROP DATABASE IF EXISTS {db}")
        except Exception:
            pass


def test_0002_is_idempotent_on_fresh_install():
    """On a fresh DB, 0001 create_all already made the objects; 0002 must be a
    safe no-op (IF NOT EXISTS), not a failure."""
    db = f"studio_migidem_{uuid.uuid4().hex[:8]}"
    url = ASYNC_TMPL.format(db=db)
    env = {**os.environ, "DATABASE_URL": url}
    try:
        _admin_exec(f"CREATE DATABASE {db}")
        # A single `upgrade head` runs 0001 (create_all makes everything incl. the
        # 0002 objects) then 0002 on top — proving 0002 tolerates pre-existing objects.
        up = _alembic(env, "upgrade", "head")
        assert up.returncode == 0, f"0002 not idempotent over create_all: {up.stderr}"
        assert _table_exists(url, "processed_stripe_events")
        assert _column_exists(url, "schedules", "claimed_at")
    finally:
        try:
            _admin_exec(f"DROP DATABASE IF EXISTS {db}")
        except Exception:
            pass
