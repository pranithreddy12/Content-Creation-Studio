from dataclasses import dataclass
from typing import Annotated, Optional
from uuid import UUID

import jwt
from fastapi import Depends, Header, HTTPException, Path, status

from app.core.security import verify_clerk_jwt


@dataclass
class CurrentUser:
    clerk_user_id: str
    clerk_org_id: Optional[str]
    email: Optional[str]
    role: Optional[str]
    raw: dict


async def current_user(
    authorization: Annotated[str | None, Header()] = None,
) -> CurrentUser:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    token = authorization.split(" ", 1)[1]
    try:
        claims = verify_clerk_jwt(token)
    except jwt.InvalidTokenError as exc:  # pragma: no cover
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid token: {exc}") from exc
    return CurrentUser(
        clerk_user_id=claims.get("sub", ""),
        clerk_org_id=claims.get("org_id"),
        email=claims.get("email"),
        role=claims.get("org_role"),
        raw=claims,
    )


def require_account(allowed_roles: tuple[str, ...] = ("owner", "admin", "editor", "viewer")):
    async def _dep(user: Annotated[CurrentUser, Depends(current_user)]) -> CurrentUser:
        if user.role and user.role not in allowed_roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "insufficient role")
        return user

    return _dep


async def require_brand_access(
    brand_id: Annotated[UUID, Path()],
    user: Annotated[CurrentUser, Depends(current_user)],
) -> UUID:
    # Authorization is enforced at the service layer (joining brand → account).
    # This dep makes the brand_id available + ensures the user is authenticated.
    return brand_id
