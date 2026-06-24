"""Alembic baseline migration test.

Creates a throwaway DB, runs `alembic upgrade head`, asserts the schema
matches what the ORM expects (all expected tables exist, extensions installed,
version stamped). Then drops the DB.

Runs inside the backend container because that's where alembic + the migration
files live.
"""
from __future__ import annotations

import os
import subprocess
import uuid

import pytest
from sqlalchemy import create_engine, text

ADMIN_URL = "postgresql://studio:studio@postgres:5432/postgres"
ASYNC_TMPL = "postgresql+asyncpg://studio:studio@postgres:5432/{db}"

EXPECTED_TABLES = {
    "accounts", "workspaces", "account_members", "users", "brands",
    "sources", "source_chunks",
    "research_runs", "research_items", "opportunities",
    "content_ideas", "content_assets", "media_assets", "video_renders",
    "publish_channels", "schedules",
    "asset_metrics", "pattern_scores",
    "viral_posts", "viral_patterns",
    "agent_prompts", "agent_runs",
    "workflows", "workflow_runs",
    "plan_limits", "usage_events", "processed_stripe_events",
    "audit_log", "webhooks",
    "notifications", "push_tokens",
    "deletion_jobs",
}

CURRENT_HEAD = "0003_deletion_jobs"


def _admin_exec(sql: str) -> None:
    eng = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        conn.execute(text(sql))
    eng.dispose()


def test_alembic_upgrade_from_empty_db_creates_full_schema():
    db_name = f"studio_alembic_t_{uuid.uuid4().hex[:8]}"
    try:
        _admin_exec(f"CREATE DATABASE {db_name}")

        env = {**os.environ, "DATABASE_URL": ASYNC_TMPL.format(db=db_name)}
        result = subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd="/app", env=env, capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, f"alembic stdout={result.stdout}\nstderr={result.stderr}"

        # Verify schema
        sync_url = ASYNC_TMPL.format(db=db_name).replace("+asyncpg", "")
        check = create_engine(sync_url)
        with check.connect() as conn:
            tables = {r[0] for r in conn.execute(text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_type='BASE TABLE'"
            ))}
            missing = EXPECTED_TABLES - tables
            assert not missing, f"missing tables: {sorted(missing)}"

            exts = {r[0] for r in conn.execute(text("SELECT extname FROM pg_extension"))}
            for required in ("pgcrypto", "pg_trgm", "vector"):
                assert required in exts, f"extension {required} not installed"

            version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            assert version == CURRENT_HEAD
        check.dispose()
    finally:
        try:
            _admin_exec(f"DROP DATABASE IF EXISTS {db_name}")
        except Exception:
            pass


def test_alembic_downgrade_drops_all_tables():
    db_name = f"studio_alembic_d_{uuid.uuid4().hex[:8]}"
    try:
        _admin_exec(f"CREATE DATABASE {db_name}")
        env = {**os.environ, "DATABASE_URL": ASYNC_TMPL.format(db=db_name)}

        up = subprocess.run(["alembic", "upgrade", "head"], cwd="/app", env=env,
                            capture_output=True, text=True, timeout=60)
        assert up.returncode == 0

        down = subprocess.run(["alembic", "downgrade", "base"], cwd="/app", env=env,
                              capture_output=True, text=True, timeout=60)
        assert down.returncode == 0, f"downgrade stderr={down.stderr}"

        sync_url = ASYNC_TMPL.format(db=db_name).replace("+asyncpg", "")
        check = create_engine(sync_url)
        with check.connect() as conn:
            count = conn.execute(text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema='public' AND table_type='BASE TABLE' "
                "AND table_name != 'alembic_version'"
            )).scalar_one()
            assert count == 0, f"downgrade left {count} tables behind"
        check.dispose()
    finally:
        try:
            _admin_exec(f"DROP DATABASE IF EXISTS {db_name}")
        except Exception:
            pass
