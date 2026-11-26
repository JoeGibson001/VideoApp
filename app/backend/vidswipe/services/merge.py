"""Tag and creator merging with aliases, preview, undo and duplicate suggestions (DESIGN.md 9.1)."""
from itertools import combinations

from fastapi import HTTPException
from rapidfuzz.distance import DamerauLevenshtein
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from ..models import (
    CategoryItem,
    Creator,
    CreatorAlias,
    MergeLog,
    Rating,
    Tag,
    TagAlias,
    TagDuplicateDismissal,
    User,
    UserCreatorStats,
    UserTagStats,
    Video,
    VideoTag,
    utcnow,
)
from . import search_index
from .ratings import recompute_creators, recompute_tags
from .text import compact, normalize_tag, singular

# ---------------------------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------------------------


def _load_tags(db: Session, from_ids: list[int], into_id: int) -> tuple[list[Tag], Tag]:
    from_ids = [i for i in dict.fromkeys(from_ids) if i != into_id]
    if not from_ids:
        raise HTTPException(400, "Nothing to merge")
    into = db.get(Tag, into_id)
    froms = [db.get(Tag, i) for i in from_ids]
    if into is None or any(t is None for t in froms):
        raise HTTPException(404, "Tag not found")
    return froms, into  # type: ignore[return-value]


def tag_merge_preview(db: Session, from_ids: list[int], into_id: int) -> dict:
    froms, into = _load_tags(db, from_ids, into_id)
    fids = [t.id for t in froms]
    from_videos = set(db.scalars(select(VideoTag.video_id).where(VideoTag.tag_id.in_(fids))))
    into_videos = set(db.scalars(select(VideoTag.video_id).where(VideoTag.tag_id == into.id)))
    cats = set(db.scalars(select(CategoryItem.category_id).where(CategoryItem.item_type == "tag",
                                                                 CategoryItem.item_id.in_(fids))))
    union = from_videos | into_videos
    users = []
    for u in db.scalars(select(User)):
        before = db.get(UserTagStats, {"user_id": u.id, "tag_id": into.id})
        rows = db.execute(select(Rating.value).where(Rating.user_id == u.id, Rating.video_id.in_(union))).all() \
            if union else []
        likes = sum(1 for (v,) in rows if v > 0)
        dislikes = sum(1 for (v,) in rows if v < 0)
        users.append({"user_id": u.id, "username": u.username, "score_before": before.score if before else 0,
                      "score_after": likes - dislikes, "likes_after": likes, "dislikes_after": dislikes})
    return {
        "from": [{"id": t.id, "name": t.name} for t in froms],
        "into": {"id": into.id, "name": into.name},
        "videos_affected": len(from_videos),
        "videos_with_both": len(from_videos & into_videos),
        "categories_affected": len(cats),
        "users": users,
    }


