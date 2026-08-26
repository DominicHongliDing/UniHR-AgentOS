from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, Header, HTTPException, Request, status

from app.domains.identity import Principal
from app.runtime.container import get_identity, get_settings


def _extract_token(authorization: str | None, cookie_token: str | None) -> str:
    if authorization and authorization.casefold().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        if token:
            return token
    if cookie_token:
        return cookie_token
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication_required")


def get_current_principal(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> Principal:
    cookie_token = request.cookies.get(get_settings().session_cookie_name)
    token = _extract_token(authorization, cookie_token)
    principal = get_identity().authenticate(token)
    if principal is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_or_expired_session")
    return principal


def require_permission(permission: str) -> Callable[..., Principal]:
    def guard(principal: Principal = Depends(get_current_principal)) -> Principal:
        if not principal.can(permission):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="permission_denied")
        return principal

    return guard


def session_cookie_name() -> str:
    return get_settings().session_cookie_name
