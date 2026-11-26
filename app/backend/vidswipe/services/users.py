"""User creation (kept as a service so a sign-up endpoint can be added later, DESIGN.md 8)."""
import logging

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


def create_user(db: Session, username: str, password: str, is_admin: bool = False) -> User:
    user = User(username=username, password_hash=hash_password(password), is_admin=is_admin, settings={})
    db.add(user)
    db.flush()
    ensure_system_collections(db, user)
    db.commit()
    return user


def seed_admin(db: Session) -> None:
    """Create the admin user on first start. Fails if ADMIN_PASSWORD is unset and no admin exists."""
    admin = db.scalar(select(User).where(User.username == "admin"))
    if admin:
        ensure_system_collections(db, admin)
        db.commit()
        return
    password = get_settings().admin_password
    if not password:
        raise RuntimeError("ADMIN_PASSWORD must be set on first run (no admin user exists)")
    create_user(db, "admin", password, is_admin=True)
    log.info("Seeded admin user")


def system_collection(db: Session, user_id: int, system_type: str) -> Collection:
    col = db.scalar(select(Collection).where(Collection.user_id == user_id, Collection.system_type == system_type))
    if col is None:
        col = Collection(user_id=user_id, name=system_type.capitalize(), system_type=system_type)
        db.add(col)
        db.flush()
    return col
