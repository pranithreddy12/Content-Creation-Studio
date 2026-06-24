"""Workflow approval pause + resume tests."""
from __future__ import annotations

import os
import uuid

import pytest

os.environ.setdefault("SECRET_KEY", "testtesttesttesttesttesttesttest")

from sqlalchemy import select, text  # noqa: E402

from app.agents.llm_router import LLMResponse  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.account import Account, Workspace  # noqa: E402
from app.models.brand import Brand  # noqa: E402
from app.models.workflow import Workflow, WorkflowRun  # noqa: E402
from app.services.workflow.runner import resume_workflow, run_workflow  # noqa: E402

TAG = f"test_appr_{uuid.uuid4().hex[:8]}"


@pytest.fixture()
async def gated_workflow(monkeypatch):
    """research → approval → writer."""

    async def fake_llm(*, system, user, json_schema=None, **kw):
        # Always return research-shape; the writer node also accepts arbitrary JSON
        return LLMResponse(
            text="{}",
            json_out={"questions":["q"],"trending":["t"],"viral_formats":["c"],"keywords":["k"]},
            tokens_in=10, tokens_out=10, cost_usd=0.001,
            model="claude-sonnet-4-6", provider="anthropic", latency_ms=5,
        )

    from app.agents.llm_router import llm_router as router
    monkeypatch.setattr(router, "complete", fake_llm)

    async with SessionLocal() as db:
        acct = Account(clerk_org_id=f"{TAG}_org", name="A", plan="free")
        db.add(acct)
        await db.flush()
        ws = Workspace(account_id=acct.id, name="Default")
        db.add(ws)
        await db.flush()
        brand = Brand(
            account_id=acct.id, workspace_id=ws.id,
            name="ApprBrand", slug=f"{TAG}-b"[:60], primary_topic="AI",
        )
        db.add(brand)
        await db.flush()
        wf = Workflow(
            account_id=acct.id, brand_id=brand.id, name="Gated",
            trigger={"kind": "schedule", "config": {"cron": "0 9 * * *"}},
            definition={
                "nodes": [
                    {"id": "n1", "kind": "trigger.schedule"},
                    {"id": "n2", "kind": "agent.research"},
                    {"id": "n3", "kind": "control.approval"},
                    {"id": "n4", "kind": "agent.writer", "config": {
                        "format": "blog",
                        "idea": {"title": "T", "angle": "x", "primary_keyword": "ai"},
                        "notes": "",
                    }},
                ],
                "edges": [
                    {"source": "n1", "target": "n2"},
                    {"source": "n2", "target": "n3"},
                    {"source": "n3", "target": "n4"},
                ],
            },
        )
        db.add(wf)
        await db.commit()
        wfid = wf.id
    yield wfid
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM accounts WHERE clerk_org_id = :o"), {"o": f"{TAG}_org"})
        await db.commit()


@pytest.mark.asyncio
async def test_approval_node_pauses_run(gated_workflow):
    """run_workflow should stop at the approval node and mark status=waiting_approval."""
    run_id = await run_workflow(gated_workflow, trigger_payload={"why": "test"})

    async with SessionLocal() as db:
        row = (await db.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))).scalar_one()
        assert row.status == "waiting_approval", f"got {row.status}, error={row.error}"
        assert row.finished_at is None, "paused runs must NOT be marked finished"

        steps = (row.state or {}).get("steps", {})
        # n1 + n2 ran cleanly
        assert steps.get("n1", {}).get("status") == "ok"
        assert steps.get("n2", {}).get("status") == "ok"
        # n3 paused
        assert steps.get("n3", {}).get("status") == "waiting_approval"
        assert steps["n3"]["output"]["__approval__"] is True
        # n4 hasn't been executed
        assert "n4" not in steps


@pytest.mark.asyncio
async def test_resume_completes_remaining_nodes(gated_workflow):
    """resume_workflow should pick up after the approval and finish n4."""
    run_id = await run_workflow(gated_workflow, trigger_payload={"why": "test"})

    # Resume with a custom approval payload
    await resume_workflow(gated_workflow, run_id, approval={"approved_by": "qa", "ok": True})

    async with SessionLocal() as db:
        row = (await db.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))).scalar_one()
        assert row.status == "completed", f"final status was {row.status}, error={row.error}"
        assert row.finished_at is not None

        steps = (row.state or {}).get("steps", {})
        # Every node ran, including the writer
        assert set(steps.keys()) == {"n1", "n2", "n3", "n4"}
        assert steps["n3"]["status"] == "ok", "approval node should be promoted to ok"
        assert steps["n3"]["output"]["approved_by"] == "qa"
        assert steps["n4"]["status"] == "ok"


@pytest.mark.asyncio
async def test_resume_refuses_non_paused_runs(gated_workflow):
    """A run not in waiting_approval state should refuse to resume."""
    run_id = await run_workflow(gated_workflow)
    # First resume completes the run.
    await resume_workflow(gated_workflow, run_id, approval={"ok": True})
    # Second resume must refuse.
    with pytest.raises(RuntimeError, match="cannot resume"):
        await resume_workflow(gated_workflow, run_id, approval={"ok": True})


@pytest.mark.asyncio
async def test_resume_default_approval_when_payload_missing(gated_workflow):
    """If resume() is called with approval=None, it should still work using a default truthy payload."""
    run_id = await run_workflow(gated_workflow)
    await resume_workflow(gated_workflow, run_id, approval=None)

    async with SessionLocal() as db:
        row = (await db.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))).scalar_one()
        assert row.status == "completed"
        steps = (row.state or {}).get("steps", {})
        assert steps["n3"]["output"] == {"approved": True}
