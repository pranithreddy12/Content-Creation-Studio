from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DBSession, current_user
from app.models.account import Account
from app.models.workflow import Workflow, WorkflowRun
from app.schemas.workflow import (
    WorkflowCreate,
    WorkflowOut,
    WorkflowRunOut,
    WorkflowRunRequest,
    WorkflowUpdate,
)
from app.services.provisioning import get_or_create_account
from app.services.workflow.schema import validate_workflow
from app.workers.tasks.workflow_tasks import run_workflow_task

router = APIRouter()


async def _account(db, user: CurrentUser) -> Account:
    return await get_or_create_account(db, user)


@router.post("", response_model=WorkflowOut, status_code=status.HTTP_201_CREATED)
async def create_workflow(
    payload: WorkflowCreate,
    db: DBSession,
    user: Annotated[CurrentUser, Depends(current_user)],
):
    try:
        validate_workflow(payload.definition)
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"invalid workflow: {exc}")
    acct = await _account(db, user)
    wf = Workflow(
        account_id=acct.id,
        brand_id=payload.brand_id,
        name=payload.name,
        definition=payload.definition,
        trigger=payload.trigger,
        status=payload.status,
    )
    db.add(wf)
    await db.commit()
    await db.refresh(wf)
    return wf


@router.get("", response_model=list[WorkflowOut])
async def list_workflows(db: DBSession, user: Annotated[CurrentUser, Depends(current_user)]):
    acct = await _account(db, user)
    rows = (await db.execute(
        select(Workflow).where(Workflow.account_id == acct.id).order_by(Workflow.created_at.desc())
    )).scalars().all()
    return list(rows)


@router.patch("/{workflow_id}", response_model=WorkflowOut)
async def update_workflow(
    workflow_id: UUID,
    payload: WorkflowUpdate,
    db: DBSession,
    user: Annotated[CurrentUser, Depends(current_user)],
):
    acct = await _account(db, user)
    wf = (await db.execute(
        select(Workflow).where(Workflow.id == workflow_id, Workflow.account_id == acct.id)
    )).scalar_one_or_none()
    if not wf:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "workflow not found")
    if payload.definition is not None:
        validate_workflow(payload.definition)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(wf, k, v)
    await db.commit()
    await db.refresh(wf)
    return wf


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(
    workflow_id: UUID,
    db: DBSession,
    user: Annotated[CurrentUser, Depends(current_user)],
):
    acct = await _account(db, user)
    wf = (await db.execute(
        select(Workflow).where(Workflow.id == workflow_id, Workflow.account_id == acct.id)
    )).scalar_one_or_none()
    if wf:
        await db.delete(wf)
        await db.commit()


@router.post("/run")
async def run_workflow_ep(
    payload: WorkflowRunRequest,
    db: DBSession,
    user: Annotated[CurrentUser, Depends(current_user)],
):
    if payload.workflow_id:
        # validate it belongs to the account before queuing
        acct = await _account(db, user)
        wf = (await db.execute(
            select(Workflow).where(Workflow.id == payload.workflow_id, Workflow.account_id == acct.id)
        )).scalar_one_or_none()
        if not wf:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "workflow not found")
        task = run_workflow_task.delay(str(wf.id), payload.payload)
        return {"task_id": task.id, "workflow_id": str(wf.id)}
    # ad-hoc validation (no persistence)
    if payload.definition is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "workflow_id or definition required")
    try:
        validate_workflow(payload.definition)
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"invalid workflow: {exc}")
    return {"validated": True}


@router.get("/{workflow_id}/runs", response_model=list[WorkflowRunOut])
async def list_runs(
    workflow_id: UUID,
    db: DBSession,
    _: Annotated[CurrentUser, Depends(current_user)],
):
    rows = (await db.execute(
        select(WorkflowRun).where(WorkflowRun.workflow_id == workflow_id)
        .order_by(WorkflowRun.started_at.desc()).limit(50)
    )).scalars().all()
    return list(rows)
