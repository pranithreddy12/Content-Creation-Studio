"""Account lifecycle — hard deletion with full data purge (GDPR Art. 17)."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import CurrentUser, DBSession, current_user
from app.services.account_deletion import create_deletion_job
from app.services.provisioning import get_or_create_account
from app.workers.tasks.account_tasks import purge_account

router = APIRouter()


@router.delete("/{account_id}", status_code=status.HTTP_202_ACCEPTED)
async def delete_account(
    account_id: UUID,
    db: DBSession,
    user: Annotated[CurrentUser, Depends(current_user)],
) -> dict:
    """Hard-delete the caller's account and purge all its data across every store.

    A caller may only delete their OWN account: addressing any other account_id
    returns 404 (no existence side channel — same shape as "doesn't exist").
    Kicks off an async, resumable purge job and returns 202 immediately.
    """
    acct = await get_or_create_account(db, user)
    if account_id != acct.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "account not found")

    job = await create_deletion_job(db, acct.id)
    purge_account.delay(str(job.id))
    return {"status": "accepted", "job_id": str(job.id), "account_id": str(acct.id)}
