"""GET /v1/workflows/{workflow_id}/runs/{run_id} — detail endpoint coverage.

Mirrors the cross-tenant + shape contract from the list endpoint so a single
run cannot leak across accounts via direct addressing.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

os.environ.setdefault("SECRET_KEY", "testtesttesttesttesttesttesttest")

from app.api.deps.auth import CurrentUser, current_user  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models.account import Account  # noqa: E402
from app.models.workflow import Workflow, WorkflowRun  # noqa: E402

TAG = f"test_wfdetail_{uuid.uuid4().hex[:8]}"


def _user(suffix: str = "") -> CurrentUser:
    return CurrentUser(
        clerk_user_id=f"{TAG}{suffix}_u",
        clerk_org_id=f"{TAG}{suffix}_o",
        email=f"d{suffix}@test.local",
        role="owner", raw={},
    )


@pytest.fixture()
def auth_as():
    def _set(u: CurrentUser):
        app.dependency_overrides[current_user] = lambda: u
    yield _set
    app.dependency_overrides.pop(current_user, None)


@pytest.fixture()
async def workflow_with_run():
    async with SessionLocal() as db:
        acct = Account(clerk_org_id=f"{TAG}_o", name="WD", plan="free")
        db.add(acct); await db.flush()
        wf = Workflow(
            account_id=acct.id, name="Detail WF",
            definition={"nodes": [{"id": "n", "kind": "trigger.schedule"}], "edges": []},
            trigger={"kind": "schedule", "config": {}},
        )
        db.add(wf); await db.flush()

        now = datetime.now(timezone.utc)
        run = WorkflowRun(
            workflow_id=wf.id, status="completed",
            trigger={"source": "manual"},
            state={"steps": {"writer": {"status": "ok"}}},
            started_at=now,
            finished_at=now,
        )
        db.add(run); await db.commit()
        yield {"workflow_id": wf.id, "run_id": run.id}

    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM accounts WHERE clerk_org_id LIKE :p"),
                         {"p": f"{TAG}%"})
        await db.commit()


@pytest.mark.asyncio
async def test_get_run_returns_shape(auth_as, workflow_with_run):
    auth_as(_user())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get(
            f"/v1/workflows/{workflow_with_run['workflow_id']}/runs/{workflow_with_run['run_id']}"
        )
    assert r.status_code == 200
    body = r.json()
    for k in ("id", "workflow_id", "status", "state", "started_at"):
        assert k in body, f"missing {k}"
    assert body["status"] == "completed"
    assert body["state"]["steps"]["writer"]["status"] == "ok"
    assert body["trigger"] == {"source": "manual"}


@pytest.mark.asyncio
async def test_get_run_unknown_run_is_404(auth_as, workflow_with_run):
    auth_as(_user())
    fake_run = uuid.uuid4()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get(
            f"/v1/workflows/{workflow_with_run['workflow_id']}/runs/{fake_run}"
        )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_run_cross_tenant_returns_404(auth_as, workflow_with_run):
    """User B addressing User A's run id must NOT receive the row."""
    other = _user("_b")
    async with SessionLocal() as db:
        acct_b = Account(clerk_org_id=other.clerk_org_id, name="OB", plan="free")
        db.add(acct_b); await db.commit()

    auth_as(other)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get(
            f"/v1/workflows/{workflow_with_run['workflow_id']}/runs/{workflow_with_run['run_id']}"
        )
    assert r.status_code == 404, "cross-tenant access must not leak data"


@pytest.mark.asyncio
async def test_get_run_mismatched_workflow_id_is_404(auth_as, workflow_with_run):
    """Run id is real but addressed under a *different* workflow id — must 404."""
    user = _user("_mismatch")
    async with SessionLocal() as db:
        acct = Account(clerk_org_id=user.clerk_org_id, name="M", plan="free")
        db.add(acct); await db.flush()
        other_wf = Workflow(
            account_id=acct.id, name="Other",
            definition={"nodes": [{"id": "n", "kind": "trigger.schedule"}], "edges": []},
            trigger={"kind": "schedule", "config": {}},
        )
        db.add(other_wf); await db.commit()
        other_wf_id = other_wf.id

    auth_as(user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get(
            f"/v1/workflows/{other_wf_id}/runs/{workflow_with_run['run_id']}"
        )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_run_unauthenticated_is_401():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get(f"/v1/workflows/{uuid.uuid4()}/runs/{uuid.uuid4()}")
    assert r.status_code == 401
