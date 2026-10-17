from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from nvr.http.security import SESSION_COOKIE, CurrentUser, client_address
from nvr.identity.domain import SESSION_TTL, User

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class UserOut(BaseModel):
    id: str
    username: str
    role: Literal["admin", "viewer"]

    @classmethod
    def from_domain(cls, user: User) -> UserOut:
        return cls(id=user.id, username=user.username, role=user.role.value)


@router.post("/login", response_model=UserOut)
async def login(body: LoginIn, request: Request, response: Response) -> UserOut:
    result = await request.app.state.use_cases.login(body.username, body.password, client_address(request))
    response.set_cookie(
        SESSION_COOKIE,
        result.token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        secure=request.app.state.cookie_secure,
        samesite="strict",
        path="/",
    )
    return UserOut.from_domain(result.user)


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response) -> None:
    await request.app.state.use_cases.logout(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(
        SESSION_COOKIE, path="/", secure=request.app.state.cookie_secure, httponly=True, samesite="strict"
    )


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return UserOut.from_domain(user)
