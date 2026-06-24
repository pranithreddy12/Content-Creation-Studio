"""GET /v1/workflows/{workflow_id}/runs — list endpoint coverage."""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

os.environ.setdefault("SECRET_KEY", "testtesttesttesttesttesttesttest")

from app.api.deps.auth import CurrentUser, current_user  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models.account import Account  # noqa: E402
from app.models.workflow import Workflow, WorkflowRun  # noqa: E402

TAG = f"test_wfruns_{uuid.uuid4().hex[:8]}"


def _user(suffix: str = "") -> CurrentUser:
    return CurrentUser(
        clerk_user_id=f"{TAG}{suffix}_u",
        clerk_org_id=f"{TAG}{suffix}_o",
        email=f"r{suffix}@test.local",
        role="owner", raw={},
    )


@pytest.fixture()
def auth_as():
    def _set(u: CurrentUser):
        app.dependency_overrides[current_user] = lambda: u
    yield _set
    app.dependency_overrides.pop(current_user, None)


@pytest.fixture()
async def workflow_with_runs():
    """Create one workflow + 5 historical runs at descending start times."""
    user = _user()
    async with SessionLocal() as db:
        acct = Account(clerk_org_id=f"{TAG}_o", name="W", plan="free")
        db.add(acct); await db.flush()
        wf = Workflow(
            account_id=acct.id, name="Has Runs",
            definition={"nodes": [{"id": "n", "kind": "trigger.schedule"}], "edges": []},
            trigger={"kind": "schedule", "config": {}},
        )
        db.add(wf); await db.flush()

        now = datetime.now(timezone.utc)
        for i in range(5):
            db.add(WorkflowRun(
                workflow_id=wf.id, status="completed",
                trigger={"i": i}, state={"steps": {}},
                started_at=now - timedelta(minutes=i),  # newer first
                finished_at=now - timedelta(minutes=i) + timedelta(seconds=12),
            ))
        await db.commit()
        wfid = wf.id

    yield {"workflow_id": wfid}

    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM accounts WHERE clerk_org_id LIKE :p"),
                         {"p": f"{TAG}%"})
        await db.commit()


@pytest.mark.asyncio
async def test_list_runs_returns_newest_first(auth_as, workflow_with_runs):
    auth_as(_user())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get(f"/v1/workflows/{workflow_with_runs['workflow_id']}/runs")
    assert r.status_code == 200
    runs = r.json()
    assert len(runs) == 5
    starts = [run["started_at"] for run in runs]
    assert starts == sorted(starts, reverse=True), "runs must be newest-first"


@pytest.mark.asyncio
async def test_list_runs_for_workflow_with_no_runs_is_empty(auth_as):
    """Workflow exists but has no run history → []."""
    user = _user("_empty")
    auth_as(user)
    async with SessionLocal() as db:
        acct = Account(clerk_org_id=f"{TAG}_empty_o", name="W", plan="free")
        db.add(acct); await db.flush()
        wf = Workflow(
            account_id=acct.id, name="No Runs",
            definition={"nodes": [{"id": "n", "kind": "trigger.schedule"}], "edges": []},
            trigger={"kind": "schedule", "config": {}},
        )
        db.add(wf); await db.commit()
        wfid = wf.id

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
            r = await cx.get(f"/v1/workflows/{wfid}/runs")
        assert r.status_code == 200
        assert r.json() == []
    finally:
        async with SessionLocal() as db:
            await db.execute(text("DELETE FROM accounts WHERE clerk_org_id = :o"),
                             {"o": f"{TAG}_empty_o"})
            await db.commit()


@pytest.mark.asyncio
async def test_list_runs_limited_to_50(auth_as):
    """If more than 50 runs exist, the endpoint returns at most 50 (most recent)."""
    user = _user("_many")
    auth_as(user)
    async with SessionLocal() as db:
        acct = Account(clerk_org_id=f"{TAG}_many_o", name="M", plan="free")
        db.add(acct); await db.flush()
        wf = Workflow(
            account_id=acct.id, name="Many Runs",
            definition={"nodes": [{"id": "n", "kind": "trigger.schedule"}], "edges": []},
            trigger={"kind": "schedule", "config": {}},
        )
        db.add(wf); await db.flush()
        now = datetime.now(timezone.utc)
        # 60 runs
        for i in range(60):
            db.add(WorkflowRun(
                workflow_id=wf.id, status="completed", state={"steps": {}},
                trigger={"i": i},
                started_at=now - timedelta(minutes=i),
            ))
        await db.commit()
        wfid = wf.id

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
            r = await cx.get(f"/v1/workflows/{wfid}/runs")
        assert r.status_code == 200
        runs = r.json()
        assert len(runs) == 50
    finally:
        async with SessionLocal() as db:
            await db.execute(text("DELETE FROM accounts WHERE clerk_org_id = :o"),
                             {"o": f"{TAG}_many_o"})
            await db.commit()


@pytest.mark.asyncio
async def test_list_runs_shape(auth_as, workflow_with_runs):
    auth_as(_user())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get(f"/v1/workflows/{workflow_with_runs['workflow_id']}/runs")
    runs = r.json()
    row = runs[0]
    # Required fields per WorkflowRunOut
    for k in ("id", "workflow_id", "status", "state", "started_at"):
        assert k in row, f"missing {k}"
    assert row["state"] == {"steps": {}}


@pytest.mark.asyncio
async def test_list_runs_unauthenticated_returns_401():
    """No auth dep override = the real `current_user` dep runs → 401."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get(f"/v1/workflows/{uuid.uuid4()}/runs")
    assert r.status_code == 401