def merge_tags(db: Session, from_ids: list[int], into_id: int, user_id: int | None) -> MergeLog:
    froms, into = _load_tags(db, from_ids, into_id)
    fids = [t.id for t in froms]
    into_pre_videos = sorted(db.scalars(select(VideoTag.video_id).where(VideoTag.tag_id == into.id)))
    from_rows = [{"video_id": r.video_id, "tag_id": r.tag_id, "origin_name": r.origin_name, "origin": r.origin}
                 for r in db.scalars(select(VideoTag).where(VideoTag.tag_id.in_(fids)))]
    from_aliases = [{"alias": a.alias, "tag_id": a.tag_id}
                    for a in db.scalars(select(TagAlias).where(TagAlias.tag_id.in_(fids)))]
    into_cats = set(db.scalars(select(CategoryItem.category_id).where(CategoryItem.item_type == "tag",
                                                                      CategoryItem.item_id == into.id)))
    cat_rows = [{"id": c.id, "category_id": c.category_id, "item_id": c.item_id}
                for c in db.scalars(select(CategoryItem).where(CategoryItem.item_type == "tag",
                                                               CategoryItem.item_id.in_(fids)))]
    snapshot = {
        "from_tags": [{"id": t.id, "name": t.name, "created_at": t.created_at.isoformat()} for t in froms],
        "into_name": into.name,
        "into_pre_videos": into_pre_videos,
        "from_video_tags": from_rows,
        "from_aliases": from_aliases,
        "new_aliases": [t.name for t in froms],
        "category_items": cat_rows,
        "into_pre_categories": sorted(into_cats),
    }

    # 1. video_tags: re-point, dropping duplicates
    into_set = set(into_pre_videos)
    for r in db.scalars(select(VideoTag).where(VideoTag.tag_id.in_(fids))).all():
        if r.video_id in into_set:
            db.delete(r)
        else:
            vid, oname, origin = r.video_id, r.origin_name, r.origin
            db.delete(r)
            db.flush()
            db.add(VideoTag(video_id=vid, tag_id=into.id, origin_name=oname, origin=origin))
            into_set.add(vid)
    db.flush()
    # 2. category items
    for c in db.scalars(select(CategoryItem).where(CategoryItem.item_type == "tag",
                                                   CategoryItem.item_id.in_(fids))).all():
        if c.category_id in into_cats:
            db.delete(c)
        else:
            c.item_id = into.id
            into_cats.add(c.category_id)
    db.flush()
    # 3. aliases: existing aliases of from tags re-point; from names become aliases
    for a in db.scalars(select(TagAlias).where(TagAlias.tag_id.in_(fids))).all():
        a.tag_id = into.id
    for t in froms:
        if db.get(TagAlias, t.name) is None:
            db.add(TagAlias(alias=t.name, tag_id=into.id))
    db.flush()
    # 4/5. stats and delete from tags
    # per-source tag URLs and tag watches follow the surviving tag (existing ones on it win)
    in_fids = ",".join(str(int(i)) for i in fids)
    db.execute(text(f"UPDATE OR IGNORE source_tags SET tag_id = :into WHERE tag_id IN ({in_fids})"), {"into": into.id})
    db.execute(text(f"UPDATE watch_links SET tag_id = :into WHERE tag_id IN ({in_fids})"), {"into": into.id})
    db.execute(delete(UserTagStats).where(UserTagStats.tag_id.in_(fids)))
    for t in froms:
        db.delete(t)
    db.flush()
    recompute_tags(db, [into.id])
    # 6. log
    log = MergeLog(entity_type="tag", from_ids=fids, from_names=[t.name for t in froms], into_id=into.id,
                   snapshot=snapshot, merged_by=user_id)
    db.add(log)
    db.commit()
    search_index.invalidate()
    return log


def _later_merges_touching(db: Session, log: MergeLog, ids: set[int]) -> bool:
    later = db.scalars(select(MergeLog).where(MergeLog.entity_type == log.entity_type, MergeLog.id > log.id,
                                              MergeLog.undone_at.is_(None))).all()
    for m in later:
        if m.into_id in ids or ids.intersection(m.from_ids):
            return True
    return False


