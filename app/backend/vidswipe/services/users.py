"""User creation (kept as a service so a sign-up endpoint can be added later, DESIGN.md 8)."""
import logging
import re

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Collection, User
from ..security import hash_password

log = logging.getLogger(__name__)


def ensure_system_collections(db: Session, user: User) -> None:
    existing = {c.system_type for c in db.scalars(
        select(Collection).where(Collection.user_id == user.id, Collection.system_type.is_not(None)))}
    if "favorites" not in existing:
        db.add(Collection(user_id=user.id, name="Favorites", system_type="favorites"))
    if "liked" not in existing:
        db.add(Collection(user_id=user.id, name="Liked", system_type="liked"))


def create_user(db: Session, username: str, password: str, is_admin: bool = False, all_sources: bool = True,
                source_ids: list[int] | None = None) -> User:
    from ..models import UserSource

    user = User(username=username, password_hash=hash_password(password), is_admin=is_admin, is_active=True,
                all_sources=all_sources, settings={})
    db.add(user)
    db.flush()
    for sid in set(source_ids or []):
        db.add(UserSource(user_id=user.id, source_id=sid))
    ensure_system_collections(db, user)
    db.commit()
    return user


def seed_admin(db: Session) -> None:
    """Create the `admin` user on first start, i.e. only when there is no active admin at all.

    Once other admins exist, `admin` can be renamed or deleted and will not come back.
    Fails if ADMIN_PASSWORD is unset and no admin exists.
    """
    for u in db.scalars(select(User)):
        ensure_system_collections(db, u)
    db.commit()
    if db.scalar(select(User).where(User.is_admin.is_(True), User.is_active.is_(True)).limit(1)):
        return
    password = get_settings().admin_password
    if not password:
        raise RuntimeError("ADMIN_PASSWORD must be set on first run (no admin user exists)")
    existing = db.scalar(select(User).where(User.username == "admin"))
    if existing:  # e.g. the admin account was disabled or demoted: restore it so someone can log in
        existing.is_admin, existing.is_active = True, True
        db.commit()
        log.warning("No active admin found: re-enabled the 'admin' account")
        return
    create_user(db, "admin", password, is_admin=True)
    log.info("Seeded admin user")


USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
MIN_PASSWORD = 8


def validate_username(name: str) -> str:
    name = (name or "").strip()
    if not USERNAME_RE.match(name):
        raise HTTPException(400, "Username must be 1-64 characters: letters, numbers, dot, dash or underscore")
    return name


def validate_password(pw: str) -> None:
    if not pw or len(pw) < MIN_PASSWORD:
        raise HTTPException(400, f"Password must be at least {MIN_PASSWORD} characters")


def system_collection(db: Session, user_id: int, system_type: str) -> Collection:
    col = db.scalar(select(Collection).where(Collection.user_id == user_id, Collection.system_type == system_type))
    if col is None:
        col = Collection(user_id=user_id, name=system_type.capitalize(), system_type=system_type)
        db.add(col)
        db.flush()
    return col
