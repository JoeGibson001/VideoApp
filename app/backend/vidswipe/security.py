"""Password hashing, cookie sessions, CSRF and auth dependencies (DESIGN.md 8, 14)."""
import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, Header, HTTPException, Request, Response
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import User, UserSession, utcnow

SESSION_COOKIE = "vs_session"
CSRF_COOKIE = "vs_csrf"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def constant_time_equals(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return hmac.compare_digest(a.encode(), b.encode())


def _token_id(token: str) -> str:
    secret = get_settings().session_secret.encode()
    return hmac.new(secret, token.encode(), hashlib.sha256).hexdigest()


def create_session(db: Session, user: User, response: Response) -> UserSession:
    s = get_settings()
    token = secrets.token_urlsafe(32)
    sess = UserSession(
        id=_token_id(token),
        user_id=user.id,
        csrf_token=secrets.token_urlsafe(24),
        expires_at=utcnow() + timedelta(days=s.session_days),
    )
    db.add(sess)
    db.commit()
    max_age = s.session_days * 86400
    response.set_cookie(SESSION_COOKIE, token, max_age=max_age, httponly=True, secure=s.cookie_secure,
                        samesite="lax", path="/")
    response.set_cookie(CSRF_COOKIE, sess.csrf_token, max_age=max_age, httponly=False, secure=s.cookie_secure,
                        samesite="lax", path="/")
    return sess


def destroy_session(db: Session, request: Request, response: Response) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        db.query(UserSession).filter(UserSession.id == _token_id(token)).delete()
        db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


@dataclass
class Auth:
    user: User | None
    session: UserSession | None

    @property
    def user_id(self) -> int | None:
        return self.user.id if self.user else None

    @property
    def is_admin(self) -> bool:
        return bool(self.user and self.user.is_admin)


def get_auth(request: Request, db: Session = Depends(get_db),
             x_csrf_token: str | None = Header(default=None)) -> Auth:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return Auth(None, None)
    sess = db.get(UserSession, _token_id(token))
    if not sess or sess.expires_at < utcnow():
        return Auth(None, None)
    user = db.get(User, sess.user_id)
    if user is None:
        return Auth(None, None)
    if request.method in UNSAFE_METHODS and not constant_time_equals(x_csrf_token, sess.csrf_token):
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid")
    return Auth(user, sess)


def require_user(auth: Auth = Depends(get_auth)) -> User:
    if auth.user is None:
        raise HTTPException(status_code=401, detail="Login required")
    return auth.user


def require_admin(auth: Auth = Depends(get_auth)) -> User:
    if auth.user is None:
        raise HTTPException(status_code=401, detail="Login required")
    if not auth.user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    return auth.user


def require_ingest_key(x_ingest_key: str | None = Header(default=None)) -> None:
    expected = get_settings().ingest_api_key
    if not expected or not constant_time_equals(x_ingest_key, expected):
        raise HTTPException(status_code=401, detail="Invalid ingest key")


class RateLimiter:
    """Simple in-memory sliding-window limiter, keyed by client IP."""

    def __init__(self) -> None:
        self.hits: dict[str, deque] = defaultdict(deque)

    def check(self, key: str, limit: int, window_s: int) -> bool:
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > window_s:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True

    def reset(self) -> None:
        self.hits.clear()


login_limiter = RateLimiter()