def undo_tag_merge(db: Session, log: MergeLog) -> None:
    snap = log.snapshot
    into = db.get(Tag, log.into_id)
    if into is None:
        raise HTTPException(409, "Undo blocked: the target tag has since been merged or deleted")
    if _later_merges_touching(db, log, {log.into_id}):
        raise HTTPException(409, "Undo blocked: undo later merges involving this tag first")
    for t in snap["from_tags"]:
        if db.scalar(select(Tag).where(Tag.name == t["name"])) is not None or db.get(Tag, t["id"]) is not None:
            raise HTTPException(409, f"Undo blocked: a tag named '{t['name']}' exists again")

    from datetime import datetime

    # restore tags with original ids
    for t in snap["from_tags"]:
        db.add(Tag(id=t["id"], name=t["name"], created_at=datetime.fromisoformat(t["created_at"])))
    db.flush()
    name_to_from: dict[str, int] = {t["name"]: t["id"] for t in snap["from_tags"]}
    for a in snap["from_aliases"]:
        name_to_from[a["alias"]] = a["tag_id"]
    # aliases: remove the ones the merge created, restore the from tags' own aliases
    for name in snap["new_aliases"]:
        db.execute(delete(TagAlias).where(TagAlias.alias == name))
    for a in snap["from_aliases"]:
        row = db.get(TagAlias, a["alias"])
        if row is None:
            db.add(TagAlias(alias=a["alias"], tag_id=a["tag_id"]))
        else:
            row.tag_id = a["tag_id"]
    db.flush()
    into_pre = set(snap["into_pre_videos"])
    # restore snapshot rows
    snap_videos = set()
    for r in snap["from_video_tags"]:
        snap_videos.add(r["video_id"])
        if db.get(Video, r["video_id"]) is None:
            continue
        if r["video_id"] not in into_pre:
            db.execute(delete(VideoTag).where(VideoTag.video_id == r["video_id"], VideoTag.tag_id == into.id))
        if db.get(VideoTag, {"video_id": r["video_id"], "tag_id": r["tag_id"]}) is None:
            db.add(VideoTag(video_id=r["video_id"], tag_id=r["tag_id"], origin_name=r["origin_name"],
                            origin=r["origin"]))
        db.flush()
    # post-merge rows whose origin_name maps to a from tag
    for r in db.scalars(select(VideoTag).where(VideoTag.tag_id == into.id)).all():
        if r.video_id in into_pre or r.video_id in snap_videos:
            continue
        target = name_to_from.get(normalize_tag(r.origin_name))
        if target is None:
            continue
        vid, oname, origin = r.video_id, r.origin_name, r.origin
        db.delete(r)
        db.flush()
        if db.get(VideoTag, {"video_id": vid, "tag_id": target}) is None:
            db.add(VideoTag(video_id=vid, tag_id=target, origin_name=oname, origin=origin))
        db.flush()
    # category items
    pre_cats = set(snap["into_pre_categories"])
    for c in snap["category_items"]:
        if c["category_id"] not in pre_cats:
            db.execute(delete(CategoryItem).where(CategoryItem.category_id == c["category_id"],
                                                  CategoryItem.item_type == "tag", CategoryItem.item_id == into.id))
        exists_row = db.scalar(select(CategoryItem).where(CategoryItem.category_id == c["category_id"],
                                                          CategoryItem.item_type == "tag",
                                                          CategoryItem.item_id == c["item_id"]))
        if exists_row is None and db.execute(text("SELECT 1 FROM categories WHERE id=:i"),
                                             {"i": c["category_id"]}).scalar():
            db.add(CategoryItem(category_id=c["category_id"], item_type="tag", item_id=c["item_id"]))
        db.flush()
    recompute_tags(db, [into.id] + [t["id"] for t in snap["from_tags"]])
    log.undone_at = utcnow()
    db.commit()
    search_index.invalidate()


# ---------------------------------------------------------------------------------------------
# Creators
# ---------------------------------------------------------------------------------------------


def _load_creators(db: Session, from_ids: list[int], into_id: int) -> tuple[list[Creator], Creator]:
    from_ids = [i for i in dict.fromkeys(from_ids) if i != into_id]
    if not from_ids:
        raise HTTPException(400, "Nothing to merge")
    into = db.get(Creator, into_id)
    froms = [db.get(Creator, i) for i in from_ids]
    if into is None or any(c is None for c in froms):
        raise HTTPException(404, "Creator not found")
    if any(c.source_id != into.source_id for c in froms):  # type: ignore[union-attr]
        raise HTTPException(400, "Creators can only be merged within the same source")
    return froms, into  # type: ignore[return-value]


