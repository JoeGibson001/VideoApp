"""Rating rules, per-user score aggregates and the Liked collection (DESIGN.md 4)."""
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from ..models import (
    CollectionVideo,
    Rating,
    UserCreatorStats,
    UserSourceStats,
    UserTagStats,
    Video,
    VideoTag,
    utcnow,
)
from .users import system_collection

_STATS = {
    "tag": (UserTagStats, "user_tag_stats", "tag_id"),
    "creator": (UserCreatorStats, "user_creator_stats", "creator_id"),
    "source": (UserSourceStats, "user_source_stats", "source_id"),
}


def _video_items(db: Session, video: Video) -> dict[str, list[int]]:
    tag_ids = list(db.scalars(select(VideoTag.tag_id).where(VideoTag.video_id == video.id)))
    return {"tag": tag_ids, "creator": [video.creator_id], "source": [video.source_id]}


def _bump(db: Session, kind: str, user_id: int, item_id: int, dl: int, dd: int, when: datetime | None) -> None:
    model, _, key = _STATS[kind]
    row = db.get(model, {"user_id": user_id, key: item_id})
    if row is None:
        row = model(user_id=user_id, likes=0, dislikes=0, score=0, last_rated_at=None, **{key: item_id})
        db.add(row)
    row.likes += dl
    row.dislikes += dd
    row.score = row.likes - row.dislikes
    if when is not None:
        row.last_rated_at = when
    if row.likes == 0 and row.dislikes == 0:
        db.delete(row)
    db.flush()


def _apply(db: Session, user_id: int, video: Video, value: int, sign: int, when: datetime | None) -> None:
    dl = sign if value > 0 else 0
    dd = sign if value < 0 else 0
    for kind, ids in _video_items(db, video).items():
        for item_id in ids:
            _bump(db, kind, user_id, item_id, dl, dd, when)


def _refresh_last_rated(db: Session, user_id: int, video: Video) -> None:
    """After clearing a rating, last_rated_at must reflect the remaining ratings (matches recompute)."""
    for kind, ids in _video_items(db, video).items():
        model, table, key = _STATS[kind]
        for item_id in ids:
            row = db.get(model, {"user_id": user_id, key: item_id})
            if row is None:
                continue
            if kind == "tag":
                q = text("SELECT MAX(r.updated_at) FROM ratings r JOIN video_tags vt ON vt.video_id = r.video_id "
                         "WHERE r.user_id = :u AND vt.tag_id = :i")
            else:
                q = text(f"SELECT MAX(r.updated_at) FROM ratings r JOIN videos v ON v.id = r.video_id "
                         f"WHERE r.user_id = :u AND v.{key} = :i")
            val = db.execute(q, {"u": user_id, "i": item_id}).scalar()
            row.last_rated_at = _parse_dt(val)
    db.flush()


def _parse_dt(val) -> datetime | None:
    if val is None or isinstance(val, datetime):
        return val
    return datetime.fromisoformat(str(val))


def _sync_liked(db: Session, user_id: int, video_id: int, liked: bool, when: datetime) -> None:
    liked_col = system_collection(db, user_id, "liked")
    existing = db.get(CollectionVideo, {"collection_id": liked_col.id, "video_id": video_id})
    if liked:
        if existing is None:
            db.add(CollectionVideo(collection_id=liked_col.id, video_id=video_id, position=0, added_at=when))
        else:
            existing.added_at = when
    elif existing is not None:
        db.delete(existing)
    db.flush()


class RatingResult:
    def __init__(self, status: str, value: int | None):
        self.status = status  # created | changed | unchanged | cleared | none
        self.value = value


def set_rating(db: Session, user_id: int, video: Video, value: int) -> RatingResult:
    if value not in (1, -1):
        raise ValueError("value must be 1 or -1")
    now = utcnow()
    rating = db.get(Rating, {"user_id": user_id, "video_id": video.id})
    if rating is not None and rating.value == value:
        return RatingResult("unchanged", value)
    status = "created"
    if rating is not None:
        _apply(db, user_id, video, rating.value, -1, None)
        rating.value = value
        rating.updated_at = now
        status = "changed"
    else:
        rating = Rating(user_id=user_id, video_id=video.id, value=value, rated_at=now, updated_at=now)
        db.add(rating)
    db.flush()
    _apply(db, user_id, video, value, +1, now)
    _sync_liked(db, user_id, video.id, value > 0, now)
    db.commit()
    return RatingResult(status, value)


