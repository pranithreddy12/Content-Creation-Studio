from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.api.deps import CurrentUser, DBSession, current_user
from app.schemas.source import (
    SourceCreate,
    SourceOut,
    SourceUploadInit,
    SourceUploadInitOut,
)
from app.services import source_service
from app.workers.tasks.ingest_tasks import ingest_source_task

router = APIRouter()


@router.post("", response_model=SourceOut, status_code=status.HTTP_201_CREATED)
async def create_source(
    payload: SourceCreate,
    db: DBSession,
    user: Annotated[CurrentUser, Depends(current_user)],
):
    src = await source_service.create_source(db, user, payload)
    ingest_source_task.delay(str(src.id))
    return src


@router.post("/upload-intent", response_model=SourceUploadInitOut)
async def upload_intent(payload: SourceUploadInit, _: Annotated[CurrentUser, Depends(current_user)]):
    return source_service.make_upload_intent(payload)


@router.get("/brand/{brand_id}", response_model=list[SourceOut])
async def list_sources(
    brand_id: UUID,
    db: DBSession,
    user: Annotated[CurrentUser, Depends(current_user)],
    limit: int = 200,
):
    return await source_service.list_for_brand(db, user, brand_id, limit=limit)