def creator_merge_preview(db: Session, from_ids: list[int], into_id: int) -> dict:
    froms, into = _load_creators(db, from_ids, into_id)
    fids = [c.id for c in froms]
    n = db.scalar(select(func.count()).select_from(Video).where(Video.creator_id.in_(fids)))
    cats = db.scalar(select(func.count(func.distinct(CategoryItem.category_id))).where(
        CategoryItem.item_type == "creator", CategoryItem.item_id.in_(fids)))
    users = []
    for u in db.scalars(select(User)):
        before = db.get(UserCreatorStats, {"user_id": u.id, "creator_id": into.id})
        vals = db.execute(select(Rating.value).join(Video, Video.id == Rating.video_id).where(
            Rating.user_id == u.id, Video.creator_id.in_(fids + [into.id]))).all()
        users.append({"user_id": u.id, "username": u.username, "score_before": before.score if before else 0,
                      "score_after": sum(v for (v,) in vals)})
    return {"from": [{"id": c.id, "name": c.name, "handle": c.handle} for c in froms],
            "into": {"id": into.id, "name": into.name, "handle": into.handle},
            "videos_affected": n, "videos_with_both": 0, "categories_affected": cats, "users": users}


def merge_creators(db: Session, from_ids: list[int], into_id: int, user_id: int | None) -> MergeLog:
    froms, into = _load_creators(db, from_ids, into_id)
    fids = [c.id for c in froms]
    videos_by_creator = {c.id: sorted(db.scalars(select(Video.id).where(Video.creator_id == c.id))) for c in froms}
    from_aliases = [{"source_id": a.source_id, "handle": a.handle, "creator_id": a.creator_id}
                    for a in db.scalars(select(CreatorAlias).where(CreatorAlias.creator_id.in_(fids)))]
    into_cats = set(db.scalars(select(CategoryItem.category_id).where(CategoryItem.item_type == "creator",
                                                                      CategoryItem.item_id == into.id)))
    cat_rows = [{"category_id": c.category_id, "item_id": c.item_id}
                for c in db.scalars(select(CategoryItem).where(CategoryItem.item_type == "creator",
                                                               CategoryItem.item_id.in_(fids)))]
    snapshot = {
        "from_creators": [{"id": c.id, "source_id": c.source_id, "name": c.name, "handle": c.handle,
                           "profile_url": c.profile_url, "avatar_path": c.avatar_path,
                           "created_at": c.created_at.isoformat()} for c in froms],
        "videos_by_creator": {str(k): v for k, v in videos_by_creator.items()},
        "from_aliases": from_aliases,
        "new_aliases": [{"source_id": c.source_id, "handle": c.handle} for c in froms],
        "category_items": cat_rows,
        "into_pre_categories": sorted(into_cats),
    }
    db.execute(Video.__table__.update().where(Video.creator_id.in_(fids)).values(creator_id=into.id))
    for c in db.scalars(select(CategoryItem).where(CategoryItem.item_type == "creator",
                                                   CategoryItem.item_id.in_(fids))).all():
        if c.category_id in into_cats:
            db.delete(c)
        else:
            c.item_id = into.id
            into_cats.add(c.category_id)
    for a in db.scalars(select(CreatorAlias).where(CreatorAlias.creator_id.in_(fids))).all():
        a.creator_id = into.id
    in_fids = ",".join(str(int(i)) for i in fids)
    db.execute(text(f"UPDATE watch_links SET creator_id = :into WHERE creator_id IN ({in_fids})"), {"into": into.id})
    db.flush()
    for c in froms:
        db.delete(c)
    db.flush()
    for c in froms:
        if db.get(CreatorAlias, (c.source_id, c.handle)) is None:
            db.add(CreatorAlias(source_id=c.source_id, handle=c.handle, creator_id=into.id))
    db.execute(delete(UserCreatorStats).where(UserCreatorStats.creator_id.in_(fids)))
    db.flush()
    recompute_creators(db, [into.id])
    log = MergeLog(entity_type="creator", from_ids=fids, from_names=[c.name for c in froms], into_id=into.id,
                   snapshot=snapshot, merged_by=user_id)
    db.add(log)
    db.commit()
    search_index.invalidate()
    return log


