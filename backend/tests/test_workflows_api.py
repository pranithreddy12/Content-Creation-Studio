"""End-to-end CRUD test for `/v1/workflows`.

Hits the router with the auth dep overridden, asserts:
  * POST creates and returns the shape we promised
  * GET lists only the caller's workflows
  * PATCH partially updates definition / status
  * DELETE removes the row
  * POST /run with workflow_id enqueues a Celery task (queue depth grows)
  * POST /run with ad-hoc definition validates without persisting
  * Invalid definition (cycle, unknown node) is rejected with 400
"""
from __future__ import annotations

import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

os.environ.setdefault("SECRET_KEY", "testtesttesttesttesttesttesttest")

from app.api.deps.auth import CurrentUser, current_user  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models.workflow import Workflow  # noqa: E402

TAG = f"test_wfapi_{uuid.uuid4().hex[:8]}"


def _user() -> CurrentUser:
    return CurrentUser(
        clerk_user_id=f"{TAG}_u",
        clerk_org_id=f"{TAG}_o",
        email="wf@test.local",
        role="owner",
        raw={},
    )


@pytest.fixture()
def auth_as_user():
    u = _user()
    app.dependency_overrides[current_user] = lambda: u
    yield u
    app.dependency_overrides.pop(current_user, None)


@pytest.fixture()
async def cleanup():
    yield
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM accounts WHERE clerk_org_id = :o"), {"o": f"{TAG}_o"})
        await db.commit()


VALID_DEF = {
    "nodes": [
        {"id": "t",  "kind": "trigger.schedule"},
        {"id": "w",  "kind": "agent.writer"},
    ],
    "edges": [{"source": "t", "target": "w"}],
}

CYCLE_DEF = {
    "nodes": [
        {"id": "a", "kind": "agent.research"},
        {"id": "b", "kind": "agent.writer"},
    ],
    "edges": [
        {"source": "a", "target": "b"},
        {"source": "b", "target": "a"},
    ],
}


@pytest.mark.asyncio
async def test_create_workflow_returns_201_with_shape(auth_as_user, cleanup):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.post("/v1/workflows", json={
            "name": "Test WF",
            "definition": VALID_DEF,
            "trigger": {"kind": "schedule", "config": {"cron": "0 9 * * *"}},
        })
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Test WF"
    assert body["status"] == "active"
    assert body["definition"]["nodes"]
    assert body["definition"]["edges"]
    assert "id" in body
    assert "account_id" in body
    assert "created_at" in body
    assert "updated_at" in body


@pytest.mark.asyncio
async def test_create_rejects_invalid_definition_cycle(auth_as_user, cleanup):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.post("/v1/workflows", json={
            "name": "Cyclic", "definition": CYCLE_DEF,
            "trigger": {"kind": "schedule", "config": {}},
        })
    assert r.status_code == 400
    assert "cycle" in r.json()["detail"]


@pytest.mark.asyncio
async def test_create_rejects_unknown_node_kind(auth_as_user, cleanup):
    bad = {"nodes": [{"id": "x", "kind": "agent.totally_made_up"}], "edges": []}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.post("/v1/workflows", json={
            "name": "BadKind", "definition": bad,
            "trigger": {"kind": "schedule", "config": {}},
        })
    assert r.status_code == 400
    assert "unknown node kind" in r.json()["detail"]


@pytest.mark.asyncio
async def test_list_returns_only_callers_workflows(auth_as_user, cleanup):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r1 = await cx.post("/v1/workflows", json={
            "name": "Mine 1", "definition": VALID_DEF,
            "trigger": {"kind": "schedule", "config": {}},
        })
        r2 = await cx.post("/v1/workflows", json={
            "name": "Mine 2", "definition": VALID_DEF,
            "trigger": {"kind": "schedule", "config": {}},
        })
        assert r1.status_code == 201
        assert r2.status_code == 201

        r = await cx.get("/v1/workflows")
    assert r.status_code == 200
    names = {w["name"] for w in r.json()}
    assert {"Mine 1", "Mine 2"} <= names


@pytest.mark.asyncio
async def test_patch_partially_updates(auth_as_user, cleanup):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        created = (await cx.post("/v1/workflows", json={
            "name": "Original", "definition": VALID_DEF,
            "trigger": {"kind": "schedule", "config": {}},
        })).json()
        wfid = created["id"]

        r = await cx.patch(f"/v1/workflows/{wfid}", json={"name": "Renamed", "status": "paused"})
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Renamed"
    assert body["status"] == "paused"
    # Definition untouched
    assert body["definition"]["nodes"][0]["id"] == "t"


@pytest.mark.asyncio
async def test_patch_invalid_definition_returns_400(auth_as_user, cleanup):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        created = (await cx.post("/v1/workflows", json={
            "name": "Will be broken", "definition": VALID_DEF,
            "trigger": {"kind": "schedule", "config": {}},
        })).json()
        wfid = created["id"]

        r = await cx.patch(f"/v1/workflows/{wfid}", json={"definition": CYCLE_DEF})
    # PATCH must catch the validator's ValueError and return a clean 400.
    assert r.status_code == 400, f"got {r.status_code}, body={r.text}"
    assert "cycle" in r.json()["detail"]


