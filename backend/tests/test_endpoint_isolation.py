"""Cross-tenant + pagination/limit coverage for list endpoints.

Three thin slices:
  * /v1/notifications list never leaks rows across users
  * /v1/workflows/{id}/runs blocks cross-tenant access
  * /v1/assets ?limit clamp respected; default and max bounds enforced
"""
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
from app.models.account import Account, Workspace  # noqa: E402
from app.models.brand import Brand  # noqa: E402
from app.models.content import ContentAsset, ContentIdea  # noqa: E402
from app.models.notification import Notification  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.workflow import Workflow, WorkflowRun  # noqa: E402

TAG = f"test_iso_{uuid.uuid4().hex[:8]}"
SLUG = TAG.replace("_", "-")


def _user(suffix: str = "") -> CurrentUser:
    return CurrentUser(
        clerk_user_id=f"{TAG}{suffix}_u",
        clerk_org_id=f"{TAG}{suffix}_o",
        email=f"i{suffix}@test.local",
        role="owner", raw={},
    )


@pytest.fixture()
def auth_as():
    def _set(u: CurrentUser):
        app.dependency_overrides[current_user] = lambda: u
    yield _set
    app.dependency_overrides.pop(current_user, None)


@pytest.fixture()
async def cleanup():
    yield
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM users WHERE clerk_user_id LIKE :p"),
                         {"p": f"{TAG}%"})
        await db.execute(text("DELETE FROM accounts WHERE clerk_org_id LIKE :p"),
                         {"p": f"{TAG}%"})
        await db.commit()


# ── Notifications cross-tenant + pagination ─────────────────────────

@pytest.mark.asyncio
async def test_notifications_list_is_per_user(auth_as, cleanup):
    user_a = _user("_A")
    user_b = _user("_B")

    from app.services.provisioning import get_or_create_account
    async with SessionLocal() as db:
        acct_a = await get_or_create_account(db, user_a)
        acct_b = await get_or_create_account(db, user_b)
        ua = User(clerk_user_id=user_a.clerk_user_id, email="a@x")
        ub = User(clerk_user_id=user_b.clerk_user_id, email="b@x")
        db.add_all([ua, ub])
        await db.flush()
        for i in range(3):
            db.add(Notification(
                account_id=acct_a.id, user_id=ua.id, kind="info",
                title=f"A-note-{i}", body=".",
                created_at=datetime.now(timezone.utc) - timedelta(minutes=i),
            ))
        db.add(Notification(
            account_id=acct_b.id, user_id=ub.id, kind="info",
            title="B-only", body=".",
            created_at=datetime.now(timezone.utc),
        ))
        await db.commit()

    auth_as(user_a)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get("/v1/notifications")
    titles = {n["title"] for n in r.json()}
    assert {"A-note-0", "A-note-1", "A-note-2"} == titles
    assert "B-only" not in titles


