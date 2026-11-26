from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import User
from ..security import (
    Auth,
    create_session,
    destroy_session,
    get_auth,
    login_limiter,
    require_user,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str


def user_dict(user: User | None, auth: Auth | None = None) -> dict:
    if user is None:
        return {"user": None}
    return {"user": {"id": user.id, "username": user.username, "is_admin": user.is_admin,
                     "settings": user.settings or {}},
            "csrf_token": auth.session.csrf_token if auth and auth.session else None}


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    s = get_settings()
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0].strip()
    if not login_limiter.check(ip, s.login_rate_limit, s.login_rate_window_s):
        raise HTTPException(429, "Too many login attempts, try again later")
    user = db.scalar(select(User).where(User.username == body.username.strip()))
    if user is None or not verify_password(user.password_hash, body.password):
        raise HTTPException(401, "Invalid username or password")
    sess = create_session(db, user, response)
    return {"user": user_dict(user)["user"], "csrf_token": sess.csrf_token}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    destroy_session(db, request, response)
    return {"ok": True}


@router.get("/me")
def me(auth: Auth = Depends(get_auth)):
    return user_dict(auth.user, auth)


class SettingsIn(BaseModel):
    settings: dict


@router.patch("/settings")
def update_settings(body: SettingsIn, user: User = Depends(require_user), db: Session = Depends(get_db)):
    allowed = {"mute_default", "autoplay", "hide_login_banner"}
    merged = dict(user.settings or {})
    merged.update({k: v for k, v in body.settings.items() if k in allowed})
    u = db.get(User, user.id)
    u.settings = merged
    db.commit()
    return {"settings": merged}