@pytest.mark.asyncio
async def test_delete_removes_workflow(auth_as_user, cleanup):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        created = (await cx.post("/v1/workflows", json={
            "name": "Doomed", "definition": VALID_DEF,
            "trigger": {"kind": "schedule", "config": {}},
        })).json()
        wfid = created["id"]

        r = await cx.delete(f"/v1/workflows/{wfid}")
        assert r.status_code == 204

        # subsequent GET no longer lists it
        list_resp = await cx.get("/v1/workflows")
    assert all(w["id"] != wfid for w in list_resp.json())


@pytest.mark.asyncio
async def test_delete_foreign_workflow_silently_ignored(auth_as_user, cleanup):
    """Deleting a workflow that belongs to another account silently no-ops (returns 204)."""
    # Manually create a workflow under a different account_id.
    foreign_wf_id = uuid.uuid4()
    async with SessionLocal() as db:
        # Provision a foreign account
        from app.models.account import Account
        foreign_acct = Account(clerk_org_id=f"{TAG}_foreign", name="Other", plan="free")
        db.add(foreign_acct)
        await db.flush()
        wf = Workflow(
            id=foreign_wf_id,
            account_id=foreign_acct.id, name="ForeignWF",
            definition=VALID_DEF, trigger={"kind": "schedule", "config": {}},
        )
        db.add(wf)
        await db.commit()

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
            r = await cx.delete(f"/v1/workflows/{foreign_wf_id}")
        assert r.status_code == 204

        # The row must still exist.
        async with SessionLocal() as db:
            still = (await db.execute(select(Workflow).where(Workflow.id == foreign_wf_id))).scalar_one_or_none()
            assert still is not None, "tenant leak: foreign workflow was deleted"
    finally:
        async with SessionLocal() as db:
            await db.execute(text("DELETE FROM accounts WHERE clerk_org_id = :o"),
                             {"o": f"{TAG}_foreign"})
            await db.commit()


@pytest.mark.asyncio
async def test_run_ad_hoc_definition_validates_only(auth_as_user, cleanup):
    """POST /run with definition but no workflow_id should validate and NOT persist."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.post("/v1/workflows/run", json={"definition": VALID_DEF})
    assert r.status_code == 200
    assert r.json() == {"validated": True}


@pytest.mark.asyncio
async def test_run_with_invalid_definition_returns_400(auth_as_user, cleanup):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.post("/v1/workflows/run", json={"definition": CYCLE_DEF})
    assert r.status_code == 400
    assert "cycle" in r.json()["detail"]


@pytest.mark.asyncio
async def test_run_without_body_returns_400(auth_as_user, cleanup):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.post("/v1/workflows/run", json={})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_run_with_workflow_id_dispatches_task(monkeypatch, auth_as_user, cleanup):
    """POST /run with a workflow_id should enqueue run_workflow_task."""
    captured = {}

    class FakeAsync:
        def __init__(self, tid="fake-task-id"): self.id = tid

    def fake_delay(workflow_id, payload=None):
        captured["workflow_id"] = workflow_id
        captured["payload"] = payload
        return FakeAsync()

    from app.workers.tasks import workflow_tasks as wt
    monkeypatch.setattr(wt.run_workflow_task, "delay", fake_delay)
    # also patch the import in the endpoint module
    from app.api.v1.endpoints import workflows as wf_ep
    monkeypatch.setattr(wf_ep.run_workflow_task, "delay", fake_delay)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        created = (await cx.post("/v1/workflows", json={
            "name": "Runnable", "definition": VALID_DEF,
            "trigger": {"kind": "schedule", "config": {}},
        })).json()
        wfid = created["id"]

        r = await cx.post("/v1/workflows/run", json={"workflow_id": wfid, "payload": {"x": 1}})

    assert r.status_code == 200
    body = r.json()
    assert body["workflow_id"] == wfid
    assert body["task_id"] == "fake-task-id"
    assert captured["workflow_id"] == wfid
    assert captured["payload"] == {"x": 1}


@pytest.mark.asyncio
async def test_run_with_foreign_workflow_id_returns_404(auth_as_user, cleanup):
    """POST /run with a workflow_id from another tenant must NOT dispatch."""
    foreign_wf_id = uuid.uuid4()
    async with SessionLocal() as db:
        from app.models.account import Account
        foreign_acct = Account(clerk_org_id=f"{TAG}_foreign2", name="Other2", plan="free")
        db.add(foreign_acct)
        await db.flush()
        db.add(Workflow(
            id=foreign_wf_id,
            account_id=foreign_acct.id, name="Sneaky",
            definition=VALID_DEF, trigger={"kind": "schedule", "config": {}},
        ))
        await db.commit()

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
            r = await cx.post("/v1/workflows/run", json={"workflow_id": str(foreign_wf_id)})
        assert r.status_code == 404
    finally:
        async with SessionLocal() as db:
            await db.execute(text("DELETE FROM accounts WHERE clerk_org_id = :o"),
                             {"o": f"{TAG}_foreign2"})
            await db.commit()
