"""Library lists and global search (DESIGN.md 6.1, 6.4)."""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    Category,
    CategoryItem,
    Collection,
    CollectionVideo,
    UserCreatorStats,
    UserSourceStats,
    UserTagStats,
)
from . import search_index
from .search_index import Entry, highlight_spans, match_score
from .serialize import media_url
from .text import compact, words

SORTS = {"score_desc", "score_asc", "name_asc", "name_desc", "newest", "oldest", "most_videos", "recent"}
ITEM_TYPES = ("tag", "creator", "source")


def user_stats(db: Session, user_id: int | None, kind: str, ids: list[int] | None = None) -> dict[int, dict]:
    if not user_id:
        return {}
    model, key = {"tag": (UserTagStats, "tag_id"), "creator": (UserCreatorStats, "creator_id"),
                  "source": (UserSourceStats, "source_id")}[kind]
    col = getattr(model, key)
    q = select(model).where(model.user_id == user_id)
    if ids is not None:
        q = q.where(col.in_(ids))
    return {getattr(r, key): {"score": r.score, "likes": r.likes, "dislikes": r.dislikes,
                              "last_rated_at": r.last_rated_at} for r in db.scalars(q)}


def entry_row(e: Entry, stats: dict | None, logged_in: bool) -> dict:
    row = {"type": e.type, "id": e.id, "name": e.name, "video_count": e.video_count,
           "created_at": e.extra.get("created_at").isoformat() + "Z" if e.extra.get("created_at") else None}
    if e.type == "creator":
        row["handle"] = e.extra.get("handle")
        row["avatar_url"] = media_url(e.extra.get("avatar_path"))
        row["source_id"] = e.extra.get("source_id")
    if e.type == "source":
        row["domain"] = e.extra.get("domain")
        row["icon_url"] = media_url(e.extra.get("icon_path"))
    if e.type == "tag" and e.aliases:
        row["aliases"] = e.aliases
    if logged_in:
        st = stats or {}
        row.update({"score": st.get("score", 0), "likes": st.get("likes", 0), "dislikes": st.get("dislikes", 0),
                    "last_rated_at": st["last_rated_at"].isoformat() + "Z" if st.get("last_rated_at") else None})
    return row


def list_items(db: Session, user_id: int | None, kind: str, sort: str | None, q: str | None,
               page: int, page_size: int, only_ids: set[int] | None = None) -> dict:
    idx = search_index.get_index(db)
    stats = user_stats(db, user_id, kind)
    entries = idx[kind]
    if only_ids is not None:
        entries = [e for e in entries if e.id in only_ids]
    if q and compact(q):
        res = search_index.search_entries(db, q, [kind]).get(kind, [])
        if only_ids is not None:
            res = [r for r in res if r[0].id in only_ids]
        res.sort(key=lambda r: (-r[2], -(stats.get(r[0].id, {}).get("score", 0)), -r[0].video_count,
                                r[0].name.lower()))
        ordered = [r[0] for r in res]
    else:
        sort = sort or ("score_desc" if user_id else "name_asc")
        ordered = sort_entries(entries, stats, sort)
    total = len(ordered)
    chunk = ordered[(page - 1) * page_size: page * page_size]
    items = [entry_row(e, stats.get(e.id), bool(user_id)) for e in chunk]
    if q:
        for it in items:
            it["highlight"] = highlight_spans(q, it["name"])
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def sort_entries(entries: list[Entry], stats: dict, sort: str) -> list[Entry]:
    def sc(e):
        return stats.get(e.id, {}).get("score", 0)

    name = lambda e: e.name.lower()  # noqa: E731
    if sort == "score_desc":
        return sorted(entries, key=lambda e: (-sc(e), -e.video_count, name(e)))
    if sort == "score_asc":
        return sorted(entries, key=lambda e: (sc(e), -e.video_count, name(e)))
    if sort == "name_desc":
        return sorted(entries, key=name, reverse=True)
    if sort == "newest":
        return sorted(entries, key=lambda e: e.extra.get("created_at") or 0, reverse=True)
    if sort == "oldest":
        return sorted(entries, key=lambda e: e.extra.get("created_at") or 0)
    if sort == "most_videos":
        return sorted(entries, key=lambda e: (-e.video_count, name(e)))
    if sort == "recent":
        def last(e):
            v = stats.get(e.id, {}).get("last_rated_at")
            return v.isoformat() if v else ""
        return sorted(entries, key=last, reverse=True)
    return sorted(entries, key=name)


def _score_name(q: str, name: str) -> tuple[int, int] | None:
    return match_score(compact(q), [compact(name)], [words(name)], [])


def search(db: Session, user_id: int | None, q: str, types: list[str], limit: int) -> dict:
    allowed = set(ITEM_TYPES) | ({"collection", "category"} if user_id else set())
    types = [t for t in types if t in allowed] or [t for t in ("tag", "creator", "source", "collection", "category")
                                                   if t in allowed]
    groups: dict[str, dict] = {}
    item_types = [t for t in types if t in ITEM_TYPES]
    matched = search_index.search_entries(db, q, item_types) if q.strip() else {}
    for t in item_types:
        res = matched.get(t, [])
        stats = user_stats(db, user_id, t, [r[0].id for r in res])
        res.sort(key=lambda r: (-r[2], -(stats.get(r[0].id, {}).get("score", 0)), -r[0].video_count,
                                r[0].name.lower()))
        items = []
        for e, rank, _ in res[:limit]:
            row = entry_row(e, stats.get(e.id), bool(user_id))
            row["match_rank"] = rank
            row["highlight"] = highlight_spans(q, e.name)
            items.append(row)
        groups[t] = {"total": len(res), "items": items}
    if user_id and "collection" in types:
        counts = dict(db.execute(select(CollectionVideo.collection_id, func.count()).group_by(
            CollectionVideo.collection_id)).all())
        res = []
        for c in db.scalars(select(Collection).where(Collection.user_id == user_id)):
            m = _score_name(q, c.name)
            if m:
                res.append((m, c))
        res.sort(key=lambda r: (-r[0][1], -counts.get(r[1].id, 0), r[1].name.lower()))
        groups["collection"] = {"total": len(res), "items": [
            {"type": "collection", "id": c.id, "name": c.name, "system_type": c.system_type,
             "video_count": counts.get(c.id, 0), "match_rank": m[0], "highlight": highlight_spans(q, c.name)}
            for m, c in res[:limit]]}
    if user_id and "category" in types:
        counts = dict(db.execute(select(CategoryItem.category_id, func.count()).group_by(
            CategoryItem.category_id)).all())
        res = []
        for c in db.scalars(select(Category).where(Category.user_id == user_id)):
            m = _score_name(q, c.name)
            if m:
                res.append((m, c))
        res.sort(key=lambda r: (-r[0][1], -counts.get(r[1].id, 0), r[1].name.lower()))
        groups["category"] = {"total": len(res), "items": [
            {"type": "category", "id": c.id, "name": c.name, "item_count": counts.get(c.id, 0),
             "match_rank": m[0], "highlight": highlight_spans(q, c.name)} for m, c in res[:limit]]}
    return {"q": q, "groups": groups}
