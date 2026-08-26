from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.api.deps import get_current_principal
from app.domains.identity import LoginResult, Principal
from app.runtime.container import get_identity, get_settings


router = APIRouter(prefix="/auth", tags=["auth"])


class LoginCommand(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)


@router.post("/login", response_model=LoginResult)
def login(command: LoginCommand, response: Response) -> LoginResult:
    try:
        result = get_identity().login(command.username, command.password)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_credentials") from exc
    settings = get_settings()
    response.set_cookie(
        key=settings.session_cookie_name,
        value=result.access_token,
        max_age=settings.session_expire_days * 24 * 60 * 60,
        httponly=True,
        secure=False,
        samesite="lax",
        path="/",
    )
    return result


@router.get("/me", response_model=Principal)
def me(principal: Principal = Depends(get_current_principal)) -> Principal:
    return principal


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> Response:
    settings = get_settings()
    token = request.cookies.get(settings.session_cookie_name)
    if authorization and authorization.casefold().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    if token:
        get_identity().logout(token)
    response.delete_cookie(settings.session_cookie_name, path="/", samesite="lax")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