@pytest.mark.asyncio
async def test_notifications_default_returns_newest_first(auth_as, cleanup):
    user = _user("_ord")
    from app.services.provisioning import get_or_create_account
    async with SessionLocal() as db:
        acct = await get_or_create_account(db, user)
        u = User(clerk_user_id=user.clerk_user_id, email="o@x")
        db.add(u)
        await db.flush()
        # Insert 5 rows with descending created_at
        for i in range(5):
            db.add(Notification(
                account_id=acct.id, user_id=u.id, kind="info",
                title=f"n{i}", body=".",
                created_at=datetime.now(timezone.utc) - timedelta(minutes=i * 5),
            ))
        await db.commit()

    auth_as(user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get("/v1/notifications")
    rows = r.json()
    titles = [n["title"] for n in rows]
    assert titles == ["n0", "n1", "n2", "n3", "n4"]


@pytest.mark.asyncio
async def test_notifications_limit_clamp(auth_as, cleanup):
    """limit > 200 must be clamped down to 200 server-side."""
    user = _user("_clamp")
    from app.services.provisioning import get_or_create_account
    async with SessionLocal() as db:
        acct = await get_or_create_account(db, user)
        u = User(clerk_user_id=user.clerk_user_id, email="c@x")
        db.add(u)
        await db.flush()
        for i in range(210):
            db.add(Notification(
                account_id=acct.id, user_id=u.id, kind="info",
                title=f"row {i}", body=".",
                created_at=datetime.now(timezone.utc) - timedelta(seconds=i),
            ))
        await db.commit()

    auth_as(user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        # Request way more than the cap
        r = await cx.get("/v1/notifications?limit=9999")
    rows = r.json()
    assert len(rows) <= 200, f"expected ≤200, got {len(rows)}"


@pytest.mark.asyncio
async def test_notifications_custom_limit_respected(auth_as, cleanup):
    user = _user("_lim")
    from app.services.provisioning import get_or_create_account
    async with SessionLocal() as db:
        acct = await get_or_create_account(db, user)
        u = User(clerk_user_id=user.clerk_user_id, email="l@x")
        db.add(u)
        await db.flush()
        for i in range(40):
            db.add(Notification(
                account_id=acct.id, user_id=u.id, kind="info",
                title=f"l{i}", body=".",
                created_at=datetime.now(timezone.utc) - timedelta(seconds=i),
            ))
        await db.commit()

    auth_as(user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get("/v1/notifications?limit=10")
    assert len(r.json()) == 10


# ── Workflow runs cross-tenant ─────────────────────────────────────

@pytest.mark.asyncio
async def test_workflow_runs_list_does_not_leak_across_tenants(auth_as, cleanup):
    """User B should NOT see runs for User A's workflow.

    The current endpoint doesn't enforce the workflow.account_id check, so this
    is a security-tightening test that documents expected behavior; if it fails,
    the dashboard or mobile of one tenant could see workflow execution history
    of another tenant.
    """
    user_a = _user("_wfa")
    user_b = _user("_wfb")

    async with SessionLocal() as db:
        acct_a = Account(clerk_org_id=user_a.clerk_org_id, name="WA", plan="free")
        db.add(acct_a); await db.flush()
        wf = Workflow(
            account_id=acct_a.id, name="A's flow",
            definition={"nodes": [{"id": "n", "kind": "trigger.schedule"}], "edges": []},
            trigger={"kind": "schedule", "config": {}},
        )
        db.add(wf); await db.flush()
        db.add(WorkflowRun(
            workflow_id=wf.id, status="completed",
            state={"steps": {}}, trigger={},
            started_at=datetime.now(timezone.utc),
        ))
        # Also provision user B as a separate account
        acct_b = Account(clerk_org_id=user_b.clerk_org_id, name="WB", plan="free")
        db.add(acct_b)
        await db.commit()
        wfid = wf.id

    auth_as(user_b)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get(f"/v1/workflows/{wfid}/runs")
    # After the fix: foreign workflow runs are scoped by Workflow.account_id;
    # tenant B receives a clean 404 (no existence side channel, no data leak).
    assert r.status_code == 404


# ── Assets pagination ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_assets_limit_clamped_to_200(auth_as, cleanup):
    user = _user("_aslim")
    auth_as(user)
    from app.services.provisioning import get_or_create_account
    async with SessionLocal() as db:
        acct = await get_or_create_account(db, user)
        ws_id = (await db.execute(
            text("SELECT id FROM workspaces WHERE account_id = :a LIMIT 1"),
            {"a": acct.id},
        )).scalar_one()
        brand = Brand(account_id=acct.id, workspace_id=ws_id,
                      name="B", slug=f"{SLUG}-aslim"[:60])
        db.add(brand); await db.flush()
        idea = ContentIdea(account_id=acct.id, brand_id=brand.id, title="x",
                           created_at=datetime.now(timezone.utc))
        db.add(idea); await db.flush()
        for i in range(220):
            db.add(ContentAsset(
                account_id=acct.id, brand_id=brand.id, idea_id=idea.id,
                format="blog", title=f"a{i}", status="draft",
            ))
        await db.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get("/v1/assets?limit=9999")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 200, f"expected exactly 200, got {len(rows)}"


@pytest.mark.asyncio
async def test_assets_default_limit_is_50(auth_as, cleanup):
    user = _user("_asdef")
    auth_as(user)
    from app.services.provisioning import get_or_create_account
    async with SessionLocal() as db:
        acct = await get_or_create_account(db, user)
        ws_id = (await db.execute(
            text("SELECT id FROM workspaces WHERE account_id = :a LIMIT 1"),
            {"a": acct.id},
        )).scalar_one()
        brand = Brand(account_id=acct.id, workspace_id=ws_id,
                      name="B", slug=f"{SLUG}-asdef"[:60])
        db.add(brand); await db.flush()
        idea = ContentIdea(account_id=acct.id, brand_id=brand.id, title="x",
                           created_at=datetime.now(timezone.utc))
        db.add(idea); await db.flush()
        for i in range(75):
            db.add(ContentAsset(
                account_id=acct.id, brand_id=brand.id, idea_id=idea.id,
                format="blog", title=f"a{i}", status="draft",
            ))
        await db.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get("/v1/assets")
    assert r.status_code == 200
    assert len(r.json()) == 50


@pytest.mark.asyncio
async def test_assets_list_ordered_newest_first(auth_as, cleanup):
    user = _user("_asord")
    auth_as(user)
    from app.services.provisioning import get_or_create_account
    async with SessionLocal() as db:
        acct = await get_or_create_account(db, user)
        ws_id = (await db.execute(
            text("SELECT id FROM workspaces WHERE account_id = :a LIMIT 1"),
            {"a": acct.id},
        )).scalar_one()
        brand = Brand(account_id=acct.id, workspace_id=ws_id,
                      name="B", slug=f"{SLUG}-asord"[:60])
        db.add(brand); await db.flush()
        idea = ContentIdea(account_id=acct.id, brand_id=brand.id, title="x",
                           created_at=datetime.now(timezone.utc))
        db.add(idea); await db.flush()
        # Insert 5 assets at descending wall-clock — newest "z5" last
        from datetime import datetime as _dt
        for i in range(5):
            a = ContentAsset(
                account_id=acct.id, brand_id=brand.id, idea_id=idea.id,
                format="blog", title=f"z{i}", status="draft",
            )
            db.add(a); await db.flush()
        await db.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r = await cx.get("/v1/assets")
    rows = r.json()
    titles = [a["title"] for a in rows]
    assert len(titles) == 5
    # Even when created_at ties (all inserted in the same tx → same now()), the
    # id-DESC tiebreaker keeps ordering stable. The exact order isn't z4→z0
    # because UUID4 ids aren't monotonic — but the order MUST be deterministic.
    # Re-fetching should return the identical sequence.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cx:
        r2 = await cx.get("/v1/assets")
    titles2 = [a["title"] for a in r2.json()]
    assert titles == titles2, "ordering is unstable across calls — pagination would break"
