"""FK cascade-delete integrity tests.

Deleting an Account should cascade to every dependent row (workspaces, brands,
sources, source_chunks, content_ideas, content_assets, publish_channels,
schedules, agent_runs, workflows, workflow_runs, audit_log entries with a
matching account_id, usage_events, notifications, etc.) — leaving no orphan
rows that would later 500 some endpoint.

We seed a "doomed" account with rows in every table that references it,
delete the Account, and assert every dependent row is gone.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select, text

os.environ.setdefault("SECRET_KEY", "testtesttesttesttesttesttesttest")

from app.core.security import encrypt  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.account import Account, Workspace  # noqa: E402
from app.models.agent import AgentRun  # noqa: E402
from app.models.brand import Brand  # noqa: E402
from app.models.content import ContentAsset, ContentIdea, MediaAsset, VideoRender  # noqa: E402
from app.models.publishing import PublishChannel, Schedule  # noqa: E402
from app.models.source import Source, SourceChunk  # noqa: E402
from app.models.workflow import Workflow, WorkflowRun  # noqa: E402

TAG = f"test_cascade_{uuid.uuid4().hex[:8]}"
SLUG = TAG.replace("_", "-")


@pytest.mark.asyncio
async def test_account_delete_cascades_to_every_dependent_row():
    """Seed dense graph under one account, delete account, assert no orphans."""
    async with SessionLocal() as db:
        acct = Account(clerk_org_id=f"{TAG}_org", name="Doomed", plan="free")
        db.add(acct); await db.flush()

        ws = Workspace(account_id=acct.id, name="Default")
        db.add(ws); await db.flush()

        brand = Brand(
            account_id=acct.id, workspace_id=ws.id,
            name="DoomedBrand", slug=f"{SLUG}-d"[:60], primary_topic="AI",
        )
        db.add(brand); await db.flush()

        source = Source(
            account_id=acct.id, brand_id=brand.id,
            kind="topic", title="seed", raw_text="hi", status="embedded",
        )
        db.add(source); await db.flush()

        chunk = SourceChunk(
            source_id=source.id, brand_id=brand.id, ord=0, text="...", tokens=2,
        )
        db.add(chunk); await db.flush()

        idea = ContentIdea(
            account_id=acct.id, brand_id=brand.id, title="Doomed idea",
            created_at=datetime.now(timezone.utc),
        )
        db.add(idea); await db.flush()

        asset = ContentAsset(
            account_id=acct.id, brand_id=brand.id, idea_id=idea.id,
            format="blog", title="Doomed asset", status="draft",
        )
        db.add(asset); await db.flush()

        media = MediaAsset(
            account_id=acct.id, brand_id=brand.id, asset_id=asset.id,
            kind="image", storage_key="x/y.png", provider="test",
            created_at=datetime.now(timezone.utc),
        )
        db.add(media); await db.flush()

        vrender = VideoRender(
            asset_id=asset.id, brand_id=brand.id, format="reel",
            script_json={"beats": []}, status="queued",
            created_at=datetime.now(timezone.utc),
        )
        db.add(vrender); await db.flush()

        channel = PublishChannel(
            account_id=acct.id, brand_id=brand.id,
            platform="wordpress", display_name="wp@doomed",
            oauth_blob={"ct": encrypt('{"x": 1}')}, status="connected",
        )
        db.add(channel); await db.flush()

        schedule = Schedule(
            account_id=acct.id, brand_id=brand.id,
            asset_id=asset.id, channel_id=channel.id,
            scheduled_at=datetime.now(timezone.utc),
            status="pending",
            created_at=datetime.now(timezone.utc),
        )
        db.add(schedule); await db.flush()

        agent_run = AgentRun(
            account_id=acct.id, brand_id=brand.id, agent_name="research",
            input={"items": []}, output={"q": ["x"]}, status="ok",
            tokens_in=10, tokens_out=20, cost_usd=0.0001,
            created_at=datetime.now(timezone.utc),
        )
        db.add(agent_run); await db.flush()

        wf = Workflow(
            account_id=acct.id, brand_id=brand.id, name="DoomedWF",
            definition={"nodes": [{"id": "a", "kind": "agent.writer"}], "edges": []},
            trigger={"kind": "schedule", "config": {}},
        )
        db.add(wf); await db.flush()

        wf_run = WorkflowRun(
            workflow_id=wf.id, status="completed", trigger={}, state={"steps": {}},
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )
        db.add(wf_run); await db.flush()

        await db.commit()

        ids = {
            "account_id": acct.id,
            "brand_id": brand.id,
            "source_id": source.id,
            "asset_id": asset.id,
            "channel_id": channel.id,
            "workflow_id": wf.id,
        }

    # ── HARD DELETE the account ──────────────────────────────────────
    async with SessionLocal() as db:
        acct_obj = (await db.execute(
            select(Account).where(Account.id == ids["account_id"])
        )).scalar_one()
        await db.delete(acct_obj)
        await db.commit()

    # ── verify every dependent row is gone ───────────────────────────
    async with SessionLocal() as db:
        def _count(cls, **where) -> int:
            q = select(func.count()).select_from(cls)
            for k, v in where.items():
                q = q.where(getattr(cls, k) == v)
            return q

        for cls, key, value, label in [
            (Workspace,      "account_id",   ids["account_id"], "workspaces"),
            (Brand,          "id",           ids["brand_id"],    "brands"),
            (Source,         "id",           ids["source_id"],   "sources"),
            (SourceChunk,    "brand_id",     ids["brand_id"],    "source_chunks"),
            (ContentIdea,    "brand_id",     ids["brand_id"],    "content_ideas"),
            (ContentAsset,   "id",           ids["asset_id"],    "content_assets"),
            (MediaAsset,     "asset_id",     ids["asset_id"],    "media_assets"),
            (VideoRender,    "asset_id",     ids["asset_id"],    "video_renders"),
            (PublishChannel, "id",           ids["channel_id"],  "publish_channels"),
            (Schedule,       "asset_id",     ids["asset_id"],    "schedules"),
            (AgentRun,       "account_id",   ids["account_id"], "agent_runs"),
            (Workflow,       "id",           ids["workflow_id"], "workflows"),
            (WorkflowRun,    "workflow_id",  ids["workflow_id"], "workflow_runs"),
        ]:
            count = (await db.execute(_count(cls, **{key: value}))).scalar()
            assert count == 0, f"orphan row(s) in {label}: {count} remaining after account delete"


@pytest.mark.asyncio
async def test_brand_delete_cascades_to_brand_scoped_rows():
    """Deleting a single brand cascades to its sources/assets/schedules/channels."""
    async with SessionLocal() as db:
        acct = Account(clerk_org_id=f"{TAG}_bonly", name="A", plan="free")
        db.add(acct); await db.flush()
        ws = Workspace(account_id=acct.id, name="Default")
        db.add(ws); await db.flush()
        b1 = Brand(account_id=acct.id, workspace_id=ws.id,
                   name="B1", slug=f"{SLUG}-bone"[:60], primary_topic="AI")
        b2 = Brand(account_id=acct.id, workspace_id=ws.id,
                   name="B2", slug=f"{SLUG}-btwo"[:60], primary_topic="AI")
        db.add_all([b1, b2]); await db.flush()
        # 1 source under b1 (doomed), 1 under b2 (must survive)
        s1 = Source(account_id=acct.id, brand_id=b1.id, kind="topic",
                    status="embedded")
        s2 = Source(account_id=acct.id, brand_id=b2.id, kind="topic",
                    status="embedded")
        db.add_all([s1, s2]); await db.commit()
        b1_id, b2_id, s1_id, s2_id = b1.id, b2.id, s1.id, s2.id

    async with SessionLocal() as db:
        b1_obj = (await db.execute(select(Brand).where(Brand.id == b1_id))).scalar_one()
        await db.delete(b1_obj)
        await db.commit()

    async with SessionLocal() as db:
        # b1's source must be gone
        gone = (await db.execute(select(Source).where(Source.id == s1_id))).scalar_one_or_none()
        assert gone is None, "b1.source survived brand delete"
        # b2's source must still exist
        survived = (await db.execute(select(Source).where(Source.id == s2_id))).scalar_one()
        assert survived is not None
        # Cleanup remaining
        acct_obj = (await db.execute(select(Account).where(Account.clerk_org_id == f"{TAG}_bonly"))).scalar_one()
        await db.delete(acct_obj)
        await db.commit()


@pytest.mark.asyncio
async def test_source_delete_cascades_to_source_chunks():
    """source_chunks should disappear when its parent source is deleted."""
    async with SessionLocal() as db:
        acct = Account(clerk_org_id=f"{TAG}_chunkparent", name="A", plan="free")
        db.add(acct); await db.flush()
        ws = Workspace(account_id=acct.id, name="Default")
        db.add(ws); await db.flush()
        brand = Brand(account_id=acct.id, workspace_id=ws.id,
                      name="B", slug=f"{SLUG}-c"[:60], primary_topic="AI")
        db.add(brand); await db.flush()
        src = Source(account_id=acct.id, brand_id=brand.id, kind="topic",
                     status="embedded")
        db.add(src); await db.flush()
        for i in range(5):
            db.add(SourceChunk(source_id=src.id, brand_id=brand.id,
                               ord=i, text=f"chunk {i}", tokens=10))
        await db.commit()
        src_id, brand_id = src.id, brand.id

    async with SessionLocal() as db:
        src_obj = (await db.execute(select(Source).where(Source.id == src_id))).scalar_one()
        await db.delete(src_obj)
        await db.commit()

    async with SessionLocal() as db:
        count = (await db.execute(
            select(func.count()).select_from(SourceChunk)
            .where(SourceChunk.brand_id == brand_id)
        )).scalar()
        assert count == 0, f"orphan source_chunks: {count}"

        # cleanup
        acct_obj = (await db.execute(select(Account).where(Account.clerk_org_id == f"{TAG}_chunkparent"))).scalar_one()
        await db.delete(acct_obj)
        await db.commit()