def undo_creator_merge(db: Session, log: MergeLog) -> None:
    from datetime import datetime

    snap = log.snapshot
    into = db.get(Creator, log.into_id)
    if into is None:
        raise HTTPException(409, "Undo blocked: the target creator has since been merged or deleted")
    if _later_merges_touching(db, log, {log.into_id}):
        raise HTTPException(409, "Undo blocked: undo later merges involving this creator first")
    for c in snap["from_creators"]:
        if db.get(Creator, c["id"]) or db.scalar(select(Creator).where(
                Creator.source_id == c["source_id"], Creator.handle == c["handle"])):
            raise HTTPException(409, f"Undo blocked: creator '{c['handle']}' exists again")
    for a in snap["new_aliases"]:
        db.execute(delete(CreatorAlias).where(CreatorAlias.source_id == a["source_id"],
                                              CreatorAlias.handle == a["handle"]))
    for c in snap["from_creators"]:
        db.add(Creator(id=c["id"], source_id=c["source_id"], name=c["name"], handle=c["handle"],
                       profile_url=c["profile_url"], avatar_path=c["avatar_path"],
                       created_at=datetime.fromisoformat(c["created_at"])))
    db.flush()
    handle_to_from = {c["handle"]: c["id"] for c in snap["from_creators"]}
    for a in snap["from_aliases"]:
        handle_to_from[a["handle"]] = a["creator_id"]
        row = db.get(CreatorAlias, (a["source_id"], a["handle"]))
        if row is None:
            db.add(CreatorAlias(source_id=a["source_id"], handle=a["handle"], creator_id=a["creator_id"]))
        else:
            row.creator_id = a["creator_id"]
    snap_videos = set()
    for cid, vids in snap["videos_by_creator"].items():
        snap_videos.update(vids)
        if vids:
            db.execute(Video.__table__.update().where(Video.id.in_(vids), Video.creator_id == into.id)
                       .values(creator_id=int(cid)))
    # videos imported after the merge under a from handle
    for v in db.scalars(select(Video).where(Video.creator_id == into.id)).all():
        if v.id not in snap_videos and v.creator_origin_handle in handle_to_from:
            v.creator_id = handle_to_from[v.creator_origin_handle]
    pre_cats = set(snap["into_pre_categories"])
    for c in snap["category_items"]:
        if c["category_id"] not in pre_cats:
            db.execute(delete(CategoryItem).where(CategoryItem.category_id == c["category_id"],
                                                  CategoryItem.item_type == "creator",
                                                  CategoryItem.item_id == into.id))
        if db.scalar(select(CategoryItem).where(CategoryItem.category_id == c["category_id"],
                                                CategoryItem.item_type == "creator",
                                                CategoryItem.item_id == c["item_id"])) is None:
            db.add(CategoryItem(category_id=c["category_id"], item_type="creator", item_id=c["item_id"]))
        db.flush()
    recompute_creators(db, [into.id] + [c["id"] for c in snap["from_creators"]])
    log.undone_at = utcnow()
    db.commit()
    search_index.invalidate()


def undo_merge(db: Session, log_id: int) -> MergeLog:
    log = db.get(MergeLog, log_id)
    if log is None:
        raise HTTPException(404, "Merge not found")
    if log.undone_at:
        raise HTTPException(409, "Already undone")
    if log.entity_type == "tag":
        undo_tag_merge(db, log)
    else:
        undo_creator_merge(db, log)
    return log


# ---------------------------------------------------------------------------------------------
# Aliases and duplicates
# ---------------------------------------------------------------------------------------------


