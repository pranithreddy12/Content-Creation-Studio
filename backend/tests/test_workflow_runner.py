"""Workflow runner end-to-end test.

Spins up a 3-node workflow (trigger → agent.research → agent.writer), mocks the
LLM router, executes it via `run_workflow(...)`, then asserts each node landed
in `workflow_runs.state.steps` with status=ok and the run as a whole completed.
"""
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
from app.services.workflow.runner import run_workflow  # noqa: E402

TAG = f"test_wf_{uuid.uuid4().hex[:8]}"


@pytest.fixture()
async def workflow(monkeypatch):
    """Create Account + Workspace + Brand + Workflow row, then yield workflow_id."""

    # Stub the LLM router so the two agent nodes return canned JSON.
    async def fake(*, system, user, json_schema=None, **kw):
        if "synthesize" in system.lower() or "popular questions" in user.lower():
            payload = {"questions": ["q1"], "trending": ["t1"],
                       "viral_formats": ["carousel"], "keywords": ["kw1"]}
        else:  # writer
            payload = {"title": "Generated", "slug": "generated",
                       "meta_description": "...", "outline": [],
                       "body_markdown": "# Generated\nBody."}
        return LLMResponse(
            text="{}", json_out=payload,
            tokens_in=100, tokens_out=80, cost_usd=0.002,
            model="claude-sonnet-4-6", provider="anthropic", latency_ms=12,
        )

    from app.agents.llm_router import llm_router as router
    monkeypatch.setattr(router, "complete", fake)

    async with SessionLocal() as db:
        acct = Account(clerk_org_id=f"{TAG}_org", name="WF Test", plan="free")
        db.add(acct)
        await db.flush()
        ws = Workspace(account_id=acct.id, name="Default")
        db.add(ws)
        await db.flush()
        brand = Brand(
            account_id=acct.id, workspace_id=ws.id,
            name="WFBrand", slug=f"{TAG}-brand"[:60],
            primary_topic="AI", audience="founders", tone="professional",
        )
        db.add(brand)
        await db.flush()

        wf = Workflow(
            account_id=acct.id, brand_id=brand.id, name="Test Flow",
            trigger={"kind": "schedule", "config": {"cron": "0 9 * * *"}},
            definition={
                "nodes": [
                    {"id": "n1", "kind": "trigger.schedule"},
                    {"id": "n2", "kind": "agent.research"},
                    {"id": "n3", "kind": "agent.writer", "config": {
                        "format": "blog",
                        "idea": {"title": "Generated", "angle": "trend",
                                 "primary_keyword": "ai"},
                        "notes": "",
                    }},
                ],
                "edges": [
                    {"source": "n1", "target": "n2"},
                    {"source": "n2", "target": "n3"},
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
async def test_runner_executes_all_nodes_and_marks_completed(workflow):
    run_id = await run_workflow(workflow, trigger_payload={"trace": "test"})

    async with SessionLocal() as db:
        row = (await db.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))).scalar_one()
        assert row.status == "completed", f"run did not complete: {row.status}, error={row.error}"
        assert row.finished_at is not None

        steps = (row.state or {}).get("steps", {})
        assert set(steps.keys()) == {"n1", "n2", "n3"}, f"steps={steps.keys()}"
        for nid, step in steps.items():
            assert step["status"] == "ok", f"node {nid} status={step['status']} error={step.get('error')}"
            assert step["ms"] is not None
        # The writer node's output should carry the canned title.
        assert steps["n3"]["output"]["title"] == "Generated"
        # The trigger node sees the payload we passed.
        assert steps["n1"]["output"]["trace"] == "test"


@pytest.mark.asyncio
async def test_runner_persists_agent_run_rows(workflow):
    """Each agent.* node should also leave a row in `agent_runs`."""
    from app.models.agent import AgentRun

    run_id = await run_workflow(workflow)

    async with SessionLocal() as db:
        wf = (await db.execute(
            select(Workflow).join(WorkflowRun, WorkflowRun.workflow_id == Workflow.id)
            .where(WorkflowRun.id == run_id)
        )).scalar_one()
        rows = (await db.execute(
            select(AgentRun).where(AgentRun.brand_id == wf.brand_id)
        )).scalars().all()
        names = {r.agent_name for r in rows}
        # Exact agent names per node config.
        assert "research" in names
        assert "writer" in names


@pytest.mark.asyncio
async def test_runner_handles_cycle_via_validate(workflow):
    """Calling run_workflow on a workflow whose definition contains a cycle should raise."""
    async with SessionLocal() as db:
        wf = (await db.execute(select(Workflow).where(Workflow.id == workflow))).scalar_one()
        # Mutate definition to inject a cycle and re-save
        wf.definition = {
            "nodes": [
                {"id": "a", "kind": "agent.research"},
                {"id": "b", "kind": "agent.writer"},
            ],
            "edges": [
                {"source": "a", "target": "b"},
                {"source": "b", "target": "a"},
            ],
        }
        await db.commit()

    with pytest.raises(ValueError, match="cycle"):
        await run_workflow(workflow)
