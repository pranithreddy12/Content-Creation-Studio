from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import CurrentUser, DBSession, current_user
from app.schemas.brand import BrandCreate, BrandOut, BrandUpdate
from app.services import brand_service

router = APIRouter()


@router.get("", response_model=list[BrandOut])
async def list_brands(db: DBSession, user: Annotated[CurrentUser, Depends(current_user)]):
    return await brand_service.list_for_user(db, user)


@router.post("", response_model=BrandOut, status_code=status.HTTP_201_CREATED)
async def create_brand(
    payload: BrandCreate,
    db: DBSession,
    user: Annotated[CurrentUser, Depends(current_user)],
):
    return await brand_service.create(db, user, payload)


@router.get("/{brand_id}", response_model=BrandOut)
async def get_brand(brand_id: UUID, db: DBSession, user: Annotated[CurrentUser, Depends(current_user)]):
    brand = await brand_service.get(db, user, brand_id)
    if not brand:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "brand not found")
    return brand


@router.patch("/{brand_id}", response_model=BrandOut)
async def update_brand(
    brand_id: UUID,
    payload: BrandUpdate,
    db: DBSession,
    user: Annotated[CurrentUser, Depends(current_user)],
):
    return await brand_service.update(db, user, brand_id, payload)


@router.delete("/{brand_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_brand(
    brand_id: UUID, db: DBSession, user: Annotated[CurrentUser, Depends(current_user)]
):
    await brand_service.soft_delete(db, user, brand_id)