def remove_tag_alias(db: Session, alias: str, split_existing: bool) -> dict:
    alias = normalize_tag(alias)
    row = db.get(TagAlias, alias)
    if row is None:
        raise HTTPException(404, "Alias not found")
    target_id = row.tag_id
    db.delete(row)
    db.flush()
    moved = 0
    new_tag_id = None
    if split_existing:
        rows = [r for r in db.scalars(select(VideoTag).where(VideoTag.tag_id == target_id)).all()
                if normalize_tag(r.origin_name) == alias]
        if rows:
            new_tag = db.scalar(select(Tag).where(Tag.name == alias))
            if new_tag is None:
                new_tag = Tag(name=alias)
                db.add(new_tag)
                db.flush()
            new_tag_id = new_tag.id
            for r in rows:
                vid, oname, origin = r.video_id, r.origin_name, r.origin
                db.delete(r)
                db.flush()
                db.add(VideoTag(video_id=vid, tag_id=new_tag.id, origin_name=oname, origin=origin))
                moved += 1
            db.flush()
            recompute_tags(db, [target_id, new_tag.id])
    db.commit()
    search_index.invalidate()
    return {"removed": alias, "moved": moved, "new_tag_id": new_tag_id}


def add_tag_alias(db: Session, tag_id: int, alias: str) -> None:
    alias = normalize_tag(alias)
    if not alias:
        raise HTTPException(400, "Empty alias")
    if db.scalar(select(Tag).where(Tag.name == alias)):
        raise HTTPException(409, f"A tag named '{alias}' exists; merge it instead")
    if db.get(TagAlias, alias):
        raise HTTPException(409, "Alias already exists")
    db.add(TagAlias(alias=alias, tag_id=tag_id))
    db.commit()
    search_index.invalidate()


def duplicate_suggestions(db: Session, limit: int = 100) -> list[dict]:
    tags = db.scalars(select(Tag)).all()
    dismissed = {(d.tag_a, d.tag_b) for d in db.scalars(select(TagDuplicateDismissal))}
    alias_pairs = set()
    for a in db.scalars(select(TagAlias)):
        t = db.scalar(select(Tag.id).where(Tag.name == a.alias))
        if t:
            alias_pairs.add(tuple(sorted((t, a.tag_id))))
    counts = dict(db.execute(select(VideoTag.tag_id, func.count()).group_by(VideoTag.tag_id)).all())
    forms = [(t, compact(t.name)) for t in tags]
    buckets: dict[str, list[tuple[Tag, str]]] = {}
    for t, c in forms:
        if not c:
            continue
        keys = {c[:2], singular(c)[:2]}
        for k in keys:
            buckets.setdefault(k, []).append((t, c))
    seen = set()
    out = []
    for items in buckets.values():
        for (a, ca), (b, cb) in combinations(items, 2):
            pair = tuple(sorted((a.id, b.id)))
            if pair in seen or pair in dismissed or pair in alias_pairs:
                continue
            seen.add(pair)
            reason = None
            if ca == cb:
                reason = "punctuation or spacing"
            elif singular(ca) == singular(cb) or singular(ca) == cb or singular(cb) == ca:
                reason = "singular / plural"
            elif min(len(ca), len(cb)) >= 5 and DamerauLevenshtein.distance(ca, cb, score_cutoff=2) <= 1:
                reason = "similar spelling"
            if reason:
                x, y = (a, b) if a.id == pair[0] else (b, a)
                out.append({"tag_a": {"id": x.id, "name": x.name, "video_count": counts.get(x.id, 0)},
                            "tag_b": {"id": y.id, "name": y.name, "video_count": counts.get(y.id, 0)},
                            "reason": reason})
    out.sort(key=lambda d: -(d["tag_a"]["video_count"] + d["tag_b"]["video_count"]))
    return out[:limit]


def dismiss_duplicate(db: Session, a: int, b: int) -> None:
    x, y = sorted((a, b))
    if db.get(TagDuplicateDismissal, {"tag_a": x, "tag_b": y}) is None:
        db.add(TagDuplicateDismissal(tag_a=x, tag_b=y))
        db.commit()
