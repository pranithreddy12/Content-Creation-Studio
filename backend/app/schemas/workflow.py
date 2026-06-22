from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class WorkflowCreate(BaseModel):
    brand_id: Optional[UUID] = None
    name: str
    definition: dict
    trigger: dict = {"kind": "schedule", "config": {"cron": "0 9 * * *"}}
    status: str = "active"


class WorkflowUpdate(BaseModel):
    name: Optional[str] = None
    definition: Optional[dict] = None
    trigger: Optional[dict] = None
    status: Optional[str] = None


class WorkflowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    account_id: UUID
    brand_id: Optional[UUID]
    name: str
    definition: dict
    trigger: dict
    status: str
    created_at: datetime
    updated_at: datetime


class WorkflowRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    workflow_id: UUID
    status: str
    trigger: Optional[dict]
    state: dict
    started_at: datetime
    finished_at: Optional[datetime]
    error: Optional[str]


class WorkflowRunRequest(BaseModel):
    definition: Optional[dict] = None
    workflow_id: Optional[UUID] = None
    payload: dict = {}
