"""Public/user API: feed, videos, ratings, library, search, categories, collections (DESIGN.md 10)."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    Category,
    CategoryItem,
    Collection,
    CollectionVideo,
    Creator,
    CreatorAlias,
    SearchHistory,
    Source,
    Tag,
    User,
    Video,
    utcnow,
)
from ..security import Auth, get_auth, require_user
from ..services import importer_client, library, visibility
from ..services.feed import FEED_TYPES, get_feed
from ..services.ratings import clear_rating, set_rating
from ..services.serialize import creator_dict, media_url, source_dict, video_detail, videos_summary

router = APIRouter(prefix="/api", tags=["api"])


def _parse_ids(raw: str | None) -> list[int]:
    if not raw:
        return []
    out = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return out


def _allowed(db: Session, auth: Auth) -> visibility.Allowed:
    """Sources this viewer may see (None = all)."""
    return visibility.allowed_for(db, auth.user)


def _visible_video(db: Session, video_id: int, auth: Auth) -> Video:
    v = db.get(Video, video_id)
    if v is None or (v.is_hidden and not auth.is_admin) or not visibility.can_see_source(_allowed(db, auth), v.source_id):
        raise HTTPException(404, "Video not found")
    return v


def _check_item_visible(db: Session, kind: str, item_id: int, auth: Auth) -> None:
    """404 for a tag / creator / source this viewer may not see (so not even its name leaks)."""
    if item_id not in visibility.visible_entries(db, kind, _allowed(db, auth)):
        raise HTTPException(404, f"{kind.capitalize()} not found")


# ---- feed / videos / ratings ------------------------------------------------------------------

@router.get("/feed")
def feed(type: str = "main", id: int | None = None, unrated_only: bool = False,
         limit: int = Query(10, ge=1, le=50), exclude: str | None = None,
         auth: Auth = Depends(get_auth), db: Session = Depends(get_db)):
    if type not in FEED_TYPES:
        raise HTTPException(400, "Unknown feed type")
    if type in ("category", "collection") and not auth.user:
        raise HTTPException(401, "Login required")
    if type not in ("main",) and id is None:
        raise HTTPException(400, "id required")
    if type in ("tag", "creator", "source"):
        _check_item_visible(db, type, id, auth)
    allowed = _allowed(db, auth)
    videos = get_feed(db, auth.user_id, type, id, unrated_only, limit, _parse_ids(exclude), allowed)
    # Tells the app to ask visitors to log in when they may not see any source at all.
    login_required = auth.user is None and allowed is not None and not allowed
    return {"items": videos_summary(db, videos, auth.user_id), "header": _feed_header(db, type, id, auth),
            "login_required": login_required}


def _feed_header(db: Session, ftype: str, fid: int | None, auth: Auth) -> dict | None:
    if ftype == "main" or fid is None:
        return None
    if ftype == "tag":
        t = db.get(Tag, fid)
        return {"type": "tag", "id": fid, "label": f"#{t.name}" if t else "Tag"}
    if ftype == "creator":
        c = db.get(Creator, fid)
        return {"type": "creator", "id": fid, "label": c.name if c else "Creator",
                "avatar_url": media_url(c.avatar_path) if c else None}
    if ftype == "source":
        s = db.get(Source, fid)
        return {"type": "source", "id": fid, "label": s.name if s else "Source"}
    if ftype == "category":
        c = db.get(Category, fid)
        return {"type": "category", "id": fid, "label": c.name if c else "Category"}
    c = db.get(Collection, fid)
    return {"type": "collection", "id": fid, "label": c.name if c else "Collection",
            "system_type": c.system_type if c else None}


@router.get("/videos/{video_id}")
def get_video(video_id: int, auth: Auth = Depends(get_auth), db: Session = Depends(get_db)):
    return video_detail(db, _visible_video(db, video_id, auth), auth.user_id)


class RatingIn(BaseModel):
    value: int


@router.put("/videos/{video_id}/rating")
def put_rating(video_id: int, body: RatingIn, user: User = Depends(require_user), auth: Auth = Depends(get_auth),
               db: Session = Depends(get_db)):
    if body.value not in (1, -1):
        raise HTTPException(422, "value must be 1 or -1")
    v = _visible_video(db, video_id, auth)
    res = set_rating(db, user.id, v, body.value)
    return {"status": res.status, "value": res.value}


@router.delete("/videos/{video_id}/rating")
def delete_rating(video_id: int, user: User = Depends(require_user), auth: Auth = Depends(get_auth),
                  db: Session = Depends(get_db)):
    v = _visible_video(db, video_id, auth)
    res = clear_rating(db, user.id, v)
    return {"status": res.status, "value": None}


# ---- search -----------------------------------------------------------------------------------

@router.get("/search")
def search(q: str = "", types: str | None = None, limit: int = Query(5, ge=1, le=200),
           auth: Auth = Depends(get_auth), db: Session = Depends(get_db)):
    tlist = [t.strip() for t in types.split(",")] if types else []
    if not q.strip():
        return {"q": q, "groups": {}}
    return library.search(db, auth.user_id, q, tlist, limit, _allowed(db, auth))


@router.get("/search/recent")
def recent_searches(user: User = Depends(require_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(SearchHistory).where(SearchHistory.user_id == user.id)
                      .order_by(SearchHistory.searched_at.desc()).limit(10))
    return {"items": [r.query for r in rows]}


class RecentIn(BaseModel):
    q: str


@router.post("/search/recent")
def add_recent(body: RecentIn, user: User = Depends(require_user), db: Session = Depends(get_db)):
    q = body.q.strip()[:255]
    if q:
        db.execute(delete(SearchHistory).where(SearchHistory.user_id == user.id, SearchHistory.query == q))
        db.add(SearchHistory(user_id=user.id, query=q))
        db.flush()
        keep = select(SearchHistory.id).where(SearchHistory.user_id == user.id).order_by(
            SearchHistory.searched_at.desc()).limit(10)
        db.execute(delete(SearchHistory).where(SearchHistory.user_id == user.id, SearchHistory.id.not_in(keep)))
        db.commit()
    return {"ok": True}


@router.delete("/search/recent")
def clear_recent(user: User = Depends(require_user), db: Session = Depends(get_db)):
    db.execute(delete(SearchHistory).where(SearchHistory.user_id == user.id))
    db.commit()
    return {"ok": True}


# ---- library lists ----------------------------------------------------------------------------

def watched_creator_ids(db: Session) -> set[int] | None:
    from ..models import WatchLink

    items = importer_client.all_watches()
    if items is None:
        return None
    enabled_ids = {w["id"] for w in items if w.get("enabled", True)}
    out: set[int] = set(db.scalars(select(WatchLink.creator_id).where(
        WatchLink.kind == "creator", WatchLink.watch_id.in_(enabled_ids))))
    for w in items:
        dom, handle = w.get("creator_source_domain"), w.get("creator_handle")
        if not dom or not handle or not w.get("enabled", True):
            continue
        src = db.scalar(select(Source).where(Source.domain == dom))
        if not src:
            continue
        c = db.scalar(select(Creator).where(Creator.source_id == src.id, Creator.handle == handle))
        if c is None:
            a = db.get(CreatorAlias, (src.id, handle))
            c = db.get(Creator, a.creator_id) if a else None
        if c:
            out.add(c.id)
    return out


def _list(kind: str):
    def handler(sort: str | None = None, q: str | None = None, page: int = Query(1, ge=1),
                page_size: int = Query(50, ge=1, le=500), watched_only: bool = False,
                auth: Auth = Depends(get_auth), db: Session = Depends(get_db)):
        only = None
        watched = None
        if kind == "creator" and auth.is_admin:
            watched = watched_creator_ids(db)
            if watched_only:
                only = watched or set()
        res = library.list_items(db, auth.user_id, kind, sort, q, page, page_size, only, _allowed(db, auth))
        if watched is not None:
            for it in res["items"]:
                it["watched"] = it["id"] in watched
        return res
    return handler


router.add_api_route("/tags", _list("tag"), methods=["GET"])
router.add_api_route("/creators", _list("creator"), methods=["GET"])
router.add_api_route("/sources", _list("source"), methods=["GET"])


def _item(db: Session, kind: str, item_id: int, auth: Auth) -> dict:
    entry = visibility.visible_entries(db, kind, _allowed(db, auth)).get(item_id)
    if entry is None:
        raise HTTPException(404, f"{kind} not found")
    stats = library.user_stats(db, auth.user_id, kind, [item_id])
    return library.entry_row(entry, stats.get(item_id), bool(auth.user))


@router.get("/tags/{tag_id}")
def get_tag(tag_id: int, auth: Auth = Depends(get_auth), db: Session = Depends(get_db)):
    return _item(db, "tag", tag_id, auth)


@router.get("/sources/{source_id}")
def get_source(source_id: int, auth: Auth = Depends(get_auth), db: Session = Depends(get_db)):
    return _item(db, "source", source_id, auth)


@router.get("/creators/{creator_id}")
def get_creator(creator_id: int, auth: Auth = Depends(get_auth), db: Session = Depends(get_db)):
    row = _item(db, "creator", creator_id, auth)
    c = db.get(Creator, creator_id)
    row["profile_url"] = c.profile_url
    row["source"] = source_dict(c.source)
    if auth.is_admin:
        row["watch"] = creator_watch_status(db, c)
    return row


def creator_watch_status(db: Session, c: Creator) -> dict:
    from ..services.watch_targets import creator_status

    return creator_status(db, c)


# ---- categories -------------------------------------------------------------------------------

class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = ""


class CategoryPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None


class CategoryItemIn(BaseModel):
    item_type: str
    item_id: int


def _own_category(db: Session, cid: int, user: User) -> Category:
    c = db.get(Category, cid)
    if c is None or c.user_id != user.id:
        raise HTTPException(404, "Category not found")
    return c


def _visible_category_items(db: Session, c: Category) -> list[CategoryItem]:
    """Category items the owner can still see (items from sources they lost access to are left out)."""
    items = db.scalars(select(CategoryItem).where(CategoryItem.category_id == c.id)).all()
    allowed = visibility.user_allowed(db, db.get(User, c.user_id))
    if allowed is None:
        return list(items)
    vis = {k: visibility.visible_entries(db, k, allowed) for k in ("tag", "creator", "source")}
    return [i for i in items if i.item_id in vis.get(i.item_type, {})]


def _category_summary(db: Session, c: Category, user_id: int) -> dict:
    items = _visible_category_items(db, c)
    score = 0
    for kind in ("tag", "creator", "source"):
        ids = [i.item_id for i in items if i.item_type == kind]
        if ids:
            st = library.user_stats(db, user_id, kind, ids)
            score += sum(s["score"] for s in st.values())
    return {"id": c.id, "name": c.name, "description": c.description, "item_count": len(items), "score": score,
            "created_at": c.created_at.isoformat() + "Z", "updated_at": c.updated_at.isoformat() + "Z"}


@router.get("/categories")
def list_categories(user: User = Depends(require_user), db: Session = Depends(get_db)):
    cats = db.scalars(select(Category).where(Category.user_id == user.id).order_by(func.lower(Category.name)))
    return {"items": [_category_summary(db, c, user.id) for c in cats]}


@router.post("/categories", status_code=201)
def create_category(body: CategoryIn, user: User = Depends(require_user), db: Session = Depends(get_db)):
    c = Category(user_id=user.id, name=body.name.strip(), description=body.description)
    db.add(c)
    db.commit()
    return _category_summary(db, c, user.id)


@router.get("/categories/{cid}")
def get_category(cid: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    c = _own_category(db, cid, user)
    out = _category_summary(db, c, user.id)
    items = _visible_category_items(db, c)
    allowed = visibility.user_allowed(db, user)
    sections: dict[str, list] = {"tag": [], "creator": [], "source": []}
    for kind in sections:
        ids = {i.item_id: i.id for i in items if i.item_type == kind}
        stats = library.user_stats(db, user.id, kind, list(ids))
        for e in visibility.visible_entries(db, kind, allowed).values():
            if e.id in ids:
                row = library.entry_row(e, stats.get(e.id), True)
                row["category_item_id"] = ids[e.id]
                sections[kind].append(row)
        sections[kind].sort(key=lambda r: r["name"].lower())
    out["items"] = sections
    return out


@router.patch("/categories/{cid}")
def patch_category(cid: int, body: CategoryPatch, user: User = Depends(require_user), db: Session = Depends(get_db)):
    c = _own_category(db, cid, user)
    if body.name is not None:
        c.name = body.name.strip()
    if body.description is not None:
        c.description = body.description
    c.updated_at = utcnow()
    db.commit()
    return _category_summary(db, c, user.id)


@router.delete("/categories/{cid}")
def delete_category(cid: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    c = _own_category(db, cid, user)
    db.delete(c)
    db.commit()
    return {"ok": True}


@router.post("/categories/{cid}/items", status_code=201)
def add_category_item(cid: int, body: CategoryItemIn, user: User = Depends(require_user),
                      db: Session = Depends(get_db)):
    c = _own_category(db, cid, user)
    model = {"tag": Tag, "creator": Creator, "source": Source}.get(body.item_type)
    if model is None or db.get(model, body.item_id) is None:
        raise HTTPException(404, "Item not found")
    if body.item_id not in visibility.visible_entries(db, body.item_type, visibility.user_allowed(db, user)):
        raise HTTPException(404, "Item not found")
    existing = db.scalar(select(CategoryItem).where(CategoryItem.category_id == c.id,
                                                    CategoryItem.item_type == body.item_type,
                                                    CategoryItem.item_id == body.item_id))
    if existing is None:
        existing = CategoryItem(category_id=c.id, item_type=body.item_type, item_id=body.item_id)
        db.add(existing)
        c.updated_at = utcnow()
        db.commit()
    return {"id": existing.id, "item_type": existing.item_type, "item_id": existing.item_id}


@router.delete("/categories/{cid}/items/{item_id}")
def remove_category_item(cid: int, item_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    c = _own_category(db, cid, user)
    db.execute(delete(CategoryItem).where(CategoryItem.category_id == c.id, CategoryItem.id == item_id))
    c.updated_at = utcnow()
    db.commit()
    return {"ok": True}


# ---- collections ------------------------------------------------------------------------------

class CollectionIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class CollectionVideoIn(BaseModel):
    video_id: int


class OrderIn(BaseModel):
    video_ids: list[int]


def _own_collection(db: Session, cid: int, user: User) -> Collection:
    c = db.get(Collection, cid)
    if c is None or c.user_id != user.id:
        raise HTTPException(404, "Collection not found")
    return c


def _collection_order(c: Collection):
    if c.system_type == "liked":
        return (CollectionVideo.added_at.desc(),)
    return (CollectionVideo.position.asc(), CollectionVideo.added_at.asc())


def _collection_filters(db: Session, c: Collection) -> list:
    """Visible videos of a collection, for its owner (hidden videos and sources they can't see are left out)."""
    conds = [CollectionVideo.collection_id == c.id, Video.is_hidden.is_(False)]
    allowed = visibility.user_allowed(db, db.get(User, c.user_id))
    if allowed is not None:
        conds.append(Video.source_id.in_(allowed))
    return conds


def _collection_summary(db: Session, c: Collection) -> dict:
    conds = _collection_filters(db, c)
    count = db.scalar(select(func.count()).select_from(CollectionVideo).join(Video).where(*conds))
    cover = db.scalar(select(Video.poster_path).join(CollectionVideo).where(*conds)
                      .order_by(*_collection_order(c)).limit(1))
    return {"id": c.id, "name": c.name, "system_type": c.system_type, "video_count": count,
            "cover_url": media_url(cover), "created_at": c.created_at.isoformat() + "Z",
            "updated_at": c.updated_at.isoformat() + "Z"}


@router.get("/collections")
def list_collections(video_id: int | None = None, user: User = Depends(require_user), db: Session = Depends(get_db)):
    cols = db.scalars(select(Collection).where(Collection.user_id == user.id)).all()
    rank = {"favorites": 0, "liked": 1}
    cols = sorted(cols, key=lambda c: (rank.get(c.system_type or "", 2), c.name.lower()))
    items = [_collection_summary(db, c) for c in cols]
    if video_id is not None:
        member = set(db.scalars(select(CollectionVideo.collection_id).where(CollectionVideo.video_id == video_id)))
        for it in items:
            it["contains_video"] = it["id"] in member
    return {"items": items}


@router.post("/collections", status_code=201)
def create_collection(body: CollectionIn, user: User = Depends(require_user), db: Session = Depends(get_db)):
    c = Collection(user_id=user.id, name=body.name.strip())
    db.add(c)
    db.commit()
    return _collection_summary(db, c)


@router.get("/collections/{cid}")
def get_collection(cid: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    c = _own_collection(db, cid, user)
    vids = db.scalars(select(Video).join(CollectionVideo).where(*_collection_filters(db, c))
                      .order_by(*_collection_order(c))).unique()
    out = _collection_summary(db, c)
    out["videos"] = videos_summary(db, list(vids), user.id)
    return out


@router.patch("/collections/{cid}")
def rename_collection(cid: int, body: CollectionIn, user: User = Depends(require_user), db: Session = Depends(get_db)):
    c = _own_collection(db, cid, user)
    if c.system_type:
        raise HTTPException(400, "System collections cannot be renamed")
    c.name = body.name.strip()
    c.updated_at = utcnow()
    db.commit()
    return _collection_summary(db, c)


@router.delete("/collections/{cid}")
def delete_collection(cid: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    c = _own_collection(db, cid, user)
    if c.system_type:
        raise HTTPException(400, "System collections cannot be deleted")
    db.delete(c)
    db.commit()
    return {"ok": True}


@router.post("/collections/{cid}/videos", status_code=201)
def add_to_collection(cid: int, body: CollectionVideoIn, user: User = Depends(require_user),
                      db: Session = Depends(get_db)):
    c = _own_collection(db, cid, user)
    if c.system_type == "liked":
        raise HTTPException(400, "Liked is managed automatically; like the video instead")
    v = db.get(Video, body.video_id)
    if v is None or not visibility.can_see_source(visibility.user_allowed(db, user), v.source_id):
        raise HTTPException(404, "Video not found")
    if db.get(CollectionVideo, {"collection_id": c.id, "video_id": body.video_id}) is None:
        pos = (db.scalar(select(func.max(CollectionVideo.position)).where(CollectionVideo.collection_id == c.id))
               or 0) + 1
        db.add(CollectionVideo(collection_id=c.id, video_id=body.video_id, position=pos))
        c.updated_at = utcnow()
        db.commit()
    return {"ok": True}


@router.delete("/collections/{cid}/videos/{video_id}")
def remove_from_collection(cid: int, video_id: int, user: User = Depends(require_user), auth: Auth = Depends(get_auth),
                           db: Session = Depends(get_db)):
    c = _own_collection(db, cid, user)
    if c.system_type == "liked":
        # Removing from Liked clears the rating (DESIGN.md 3)
        v = db.get(Video, video_id)
        if v is not None:
            clear_rating(db, user.id, v)
        return {"ok": True, "rating_cleared": True}
    db.execute(delete(CollectionVideo).where(CollectionVideo.collection_id == c.id,
                                             CollectionVideo.video_id == video_id))
    c.updated_at = utcnow()
    db.commit()
    return {"ok": True}


@router.put("/collections/{cid}/order")
def reorder_collection(cid: int, body: OrderIn, user: User = Depends(require_user), db: Session = Depends(get_db)):
    c = _own_collection(db, cid, user)
    if c.system_type == "liked":
        raise HTTPException(400, "Liked cannot be reordered")
    rows = {r.video_id: r for r in db.scalars(select(CollectionVideo).where(CollectionVideo.collection_id == c.id))}
    pos = 1
    for vid in body.video_ids:
        if vid in rows:
            rows.pop(vid).position = pos
            pos += 1
    for r in sorted(rows.values(), key=lambda r: r.position):
        r.position = pos
        pos += 1
    c.updated_at = utcnow()
    db.commit()
    return {"ok": True}


# convenience: favorites toggle used by the heart button
@router.put("/videos/{video_id}/favorite")
def favorite(video_id: int, user: User = Depends(require_user), auth: Auth = Depends(get_auth),
             db: Session = Depends(get_db)):
    from ..services.users import system_collection

    _visible_video(db, video_id, auth)
    fav = system_collection(db, user.id, "favorites")
    if db.get(CollectionVideo, {"collection_id": fav.id, "video_id": video_id}) is None:
        pos = (db.scalar(select(func.max(CollectionVideo.position)).where(CollectionVideo.collection_id == fav.id))
               or 0) + 1
        db.add(CollectionVideo(collection_id=fav.id, video_id=video_id, position=pos))
    db.commit()
    return {"is_favorite": True}


@router.delete("/videos/{video_id}/favorite")
def unfavorite(video_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    from ..services.users import system_collection

    fav = system_collection(db, user.id, "favorites")
    db.execute(delete(CollectionVideo).where(CollectionVideo.collection_id == fav.id,
                                             CollectionVideo.video_id == video_id))
    db.commit()
    return {"is_favorite": False}


_ = creator_dict  # re-exported for admin router