def clear_rating(db: Session, user_id: int, video: Video) -> RatingResult:
    rating = db.get(Rating, {"user_id": user_id, "video_id": video.id})
    if rating is None:
        return RatingResult("none", None)
    _apply(db, user_id, video, rating.value, -1, None)
    db.delete(rating)
    db.flush()
    _refresh_last_rated(db, user_id, video)
    _sync_liked(db, user_id, video.id, False, utcnow())
    db.commit()
    return RatingResult("cleared", None)


# ---- recompute from the ratings table ---------------------------------------------------------

def _in_clause(ids: Iterable[int]) -> str:
    ids = [int(i) for i in ids]
    return ",".join(str(i) for i in ids) if ids else "NULL"


def recompute_tags(db: Session, tag_ids: Iterable[int] | None = None) -> None:
    where = "" if tag_ids is None else f"WHERE vt.tag_id IN ({_in_clause(tag_ids)})"
    if tag_ids is None:
        db.execute(text("DELETE FROM user_tag_stats"))
    else:
        db.execute(text(f"DELETE FROM user_tag_stats WHERE tag_id IN ({_in_clause(tag_ids)})"))
    db.execute(text(
        "INSERT INTO user_tag_stats (user_id, tag_id, likes, dislikes, score, last_rated_at) "
        "SELECT r.user_id, vt.tag_id, SUM(CASE WHEN r.value > 0 THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN r.value < 0 THEN 1 ELSE 0 END), SUM(r.value), MAX(r.updated_at) "
        f"FROM ratings r JOIN video_tags vt ON vt.video_id = r.video_id {where} GROUP BY r.user_id, vt.tag_id"))


def _recompute_simple(db: Session, kind: str, ids: Iterable[int] | None) -> None:
    _, table, key = _STATS[kind]
    where = "" if ids is None else f"WHERE v.{key} IN ({_in_clause(ids)})"
    if ids is None:
        db.execute(text(f"DELETE FROM {table}"))
    else:
        db.execute(text(f"DELETE FROM {table} WHERE {key} IN ({_in_clause(ids)})"))
    db.execute(text(
        f"INSERT INTO {table} (user_id, {key}, likes, dislikes, score, last_rated_at) "
        "SELECT r.user_id, v." + key + ", SUM(CASE WHEN r.value > 0 THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN r.value < 0 THEN 1 ELSE 0 END), SUM(r.value), MAX(r.updated_at) "
        f"FROM ratings r JOIN videos v ON v.id = r.video_id {where} GROUP BY r.user_id, v.{key}"))


def recompute_creators(db: Session, creator_ids: Iterable[int] | None = None) -> None:
    _recompute_simple(db, "creator", creator_ids)


def recompute_sources(db: Session, source_ids: Iterable[int] | None = None) -> None:
    _recompute_simple(db, "source", source_ids)


def rebuild_liked(db: Session) -> None:
    from ..models import Collection, User

    for user in db.scalars(select(User)):
        system_collection(db, user.id, "liked")
    db.flush()
    liked_ids = select(Collection.id).where(Collection.system_type == "liked")
    db.execute(delete(CollectionVideo).where(CollectionVideo.collection_id.in_(liked_ids)))
    db.execute(text(
        "INSERT INTO collection_videos (collection_id, video_id, position, added_at) "
        "SELECT c.id, r.video_id, 0, r.updated_at FROM ratings r "
        "JOIN collections c ON c.user_id = r.user_id AND c.system_type = 'liked' WHERE r.value > 0"))


def recompute_all(db: Session) -> None:
    recompute_tags(db)
    recompute_creators(db)
    recompute_sources(db)
    rebuild_liked(db)
    db.commit()
