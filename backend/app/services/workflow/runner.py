"""Workflow runner — topological async execution with per-node context propagation.

Persists each run to `workflow_runs.state.steps[node_id] = {status, output, error, ms}`.
Approval nodes pause the run by writing status='waiting_approval'; a /workflows/{id}/runs/{run_id}/resume
endpoint resumes it.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import select

from app.agents.base import AgentContext
from app.agents.registry import get_agent
from app.agents.runner import run_agent
from app.db.session import SessionLocal
from app.models.brand import Brand
from app.models.workflow import Workflow, WorkflowRun
from app.services.workflow.schema import NodeDef, WorkflowDef, validate_workflow

# Per-run ceilings so a cyclic or runaway flow (e.g. Writer↔SEO) can't run
# unbounded even while individual calls stay within budget. Terminal when hit.
MAX_RUN_STEPS = 200
MAX_RUN_USD = 25.0


class WorkflowCeilingExceeded(RuntimeError):
    """Raised when a run exceeds its step or cumulative-cost ceiling."""


async def resume_workflow(
    workflow_id: UUID,
    run_id: UUID,
    approval: dict | None = None,
) -> None:
    """Continue a workflow that paused at a `control.approval` node.

    Replays the recorded `state.steps` to rebuild upstream outputs, then resumes
    execution from the approved node onward. The approval payload (`approval`)
    becomes the approval node's effective output for downstream consumers.
    """
    async with SessionLocal() as db:
        wf = (await db.execute(select(Workflow).where(Workflow.id == workflow_id))).scalar_one()
        run = (await db.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))).scalar_one()
    if run.status != "waiting_approval":
        raise RuntimeError(f"cannot resume run in state '{run.status}'")
    defn = validate_workflow(wf.definition)

    # Reconstruct prior outputs from persisted steps; for the approval node treat
    # the user-supplied payload as its output.
    prior_steps = (run.state or {}).get("steps", {})
    outputs: dict[str, dict] = {}
    completed_ids: set[str] = set()
    approved_nodes: list[str] = []
    for nid, step in prior_steps.items():
        if step.get("status") == "ok":
            outputs[nid] = step.get("output") or {}
            completed_ids.add(nid)
        elif step.get("status") == "waiting_approval":
            outputs[nid] = approval if approval is not None else {"approved": True}
            completed_ids.add(nid)
            approved_nodes.append(nid)
            # promote the step to ok
            await _mark_step(run_id, nid, status="ok", output=outputs[nid], ms=0)

    await _set_run_status(run_id, "running")
    try:
        await _resume(workflow_id, run_id, defn, run.trigger or {}, outputs, completed_ids)
    except Exception as exc:
        async with SessionLocal() as db:
            row = (await db.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))).scalar_one()
            row.status = "failed"
            row.error = str(exc)[:2000]
            row.finished_at = datetime.now(timezone.utc)
            await db.commit()
        raise


async def _resume(
    workflow_id: UUID,
    run_id: UUID,
    defn: WorkflowDef,
    payload: dict,
    outputs: dict[str, dict],
    completed_ids: set[str],
) -> None:
    """Execute remaining nodes given a partial output map (no DAG rewind)."""
    indeg = {n.id: 0 for n in defn.nodes}
    for e in defn.edges:
        indeg[e.target] += 1
    # Decrement indeg for any already-completed predecessor.
    ready: asyncio.Queue[str] = asyncio.Queue()
    for nid in list(indeg.keys()):
        if nid in completed_ids:
            indeg[nid] = -1   # mark done
            continue
        preds_done = sum(1 for e in defn.edges if e.target == nid and e.source in completed_ids)
        indeg[nid] -= preds_done
        if indeg[nid] == 0:
            await ready.put(nid)
    completed = sum(1 for v in indeg.values() if v == -1)
    steps_run = 0
    run_cost = 0.0
    while completed < len(defn.nodes):
        if ready.empty():
            break  # blocked / nothing more to do
        nid = await ready.get()
        node = defn.node(nid)

        steps_run += 1
        if steps_run > MAX_RUN_STEPS:
            await _mark_step(run_id, nid, status="error", error="run step ceiling exceeded", ms=0)
            raise WorkflowCeilingExceeded(f"exceeded {MAX_RUN_STEPS} steps")

        preds = [e.source for e in defn.edges if e.target == nid]
        node_input = _merge_inputs([outputs.get(p, {}) for p in preds], payload)
        t0 = time.perf_counter()
        try:
            result, node_cost = await _exec_node(workflow_id, node, node_input)
        except Exception as exc:
            await _mark_step(run_id, nid, status="error", error=str(exc),
                             ms=int((time.perf_counter() - t0) * 1000))
            raise
        ms = int((time.perf_counter() - t0) * 1000)
        run_cost += node_cost
        if run_cost > MAX_RUN_USD:
            await _mark_step(run_id, nid, status="error",
                             error=f"run cost ceiling exceeded (${run_cost:.2f})", ms=ms)
            raise WorkflowCeilingExceeded(f"exceeded ${MAX_RUN_USD:.2f} cumulative")
        outputs[nid] = result
        completed += 1
        indeg[nid] = -1
        await _mark_step(run_id, nid, status="ok", output=result, ms=ms)
        for e in defn.out_edges(nid):
            if e.when and not _eval_when(e.when, result):
                continue
            indeg[e.target] -= 1
            if indeg[e.target] == 0:
                await ready.put(e.target)

    async with SessionLocal() as db:
        row = (await db.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))).scalar_one()
        row.status = "completed"
        row.finished_at = datetime.now(timezone.utc)
        await db.commit()


async def run_workflow(workflow_id: UUID, trigger_payload: dict | None = None) -> UUID:
    async with SessionLocal() as db:
        wf = (await db.execute(select(Workflow).where(Workflow.id == workflow_id))).scalar_one()
        defn = validate_workflow(wf.definition)
        run = WorkflowRun(
            workflow_id=wf.id,
            status="running",
            trigger=trigger_payload or {},
            state={"steps": {}},
            started_at=datetime.now(timezone.utc),
        )
        db.add(run)
        await db.flush()
        run_id = run.id
        await db.commit()

    try:
        await _execute(workflow_id, run_id, defn, trigger_payload or {})
    except Exception as exc:
        async with SessionLocal() as db:
            row = (await db.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))).scalar_one()
            row.status = "failed"
            row.error = str(exc)[:2000]
            row.finished_at = datetime.now(timezone.utc)
            await db.commit()
        raise
    return run_id


async def _execute(
    workflow_id: UUID,
    run_id: UUID,
    defn: WorkflowDef,
    payload: dict,
) -> None:
    # Topological scheduling — every node runs when all its predecessors finish.
    indeg: dict[str, int] = {n.id: 0 for n in defn.nodes}
    for e in defn.edges:
        indeg[e.target] += 1

    outputs: dict[str, dict] = {}
    ready: asyncio.Queue[str] = asyncio.Queue()
    for nid, d in indeg.items():
        if d == 0:
            await ready.put(nid)

    in_flight = 0
    completed = 0
    steps_run = 0
    run_cost = 0.0
    waiting_approval: list[str] = []

    while completed + len(waiting_approval) < len(defn.nodes):
        if ready.empty() and in_flight == 0:
            break  # blocked
        nid = await ready.get()
        node = defn.node(nid)

        # Loop guard: bound total steps so a cyclic flow can't churn forever.
        steps_run += 1
        if steps_run > MAX_RUN_STEPS:
            await _mark_step(run_id, nid, status="error", error="run step ceiling exceeded", ms=0)
            raise WorkflowCeilingExceeded(f"exceeded {MAX_RUN_STEPS} steps")

        # gather inputs from predecessors
        preds = [e.source for e in defn.edges if e.target == nid]
        node_input = _merge_inputs([outputs.get(p, {}) for p in preds], payload)

        t0 = time.perf_counter()
        try:
            result, node_cost = await _exec_node(workflow_id, node, node_input)
        except Exception as exc:
            await _mark_step(run_id, nid, status="error", error=str(exc), ms=int((time.perf_counter() - t0) * 1000))
            raise
        ms = int((time.perf_counter() - t0) * 1000)

        run_cost += node_cost
        if run_cost > MAX_RUN_USD:
            await _mark_step(run_id, nid, status="error",
                             error=f"run cost ceiling exceeded (${run_cost:.2f})", ms=ms)
            raise WorkflowCeilingExceeded(f"exceeded ${MAX_RUN_USD:.2f} cumulative")

        if result.get("__approval__"):
            await _mark_step(run_id, nid, status="waiting_approval", output=result, ms=ms)
            waiting_approval.append(nid)
            await _set_run_status(run_id, "waiting_approval")
            return  # pause — resume() must continue

        outputs[nid] = result
        completed += 1
        await _mark_step(run_id, nid, status="ok", output=result, ms=ms)

        for e in defn.out_edges(nid):
            if e.when and not _eval_when(e.when, result):
                continue
            indeg[e.target] -= 1
            if indeg[e.target] == 0:
                await ready.put(e.target)

    async with SessionLocal() as db:
        row = (await db.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))).scalar_one()
        row.status = "completed"
        row.finished_at = datetime.now(timezone.utc)
        await db.commit()


def _merge_inputs(prior: list[dict], trigger: dict) -> dict:
    out: dict = {"trigger": trigger}
    for p in prior:
        out.update(p)
    return out


def _eval_when(predicate: dict, value: Any) -> bool:
    """Tiny safe expression: {"eq": ["$.status", "ok"]} | {"gt": ["$.score", 0.5]}.

    Operators are evaluated lazily so a predicate like {"eq": ["$.foo", 42]} doesn't
    crash trying to compute `a in (b or [])` against an int.
    """
    if not predicate:
        return True
    op, args = next(iter(predicate.items()))
    a, b = (_resolve(args[0], value), _resolve(args[1], value))
    try:
        if op == "eq":  return a == b
        if op == "neq": return a != b
        if op == "gt":  return (a or 0) > (b or 0)
        if op == "gte": return (a or 0) >= (b or 0)
        if op == "lt":  return (a or 0) < (b or 0)
        if op == "lte": return (a or 0) <= (b or 0)
        if op == "in":  return a in (b or [])
    except TypeError:
        return False
    return False


def _resolve(token: Any, root: Any) -> Any:
    if isinstance(token, str) and token.startswith("$."):
        path = token[2:].split(".")
        cur: Any = root
        for k in path:
            if isinstance(cur, dict):
                cur = cur.get(k)
            else:
                return None
        return cur
    return token


async def _exec_node(workflow_id: UUID, node: NodeDef, node_input: dict) -> tuple[dict, float]:
    """Execute one node. Returns (output, cost_usd). cost is 0.0 for non-agent nodes."""
    family, sub = node.kind.split(".", 1)

    if family == "trigger":
        return {"triggered": True, **(node_input.get("trigger") or {})}, 0.0

    if family == "agent":
        async with SessionLocal() as db:
            wf = (await db.execute(select(Workflow).where(Workflow.id == workflow_id))).scalar_one()
            brand = (await db.execute(select(Brand).where(Brand.id == wf.brand_id))).scalar_one() if wf.brand_id else None
        if brand is None:
            raise RuntimeError("agent node requires workflow.brand_id")
        agent = get_agent(sub)
        ctx = AgentContext(
            account_id=brand.account_id,
            brand_id=brand.id,
            brand={"name": brand.name, "tone": brand.tone, "audience": brand.audience,
                   "primary_topic": brand.primary_topic, "style_guide": brand.style_guide},
            inputs={**node_input, **(node.config or {})},
        )
        async with SessionLocal() as db:
            result = await run_agent(db, agent, ctx)
        return result.output, float(result.cost_usd or 0.0)

    if family == "control":
        if sub == "condition":
            return {"matched": _eval_when(node.config.get("when", {}), node_input)}, 0.0
        if sub == "loop":
            n = int(node.config.get("times", 1))
            return {"iterations": n, "input": node_input}, 0.0
        if sub == "approval":
            return {"__approval__": True, "for": node_input}, 0.0
        return {}, 0.0

    if family == "effect":
        if sub == "http":
            url = node.config.get("url")
            method = (node.config.get("method") or "POST").upper()
            async with httpx.AsyncClient(timeout=20) as cx:
                r = await cx.request(method, url, json=node.config.get("body") or node_input)
            return {"status": r.status_code, "body": _safe_json(r)}, 0.0
        if sub == "publish":
            return {"published": True, "asset_id": node_input.get("asset_id")}, 0.0
        if sub == "enqueue":
            from app.workers.celery_app import celery_app
            celery_app.signature(node.config["task"], args=node.config.get("args", []),
                                 kwargs=node.config.get("kwargs", {})).apply_async()
            return {"enqueued": node.config["task"]}, 0.0
        return {}, 0.0

    return {}, 0.0


def _safe_json(r: httpx.Response) -> Any:
    try: return r.json()
    except Exception: return r.text[:1000]


async def _mark_step(run_id: UUID, node_id: str, *, status: str, output: dict | None = None,
                     error: str | None = None, ms: int = 0) -> None:
    async with SessionLocal() as db:
        row = (await db.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))).scalar_one()
        state = dict(row.state or {})
        steps = dict(state.get("steps", {}))
        steps[node_id] = {"status": status, "output": output, "error": error, "ms": ms}
        state["steps"] = steps
        row.state = state
        await db.commit()


async def _set_run_status(run_id: UUID, status: str) -> None:
    async with SessionLocal() as db:
        row = (await db.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))).scalar_one()
        row.status = status
        await db.commit()
