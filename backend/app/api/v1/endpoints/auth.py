from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, current_user

router = APIRouter()


@router.get("/me")
async def me(user: CurrentUser = Depends(current_user)) -> dict:
    return {
        "clerk_user_id": user.clerk_user_id,
        "clerk_org_id": user.clerk_org_id,
        "email": user.email,
        "role": user.role,
    }
