"""Session auth helpers: login/logout routes and FastAPI dependencies."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .store import store

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


def current_user(request: Request) -> str:
    """Dependency: return the logged-in username or 401."""
    user = request.session.get("user")
    if not user or store.role(user) is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return user


def require_admin(user: str = Depends(current_user)) -> str:
    if not store.is_admin(user):
        raise HTTPException(status_code=403, detail="admin only")
    return user


@router.post("/api/login")
def login(req: LoginRequest, request: Request) -> JSONResponse:
    role = store.verify(req.username, req.password)
    if role is None:
        raise HTTPException(status_code=401, detail="invalid credentials")
    request.session["user"] = req.username
    return JSONResponse({"username": req.username, "role": role})


@router.post("/api/logout")
def logout(request: Request) -> JSONResponse:
    request.session.clear()
    return JSONResponse({"ok": True})


@router.get("/api/me")
def me(request: Request) -> JSONResponse:
    user = request.session.get("user")
    if not user or store.role(user) is None:
        raise HTTPException(status_code=401, detail="not logged in")
    return JSONResponse({"username": user, "role": store.role(user)})
