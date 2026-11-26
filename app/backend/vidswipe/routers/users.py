"""Admin: local users and source access (who can see which sources)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import AppSetting, Rating, Source, User, UserSession, UserSource
from ..security import hash_password, require_admin
from ..services import visibility
from ..services.users import create_user, validate_password, validate_username

router = APIRouter(prefix="/api/admin", tags=["admin-users"], dependencies=[Depends(require_admin)])


def _source_ids(db: Session, ids: list[int] | None) -> list[int]:
    ids = sorted(set(ids or []))
    if not ids:
        return []
    known = set(db.scalars(select(Source.id).where(Source.id.in_(ids))))
    unknown = [i for i in ids if i not in known]
    if unknown:
        raise HTTPException(400, f"Unknown source id(s): {', '.join(map(str, unknown))}")
    return ids


def _user_dict(db: Session, u: User, me: User | None = None) -> dict:
    sources = sorted(db.scalars(select(UserSource.source_id).where(UserSource.user_id == u.id)))
    return {
        "id": u.id, "username": u.username, "is_admin": u.is_admin, "is_active": u.is_active,
        "all_sources": u.all_sources, "source_ids": sources,
        "created_at": u.created_at.isoformat() + "Z" if u.created_at else None,
        "ratings": db.scalar(select(func.count()).select_from(Rating).where(Rating.user_id == u.id)),
        "is_me": bool(me and me.id == u.id),
    }


def _active_admins(db: Session, exclude: int | None = None) -> int:
    q = select(func.count()).select_from(User).where(User.is_admin.is_(True), User.is_active.is_(True))
    if exclude is not None:
        q = q.where(User.id != exclude)
    return db.scalar(q)


def _set_sources(db: Session, u: User, all_sources: bool, source_ids: list[int] | None) -> None:
    u.all_sources = all_sources
    db.execute(delete(UserSource).where(UserSource.user_id == u.id))
    if not all_sources:
        for sid in _source_ids(db, source_ids):
            db.add(UserSource(user_id=u.id, source_id=sid))


# ---- users ------------------------------------------------------------------------------------

@router.get("/users")
def list_users(me: User = Depends(require_admin), db: Session = Depends(get_db)):
    users = db.scalars(select(User).order_by(User.is_admin.desc(), func.lower(User.username))).all()
    return {"items": [_user_dict(db, u, me) for u in users]}


class UserIn(BaseModel):
    username: str
    password: str
    is_admin: bool = False
    all_sources: bool = True
    source_ids: list[int] = []


@router.post("/users", status_code=201)
def add_user(body: UserIn, me: User = Depends(require_admin), db: Session = Depends(get_db)):
    name = validate_username(body.username)
    validate_password(body.password)
    if db.scalar(select(User).where(func.lower(User.username) == name.lower())):
        raise HTTPException(409, f"The username '{name}' is taken")
    ids = [] if body.all_sources else _source_ids(db, body.source_ids)
    u = create_user(db, name, body.password, is_admin=body.is_admin, all_sources=body.all_sources, source_ids=ids)
    return _user_dict(db, u, me)


class UserPatch(BaseModel):
    username: str | None = None
    password: str | None = None
    is_admin: bool | None = None
    is_active: bool | None = None
    all_sources: bool | None = None
    source_ids: list[int] | None = None


@router.patch("/users/{user_id}")
def edit_user(user_id: int, body: UserPatch, me: User = Depends(require_admin), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "User not found")
    is_me = u.id == me.id
    if body.username is not None and body.username.strip() != u.username:
        name = validate_username(body.username)
        if db.scalar(select(User).where(func.lower(User.username) == name.lower(), User.id != u.id)):
            raise HTTPException(409, f"The username '{name}' is taken")
        u.username = name
    losing_admin = (body.is_admin is False and u.is_admin) or (body.is_active is False and u.is_active and u.is_admin)
    if losing_admin:
        if is_me:
            raise HTTPException(400, "You can't remove your own admin access or disable yourself")
        if _active_admins(db, exclude=u.id) == 0:
            raise HTTPException(400, "There must always be at least one active admin")
    if body.is_admin is not None:
        u.is_admin = body.is_admin
    if body.is_active is not None:
        if is_me and not body.is_active:
            raise HTTPException(400, "You can't disable yourself")
        u.is_active = body.is_active
        if not body.is_active:  # log them out everywhere
            db.execute(delete(UserSession).where(UserSession.user_id == u.id))
    if body.password:
        validate_password(body.password)
        u.password_hash = hash_password(body.password)
        if not is_me:  # an admin reset logs the user out on every device
            db.execute(delete(UserSession).where(UserSession.user_id == u.id))
    if body.all_sources is not None or body.source_ids is not None:
        all_sources = u.all_sources if body.all_sources is None else body.all_sources
        ids = body.source_ids if body.source_ids is not None else list(
            db.scalars(select(UserSource.source_id).where(UserSource.user_id == u.id)))
        _set_sources(db, u, all_sources, ids)
    db.commit()
    return _user_dict(db, u, me)


@router.delete("/users/{user_id}")
def remove_user(user_id: int, me: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Delete a user and everything that is theirs (ratings, collections, categories, sessions)."""
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "User not found")
    if u.id == me.id:
        raise HTTPException(400, "You can't delete yourself")
    if u.is_admin and u.is_active and _active_admins(db, exclude=u.id) == 0:
        raise HTTPException(400, "There must always be at least one active admin")
    db.delete(u)
    db.commit()
    return {"ok": True}


# ---- visitors who are not logged in -------------------------------------------------------------

@router.get("/visibility/anonymous")
def get_anonymous_access(db: Session = Depends(get_db)):
    return visibility.anonymous_setting(db)


class AccessIn(BaseModel):
    all_sources: bool
    source_ids: list[int] = []


@router.put("/visibility/anonymous")
def set_anonymous_access(body: AccessIn, db: Session = Depends(get_db)):
    ids = [] if body.all_sources else _source_ids(db, body.source_ids)
    row = db.get(AppSetting, visibility.ANON_KEY)
    value = {"all_sources": body.all_sources, "source_ids": ids}
    if row is None:
        db.add(AppSetting(key=visibility.ANON_KEY, value=value))
    else:
        row.value = value
    db.commit()
    return visibility.anonymous_setting(db)
