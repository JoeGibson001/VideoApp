"""Feed selection and personalised ordering (DESIGN.md 5.4, 5.5)."""
import math
import random
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import (
    Category,
    CategoryItem,
    Collection,
    CollectionVideo,
    Rating,
    UserCreatorStats,
    UserSourceStats,
    UserTagStats,
    Video,
    VideoTag,
    utcnow,
)

FEED_TYPES = {"main", "tag", "creator", "source", "category", "collection"}


def _rated_exists(user_id: int):
    return exists().where(Rating.video_id == Video.id, Rating.user_id == user_id)


def build_query(db: Session, user_id: int | None, ftype: str, fid: int | None, unrated_only: bool,
                allowed: frozenset[int] | None = None):
    q = select(Video).where(Video.is_hidden.is_(False))
    if allowed is not None:  # only sources this viewer may see
        q = q.where(Video.source_id.in_(allowed))
    if ftype == "main":
        if user_id:
            q = q.where(~_rated_exists(user_id))
    elif ftype == "tag":
        q = q.where(exists().where(VideoTag.video_id == Video.id, VideoTag.tag_id == fid))
    elif ftype == "creator":
        q = q.where(Video.creator_id == fid)
    elif ftype == "source":
        q = q.where(Video.source_id == fid)
    elif ftype == "category":
        cat = db.get(Category, fid) if fid else None
        if cat is None or cat.user_id != user_id:
            raise HTTPException(404, "Category not found")
        items = db.scalars(select(CategoryItem).where(CategoryItem.category_id == cat.id)).all()
        tag_ids = [i.item_id for i in items if i.item_type == "tag"]
        creator_ids = [i.item_id for i in items if i.item_type == "creator"]
        source_ids = [i.item_id for i in items if i.item_type == "source"]
        conds = []
        if tag_ids:
            conds.append(exists().where(VideoTag.video_id == Video.id, VideoTag.tag_id.in_(tag_ids)))
        if creator_ids:
            conds.append(Video.creator_id.in_(creator_ids))
        if source_ids:
            conds.append(Video.source_id.in_(source_ids))
        q = q.where(or_(*conds)) if conds else q.where(Video.id.is_(None))
    elif ftype == "collection":
        col = db.get(Collection, fid) if fid else None
        if col is None or col.user_id != user_id:
            raise HTTPException(404, "Collection not found")
        q = q.join(CollectionVideo, and_(CollectionVideo.video_id == Video.id, CollectionVideo.collection_id == col.id))
        if col.system_type == "liked":
            q = q.order_by(CollectionVideo.added_at.desc())
        else:
            q = q.order_by(CollectionVideo.position.asc(), CollectionVideo.added_at.asc())
    else:
        raise HTTPException(400, "Unknown feed type")
    if unrated_only and user_id and ftype != "main":
        q = q.where(~_rated_exists(user_id))
    return q


def _freshness(imported_at: datetime | None, now: datetime, days: float) -> float:
    if imported_at is None:
        return 0.0
    age = max((now - imported_at).total_seconds() / 86400.0, 0.0)
    return math.exp(-age / days)


def personalised_order(db: Session, user_id: int | None, candidates: list[Video], limit: int,
                       rng: random.Random | None = None) -> list[Video]:
    s = get_settings()
    rng = rng or random.Random()
    now = utcnow()
    if not candidates:
        return []
    if not user_id:
        scored = [(s.feed_w_fresh * _freshness(v.imported_at, now, s.feed_fresh_days) + s.feed_w_random * rng.random(), v)
                  for v in candidates]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [v for _, v in scored[:limit]]

    ids = [v.id for v in candidates]
    vt = db.execute(select(VideoTag.video_id, VideoTag.tag_id).where(VideoTag.video_id.in_(ids))).all()
    tags_of: dict[int, list[int]] = {}
    for vid, tid in vt:
        tags_of.setdefault(vid, []).append(tid)
    all_tags = {t for _, t in vt}
    tag_score = dict(db.execute(select(UserTagStats.tag_id, UserTagStats.score).where(
        UserTagStats.user_id == user_id, UserTagStats.tag_id.in_(all_tags))).all()) if all_tags else {}
    creator_score = dict(db.execute(select(UserCreatorStats.creator_id, UserCreatorStats.score).where(
        UserCreatorStats.user_id == user_id, UserCreatorStats.creator_id.in_({v.creator_id for v in candidates}))).all())
    source_score = dict(db.execute(select(UserSourceStats.source_id, UserSourceStats.score).where(
        UserSourceStats.user_id == user_id, UserSourceStats.source_id.in_({v.source_id for v in candidates}))).all())
    rated = set(db.scalars(select(Rating.video_id).where(Rating.user_id == user_id, Rating.video_id.in_(ids))))

    K = s.feed_k
    scored = []
    for v in candidates:
        rated_tags = [tag_score[t] for t in tags_of.get(v.id, []) if t in tag_score]
        tag_c = sum(math.tanh(x / K) for x in rated_tags) / len(rated_tags) if rated_tags else 0.0
        creator_c = math.tanh(creator_score.get(v.creator_id, 0) / K)
        source_c = math.tanh(source_score.get(v.source_id, 0) / K)
        score = (s.feed_w_tag * tag_c + s.feed_w_creator * creator_c + s.feed_w_source * source_c
                 + s.feed_w_fresh * _freshness(v.imported_at, now, s.feed_fresh_days)
                 + s.feed_w_random * rng.random())
        scored.append((score, v))
    scored.sort(key=lambda x: x[0], reverse=True)
    ranked = [v for _, v in scored]

    out: list[Video] = []
    used: set[int] = set()
    unrated_pool = [v for v in candidates if v.id not in rated]
    rng.shuffle(unrated_pool)
    every = max(s.feed_explore_every, 0)
    ri = 0
    while len(out) < limit and len(used) < len(candidates):
        slot = len(out) + 1
        pick = None
        if every and slot % every == 0:
            while unrated_pool and unrated_pool[-1].id in used:
                unrated_pool.pop()
            if unrated_pool:
                pick = unrated_pool.pop()
        if pick is None:
            while ri < len(ranked) and ranked[ri].id in used:
                ri += 1
            if ri >= len(ranked):
                break
            pick = ranked[ri]
        used.add(pick.id)
        out.append(pick)
    return out


def get_feed(db: Session, user_id: int | None, ftype: str, fid: int | None, unrated_only: bool,
             limit: int, exclude: list[int], allowed: frozenset[int] | None = None) -> list[Video]:
    q = build_query(db, user_id, ftype, fid, unrated_only, allowed)
    if exclude:
        q = q.where(Video.id.not_in(exclude))
    if ftype == "collection":
        return list(db.scalars(q.limit(limit)).unique())
    if ftype == "creator":
        q = q.order_by(Video.upload_date.is_(None), Video.upload_date.desc(), Video.imported_at.desc())
        return list(db.scalars(q.limit(limit)).unique())
    s = get_settings()
    candidates = list(db.scalars(q.order_by(_random()).limit(s.feed_candidates)).unique())
    return personalised_order(db, user_id, candidates, limit)


def _random():
    from sqlalchemy import func

    return func.random()
