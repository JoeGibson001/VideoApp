"""Which sources a viewer may see.

A viewer is a logged-in user or an anonymous visitor. `allowed` is either None (every source,
including ones added later) or a frozenset of source ids. Everything a viewer can reach (feeds,
videos, ratings, library lists, search, collections, categories and media files) is filtered by it.
Admin pages are not: admins always manage everything.
"""
import threading
from dataclasses import replace

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import AppSetting, Source, User, UserSource, Video, VideoTag

Allowed = frozenset[int] | None
ANON_KEY = "anonymous_sources"


def user_allowed(db: Session, user: User) -> Allowed:
    if user.all_sources:
        return None
    return frozenset(db.scalars(select(UserSource.source_id).where(UserSource.user_id == user.id)))


def anonymous_setting(db: Session) -> dict:
    row = db.get(AppSetting, ANON_KEY)
    value = (row.value if row else None) or {}
    # Default (no setting saved yet): visitors see nothing until an admin chooses sources for them.
    return {"all_sources": bool(value.get("all_sources", False)), "source_ids": list(value.get("source_ids", []))}


def anonymous_allowed(db: Session) -> Allowed:
    s = anonymous_setting(db)
    return None if s["all_sources"] else frozenset(int(i) for i in s["source_ids"])


def allowed_for(db: Session, user: User | None) -> Allowed:
    return user_allowed(db, user) if user else anonymous_allowed(db)


def can_see_source(allowed: Allowed, source_id: int | None) -> bool:
    return allowed is None or (source_id is not None and source_id in allowed)


def source_id_for_domain(db: Session, domain: str) -> int | None:
    return db.scalar(select(Source.id).where(Source.domain == domain))


# ---- per-viewer counts for library lists and search ------------------------------------------

_lock = threading.Lock()
_count_cache: dict[tuple[str, frozenset], dict[int, int]] = {}


def invalidate() -> None:
    with _lock:
        _count_cache.clear()


def visible_counts(db: Session, kind: str, allowed: frozenset[int]) -> dict[int, int]:
    """Visible (not hidden) video counts per tag / creator / source, limited to `allowed` sources."""
    key = (kind, allowed)
    with _lock:
        if key in _count_cache:
            return _count_cache[key]
    if not allowed:
        counts: dict[int, int] = {}
    else:
        base = [Video.is_hidden.is_(False), Video.source_id.in_(allowed)]
        if kind == "tag":
            q = select(VideoTag.tag_id, func.count(func.distinct(Video.id))).join(
                Video, Video.id == VideoTag.video_id).where(*base).group_by(VideoTag.tag_id)
        elif kind == "creator":
            q = select(Video.creator_id, func.count()).where(*base).group_by(Video.creator_id)
        else:
            q = select(Video.source_id, func.count()).where(*base).group_by(Video.source_id)
        counts = dict(db.execute(q).all())
    with _lock:
        _count_cache[key] = counts
    return counts


def visible_entries(db: Session, kind: str, allowed: Allowed) -> dict:
    """Search-index entries a viewer may see, keyed by id, with video counts for that viewer.

    Tags are visible when at least one visible video from an allowed source carries them;
    creators and sources when their source is allowed.
    """
    from . import search_index

    entries = search_index.get_index(db)[kind]
    if allowed is None:
        return {e.id: e for e in entries}
    counts = visible_counts(db, kind, allowed)
    out = {}
    for e in entries:
        if kind == "tag":
            n = counts.get(e.id, 0)
            if not n:
                continue
        elif kind == "creator":
            if e.extra.get("source_id") not in allowed:
                continue
            n = counts.get(e.id, 0)
        else:
            if e.id not in allowed:
                continue
            n = counts.get(e.id, 0)
        out[e.id] = replace(e, video_count=n)
    return out
