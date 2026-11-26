"""JSON shapes returned by the API."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Collection,
    CollectionVideo,
    Creator,
    Rating,
    Source,
    Tag,
    UserCreatorStats,
    UserSourceStats,
    UserTagStats,
    Video,
    VideoTag,
)


def media_url(path: str | None) -> str | None:
    if not path:
        return None
    return "/media/" + path.lstrip("/")


def source_dict(s: Source) -> dict:
    return {"id": s.id, "name": s.name, "domain": s.domain, "icon_url": media_url(s.icon_path)}


def creator_dict(c: Creator) -> dict:
    return {"id": c.id, "name": c.name, "handle": c.handle, "profile_url": c.profile_url,
            "avatar_url": media_url(c.avatar_path), "source_id": c.source_id}


def videos_summary(db: Session, videos: list[Video], user_id: int | None) -> list[dict]:
    """Feed card shape, batched to avoid N+1 queries."""
    if not videos:
        return []
    ids = [v.id for v in videos]
    tags_by_video: dict[int, list[dict]] = {}
    rows = db.execute(select(VideoTag.video_id, Tag.id, Tag.name).join(Tag, Tag.id == VideoTag.tag_id)
                      .where(VideoTag.video_id.in_(ids)).order_by(Tag.name)).all()
    for vid, tid, tname in rows:
        tags_by_video.setdefault(vid, []).append({"id": tid, "name": tname})
    ratings: dict[int, int] = {}
    favs: set[int] = set()
    if user_id:
        ratings = dict(db.execute(select(Rating.video_id, Rating.value)
                                  .where(Rating.user_id == user_id, Rating.video_id.in_(ids))).all())
        favs = set(db.scalars(select(CollectionVideo.video_id).join(Collection)
                              .where(Collection.user_id == user_id, Collection.system_type == "favorites",
                                     CollectionVideo.video_id.in_(ids))))
    out = []
    for v in videos:
        out.append({
            "id": v.id,
            "title": v.title,
            "creator": creator_dict(v.creator),
            "source": source_dict(v.source),
            "tags": tags_by_video.get(v.id, []),
            "video_url": media_url(v.file_path),
            "poster_url": media_url(v.poster_path),
            "width": v.width,
            "height": v.height,
            "duration_s": v.duration_s,
            "has_audio": v.has_audio,
            "is_hidden": v.is_hidden,
            "my_rating": ratings.get(v.id),
            "is_favorite": v.id in favs,
        })
    return out


def video_detail(db: Session, v: Video, user_id: int | None) -> dict:
    d = videos_summary(db, [v], user_id)[0]
    tag_scores: dict[int, int] = {}
    creator_score = source_score = None
    collections: list[dict] = []
    if user_id:
        tag_ids = [t["id"] for t in d["tags"]]
        tag_scores = dict(db.execute(select(UserTagStats.tag_id, UserTagStats.score).where(
            UserTagStats.user_id == user_id, UserTagStats.tag_id.in_(tag_ids))).all())
        cs = db.get(UserCreatorStats, {"user_id": user_id, "creator_id": v.creator_id})
        ss = db.get(UserSourceStats, {"user_id": user_id, "source_id": v.source_id})
        creator_score = cs.score if cs else 0
        source_score = ss.score if ss else 0
        for c in db.scalars(select(Collection).join(CollectionVideo).where(
                Collection.user_id == user_id, CollectionVideo.video_id == v.id)):
            collections.append({"id": c.id, "name": c.name, "system_type": c.system_type})
        for t in d["tags"]:
            t["score"] = tag_scores.get(t["id"], 0)
    d.update({
        "description": v.description,
        "source_url": v.source_url,
        "source_video_id": v.source_video_id,
        "filename": v.filename,
        "original_filename": v.original_filename,
        "file_path": v.file_path,
        "size_bytes": v.size_bytes,
        "fps": v.fps,
        "video_codec": v.video_codec,
        "audio_codec": v.audio_codec,
        "container": v.container,
        "upload_date": v.upload_date.isoformat() if v.upload_date else None,
        "imported_at": v.imported_at.isoformat() + "Z" if v.imported_at else None,
        "creator_score": creator_score,
        "source_score": source_score,
        "collections": collections,
    })
    if user_id:
        r = db.get(Rating, {"user_id": user_id, "video_id": v.id})
        d["rated_at"] = r.updated_at.isoformat() + "Z" if r else None
    return d
