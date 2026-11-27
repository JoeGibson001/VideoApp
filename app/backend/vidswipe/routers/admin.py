"""Admin API (DESIGN.md 9, 10, 11.8)."""
import os
import re
import time

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import (
    Creator,
    CreatorAlias,
    MergeLog,
    Source,
    SourceTag,
    Tag,
    TagAlias,
    User,
    Video,
    VideoTag,
    WatchLink,
)
from ..security import require_admin
from ..services import importer_client, maintenance, merge, search_index, urls, watch_targets
from ..services.ingest import attach_tags
from ..services.ratings import recompute_all, recompute_creators, recompute_sources, recompute_tags
from ..services.resolve import find_tag
from ..services.serialize import creator_dict, media_url, source_dict, video_detail
from ..services.library import user_stats
from ..services.text import normalize_tag
from .api import creator_watch_status

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])


def _ids(raw: str) -> list[int]:
    return [int(x) for x in raw.split(",") if x.strip().isdigit()]


# ---- videos -----------------------------------------------------------------------------------

@router.get("/videos")
def list_videos(q: str = "", hidden: bool | None = None, page: int = Query(1, ge=1),
                page_size: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    query = select(Video).join(Creator, Creator.id == Video.creator_id)
    if q:
        like = f"%{q}%"
        query = query.where(or_(Video.title.ilike(like), Creator.name.ilike(like), Creator.handle.ilike(like),
                                Video.source_video_id.ilike(like)))
    if hidden is not None:
        query = query.where(Video.is_hidden.is_(hidden))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    vids = db.scalars(query.order_by(Video.imported_at.desc()).offset((page - 1) * page_size).limit(page_size)).unique()
    items = []
    for v in vids:
        items.append({"id": v.id, "title": v.title, "creator": creator_dict(v.creator), "source": source_dict(v.source),
                      "poster_url": media_url(v.poster_path), "is_hidden": v.is_hidden,
                      "duration_s": v.duration_s, "width": v.width, "height": v.height,
                      "imported_at": v.imported_at.isoformat() + "Z"})
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/videos/{video_id}")
def admin_video(video_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    v = db.get(Video, video_id)
    if v is None:
        raise HTTPException(404)
    d = video_detail(db, v, admin.id)
    d["tag_links"] = [{"tag_id": r.tag_id, "name": db.get(Tag, r.tag_id).name, "origin_name": r.origin_name,
                       "origin": r.origin}
                      for r in db.scalars(select(VideoTag).where(VideoTag.video_id == v.id))]
    return d


class VideoPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    creator_id: int | None = None
    source_id: int | None = None
    tags: list[str] | None = None
    is_hidden: bool | None = None


@router.patch("/videos/{video_id}")
def patch_video(video_id: int, body: VideoPatch, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    v = db.get(Video, video_id)
    if v is None:
        raise HTTPException(404)
    if body.title is not None:
        v.title = body.title
    if body.description is not None:
        v.description = body.description
    old_creator, old_source = v.creator_id, v.source_id
    if body.creator_id is not None and body.creator_id != v.creator_id:
        if db.get(Creator, body.creator_id) is None:
            raise HTTPException(404, "Creator not found")
        v.creator_id = body.creator_id
    if body.source_id is not None and body.source_id != v.source_id:
        if db.get(Source, body.source_id) is None:
            raise HTTPException(404, "Source not found")
        v.source_id = body.source_id
    if body.is_hidden is not None:
        v.is_hidden = body.is_hidden
    db.flush()
    affected_tags: set[int] = set()
    if body.tags is not None:
        current = {r.tag_id: r for r in db.scalars(select(VideoTag).where(VideoTag.video_id == v.id))}
        wanted: set[int] = set()
        new_names = []
        for name in body.tags:
            t, _ = find_tag(db, name)
            if t is not None:
                wanted.add(t.id)
                if t.id not in current:
                    new_names.append(name)
            elif normalize_tag(name):
                new_names.append(name)
        for tid, row in current.items():
            if tid not in wanted:
                db.delete(row)
                affected_tags.add(tid)
        db.flush()
        affected_tags.update(attach_tags(db, v, new_names, "admin"))
    if affected_tags:
        recompute_tags(db, affected_tags)
    if v.creator_id != old_creator:
        recompute_creators(db, {old_creator, v.creator_id})
    if v.source_id != old_source:
        recompute_sources(db, {old_source, v.source_id})
    db.commit()
    search_index.invalidate()
    return admin_video(video_id, admin, db)


@router.delete("/videos/{video_id}")
def delete_video(video_id: int, db: Session = Depends(get_db)):
    v = db.get(Video, video_id)
    if v is None:
        raise HTTPException(404)
    maintenance.delete_video(db, v)
    return {"ok": True}


@router.post("/videos/{video_id}/reread")
def reread_video(video_id: int, db: Session = Depends(get_db)):
    v = db.get(Video, video_id)
    if v is None:
        raise HTTPException(404)
    try:
        return maintenance.reread_metadata(db, v)
    except FileNotFoundError:
        raise HTTPException(404, "Sidecar JSON not found")


# ---- creators ---------------------------------------------------------------------------------

# ---- sorting (same options as the user library; scores are the admin's own) --------------------


def _sorted(db: Session, user: User, kind: str, objs: list, counts: dict, sort: str) -> tuple[list, dict]:
    """Sort Tag/Creator/Source rows like the library does. Returns (objs, the admin's stats by id)."""
    stats = user_stats(db, user.id, kind)

    def name(o):
        return o.name.lower()

    def score(o):
        return stats.get(o.id, {}).get("score", 0)

    def created(o):
        return o.created_at.isoformat() if o.created_at else ""

    def rated(o):
        v = stats.get(o.id, {}).get("last_rated_at")
        return v.isoformat() if v else ""

    keys = {
        "score_desc": (lambda o: (-score(o), -counts.get(o.id, 0), name(o)), False),
        "score_asc": (lambda o: (score(o), -counts.get(o.id, 0), name(o)), False),
        "name_desc": (name, True),
        "newest": (created, True),
        "oldest": (created, False),
        "most_videos": (lambda o: (-counts.get(o.id, 0), name(o)), False),
        "recent": (rated, True),
    }
    key, reverse = keys.get(sort, (name, False))
    return sorted(objs, key=key, reverse=reverse), stats


def _with_stats(d: dict, obj, stats: dict) -> dict:
    st = stats.get(obj.id, {})
    d.update(score=st.get("score", 0), likes=st.get("likes", 0), dislikes=st.get("dislikes", 0),
             last_rated_at=st["last_rated_at"].isoformat() + "Z" if st.get("last_rated_at") else None,
             created_at=obj.created_at.isoformat() + "Z" if obj.created_at else None)
    return d


@router.get("/creators")
def admin_creators(q: str = "", sort: str = "name_asc", db: Session = Depends(get_db),
                   admin: User = Depends(require_admin)):
    query = select(Creator)
    if q:
        query = query.where(or_(Creator.name.ilike(f"%{q}%"), Creator.handle.ilike(f"%{q}%")))
    counts = dict(db.execute(select(Video.creator_id, func.count()).group_by(Video.creator_id)).all())
    creators, stats = _sorted(db, admin, "creator", list(db.scalars(query).unique()), counts, sort)
    out = []
    for c in creators[:500]:
        d = creator_dict(c)
        d["source"] = source_dict(c.source)
        d["video_count"] = counts.get(c.id, 0)
        d["aliases"] = list(db.scalars(select(CreatorAlias.handle).where(CreatorAlias.creator_id == c.id)))
        out.append(_with_stats(d, c, stats))
    return {"items": out, "total": len(creators)}


class CreatorPatch(BaseModel):
    name: str | None = None
    handle: str | None = None
    profile_url: str | None = None


@router.get("/creators/{creator_id}/delete-preview")
def creator_delete_preview(creator_id: int, db: Session = Depends(get_db)):
    return maintenance.delete_preview(db, "creator", creator_id)


@router.delete("/creators/{creator_id}")
def delete_creator(creator_id: int, db: Session = Depends(get_db)):
    """Delete a creator and all of their videos (files included)."""
    res = maintenance.delete_creator(db, creator_id)
    importer_client.invalidate_watch_cache()
    return {"ok": True, **res}


@router.patch("/creators/{creator_id}")
def patch_creator(creator_id: int, body: CreatorPatch, db: Session = Depends(get_db)):
    c = db.get(Creator, creator_id)
    if c is None:
        raise HTTPException(404)
    if body.handle is not None and body.handle != c.handle:
        clash = db.scalar(select(Creator).where(Creator.source_id == c.source_id, Creator.handle == body.handle))
        if clash:
            raise HTTPException(409, f"Handle already used by creator {clash.id}; merge instead")
        c.handle = body.handle
    if body.name is not None:
        c.name = body.name
    if body.profile_url is not None:
        c.profile_url = body.profile_url or None
    db.commit()
    search_index.invalidate()
    importer_client.invalidate_watch_cache()
    return creator_dict(c)


def _save_upload(subdir: str, stem: str, upload: UploadFile) -> str:
    ext = os.path.splitext(upload.filename or "")[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".svg", ".ico"):
        raise HTTPException(400, "Unsupported image type")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "img"
    rel = f"{subdir}/{safe}-{int(time.time())}{ext}"
    dest = get_settings().media_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    data = upload.file.read(5 * 1024 * 1024 + 1)
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(413, "Image too large (max 5 MB)")
    dest.write_bytes(data)
    return rel


@router.post("/creators/{creator_id}/avatar")
def upload_avatar(creator_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    c = db.get(Creator, creator_id)
    if c is None:
        raise HTTPException(404)
    c.avatar_path = _save_upload(f"avatars/{c.source.domain}", c.handle, file)
    db.commit()
    search_index.invalidate()
    return creator_dict(c)


class MergeIn(BaseModel):
    from_ids: list[int]
    into_id: int


@router.get("/creators/merge-preview")
def creator_merge_preview(from_: str = Query(alias="from"), into: int = Query(), db: Session = Depends(get_db)):
    return merge.creator_merge_preview(db, _ids(from_), into)


@router.post("/creators/merge")
def creator_merge(body: MergeIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    log = merge.merge_creators(db, body.from_ids, body.into_id, admin.id)
    importer_client.invalidate_watch_cache()
    return {"merge_id": log.id}


class WatchToggleIn(BaseModel):
    enabled: bool
    watch_id: int | None = None
    initial_mode: str = "new_only"
    backfill_count: int | None = None
    tags: list[str] = []
    filters: dict = {}
    resume_mode: str = "only_new"  # import_missed | only_new


@router.get("/creators/{creator_id}/watch")
def get_creator_watch(creator_id: int, db: Session = Depends(get_db)):
    c = db.get(Creator, creator_id)
    if c is None:
        raise HTTPException(404)
    return creator_watch_status(db, c)


@router.post("/creators/{creator_id}/watch")
def toggle_creator_watch(creator_id: int, body: WatchToggleIn, db: Session = Depends(get_db)):
    """Off->On creates or resumes; On->Off pauses (never deletes). DESIGN.md 11.8."""
    c = db.get(Creator, creator_id)
    if c is None:
        raise HTTPException(404)
    watch_targets.toggle(db, watch_targets.creator_watches(db, c), body, watch_targets.creator_watch_url(c), c.name,
                         [], {"kind": "creator", "creator_id": c.id})
    return creator_watch_status(db, c)


# ---- tags -------------------------------------------------------------------------------------

@router.get("/tags")
def admin_tags(q: str = "", sort: str = "name_asc", db: Session = Depends(get_db),
               admin: User = Depends(require_admin)):
    query = select(Tag)
    if q:
        query = query.where(Tag.name.ilike(f"%{normalize_tag(q)}%"))
    counts = dict(db.execute(select(VideoTag.tag_id, func.count()).group_by(VideoTag.tag_id)).all())
    aliases: dict[int, list[str]] = {}
    for a in db.scalars(select(TagAlias)):
        aliases.setdefault(a.tag_id, []).append(a.alias)
    tags, stats = _sorted(db, admin, "tag", list(db.scalars(query)), counts, sort)
    items = [_with_stats({"id": t.id, "name": t.name, "video_count": counts.get(t.id, 0),
                          "aliases": sorted(aliases.get(t.id, []))}, t, stats)
             for t in tags[:1000]]
    return {"items": items, "total": len(tags)}


@router.get("/tags/{tag_id}/sources")
def tag_sources(tag_id: int, db: Session = Depends(get_db)):
    """Every source with this tag's page URL and watch state, so a tag can be watched from the Tags page.

    `used` sources (the tag is on their videos, was added by hand, or is watched there) come first.
    """
    t = db.get(Tag, tag_id)
    if t is None:
        raise HTTPException(404, "Tag not found")
    counts = dict(db.execute(select(Video.source_id, func.count(func.distinct(Video.id)))
                             .join(VideoTag, VideoTag.video_id == Video.id)
                             .where(VideoTag.tag_id == t.id).group_by(Video.source_id)).all())
    overrides = {st.source_id: st.url for st in db.scalars(select(SourceTag).where(SourceTag.tag_id == t.id))}
    linked = {lk.source_id for lk in db.scalars(select(WatchLink).where(WatchLink.kind == "source_tag",
                                                                         WatchLink.tag_id == t.id))}
    all_w = importer_client.all_watches() if linked else []
    items = []
    for s in db.scalars(select(Source).order_by(Source.name)):
        used = s.id in counts or s.id in overrides or s.id in linked
        item = {"source": source_dict(s), "video_count": counts.get(s.id, 0), "used": used,
                "added": s.id in overrides, "custom_url": overrides.get(s.id),
                "url": overrides.get(s.id) or urls.tag_url(s.tag_url_template, s.domain, t.name)}
        if s.id in linked:
            item["watch"] = watch_targets.summarize(
                None if all_w is None else watch_targets.source_tag_watches(db, s.id, t.id, all_w))
        else:
            item["watch"] = {"state": "offline" if all_w is None else "none", "watches": []}
        items.append(item)
    items.sort(key=lambda i: (not i["used"], -i["video_count"], i["source"]["name"].lower()))
    return {"tag": {"id": t.id, "name": t.name}, "items": items}


@router.get("/tags/resolve")
def resolve_tag_name(name: str, db: Session = Depends(get_db)):
    t, via_alias = find_tag(db, name)
    return {"name": normalize_tag(name), "tag": {"id": t.id, "name": t.name} if t else None, "via_alias": via_alias}


class TagIn(BaseModel):
    name: str


@router.post("/tags", status_code=201)
def create_tag(body: TagIn, db: Session = Depends(get_db)):
    norm = normalize_tag(body.name)
    if not norm:
        raise HTTPException(400, "Empty name")
    t, via_alias = find_tag(db, norm)
    if t:
        msg = f"'{norm}' is merged into '{t.name}'" if via_alias else "Tag exists"
        return {"id": t.id, "name": t.name, "existing": True, "via_alias": via_alias, "message": msg}
    t = Tag(name=norm)
    db.add(t)
    db.commit()
    search_index.invalidate()
    return {"id": t.id, "name": t.name, "existing": False}


@router.get("/tags/duplicates")
def tag_duplicates(db: Session = Depends(get_db)):
    return {"items": merge.duplicate_suggestions(db)}


class DismissIn(BaseModel):
    tag_a: int
    tag_b: int


@router.post("/tags/duplicates/dismiss")
def dismiss_duplicates(body: DismissIn, db: Session = Depends(get_db)):
    merge.dismiss_duplicate(db, body.tag_a, body.tag_b)
    return {"ok": True}


@router.get("/tags/merge-preview")
def tag_merge_preview(from_: str = Query(alias="from"), into: int = Query(), db: Session = Depends(get_db)):
    return merge.tag_merge_preview(db, _ids(from_), into)


@router.post("/tags/merge")
def tag_merge(body: MergeIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    log = merge.merge_tags(db, body.from_ids, body.into_id, admin.id)
    return {"merge_id": log.id}


@router.get("/tags/{tag_id}")
def admin_tag(tag_id: int, db: Session = Depends(get_db)):
    t = db.get(Tag, tag_id)
    if t is None:
        raise HTTPException(404)
    count = db.scalar(select(func.count()).select_from(VideoTag).where(VideoTag.tag_id == t.id))
    aliases = sorted(db.scalars(select(TagAlias.alias).where(TagAlias.tag_id == t.id)))
    return {"id": t.id, "name": t.name, "video_count": count, "aliases": aliases}


@router.patch("/tags/{tag_id}")
def rename_tag(tag_id: int, body: TagIn, db: Session = Depends(get_db)):
    t = db.get(Tag, tag_id)
    if t is None:
        raise HTTPException(404)
    norm = normalize_tag(body.name)
    if not norm:
        raise HTTPException(400, "Empty name")
    if norm == t.name:
        return {"id": t.id, "name": t.name}
    other, via_alias = find_tag(db, norm)
    if other is not None and other.id != t.id:
        # Renaming onto an existing name is a merge (DESIGN.md 9.1)
        raise HTTPException(409, {"message": f"'{norm}' already exists", "merge_into": {"id": other.id,
                                                                                         "name": other.name},
                                  "via_alias": via_alias})
    if other is not None and other.id == t.id and via_alias:
        db.execute(delete(TagAlias).where(TagAlias.alias == norm))
    old = t.name
    t.name = norm
    db.flush()
    if db.get(TagAlias, old) is None:
        db.add(TagAlias(alias=old, tag_id=t.id))
    db.commit()
    search_index.invalidate()
    return {"id": t.id, "name": t.name}


@router.delete("/tags/{tag_id}")
def delete_tag(tag_id: int, delete_videos: bool = False, db: Session = Depends(get_db)):
    """Delete a tag. With delete_videos=true every video carrying the tag is deleted as well."""
    return {"ok": True, **maintenance.delete_tag(db, tag_id, delete_videos)}


@router.get("/tags/{tag_id}/delete-preview")
def tag_delete_preview(tag_id: int, db: Session = Depends(get_db)):
    return maintenance.delete_preview(db, "tag", tag_id)


class AliasIn(BaseModel):
    alias: str


@router.post("/tags/{tag_id}/aliases", status_code=201)
def add_alias(tag_id: int, body: AliasIn, db: Session = Depends(get_db)):
    if db.get(Tag, tag_id) is None:
        raise HTTPException(404)
    merge.add_tag_alias(db, tag_id, body.alias)
    return {"ok": True}


@router.delete("/tags/aliases/{alias}")
def remove_alias(alias: str, split: bool = False, db: Session = Depends(get_db)):
    return merge.remove_tag_alias(db, alias, split)


# ---- sources ----------------------------------------------------------------------------------

def _admin_source(db: Session, s: Source, counts: dict | None = None, creator_counts: dict | None = None) -> dict:
    if counts is None:
        counts = {s.id: db.scalar(select(func.count()).select_from(Video).where(Video.source_id == s.id))}
    if creator_counts is None:
        creator_counts = {s.id: db.scalar(select(func.count()).select_from(Creator).where(Creator.source_id == s.id))}
    d = source_dict(s)
    d.update(video_count=counts.get(s.id, 0), creator_count=creator_counts.get(s.id, 0),
             creator_url_template=s.creator_url_template, tag_url_template=s.tag_url_template,
             example_creator_url=urls.creator_url(s.creator_url_template, s.domain, "@example", "Example"),
             example_tag_url=urls.tag_url(s.tag_url_template, s.domain, "example tag"))
    return d


def _source_or_404(db: Session, source_id: int) -> Source:
    s = db.get(Source, source_id)
    if s is None:
        raise HTTPException(404, "Source not found")
    return s


@router.get("/sources")
def admin_sources(sort: str = "name_asc", db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    counts = dict(db.execute(select(Video.source_id, func.count()).group_by(Video.source_id)).all())
    ccounts = dict(db.execute(select(Creator.source_id, func.count()).group_by(Creator.source_id)).all())
    sources, stats = _sorted(db, admin, "source", list(db.scalars(select(Source))), counts, sort)
    return {"items": [_with_stats(_admin_source(db, s, counts, ccounts), s, stats) for s in sources]}


@router.get("/sources/{source_id}")
def admin_source(source_id: int, db: Session = Depends(get_db)):
    return _admin_source(db, _source_or_404(db, source_id))


class SourcePatch(BaseModel):
    name: str | None = None
    creator_url_template: str | None = None  # "" clears
    tag_url_template: str | None = None  # "" clears


@router.patch("/sources/{source_id}")
def patch_source(source_id: int, body: SourcePatch, db: Session = Depends(get_db)):
    s = _source_or_404(db, source_id)
    if body.name is not None and body.name.strip():
        s.name = body.name.strip()
    creators_updated = 0
    if body.creator_url_template is not None:
        old = s.creator_url_template
        new = urls.validate_template(body.creator_url_template, urls.CREATOR_KEYS, s.domain, "creator")
        if new != old:
            s.creator_url_template = new
            creators_updated = _retemplate_creators(db, s, old, new)
    if body.tag_url_template is not None:
        s.tag_url_template = urls.validate_template(body.tag_url_template, urls.TAG_KEYS, s.domain, "tag")
    db.commit()
    search_index.invalidate()
    importer_client.invalidate_watch_cache()
    return {**_admin_source(db, s), "creators_updated": creators_updated}


def _retemplate_creators(db: Session, s: Source, old: str | None, new: str | None) -> int:
    """A changed creator URL template is applied to every existing creator of the source.

    When the template is cleared, URLs that the old template produced are removed; URLs that
    came from anywhere else are kept.
    """
    n = 0
    for c in db.scalars(select(Creator).where(Creator.source_id == s.id)).unique():
        if new:
            url = urls.creator_url(new, s.domain, c.handle, c.name)
        else:
            was = urls.creator_url(old, s.domain, c.handle, c.name)
            url = None if c.profile_url and c.profile_url == was else c.profile_url
        if url != c.profile_url:
            c.profile_url = url
            n += 1
    return n


def _creator_url_changes(db: Session, s: Source, overwrite: bool) -> list[tuple[Creator, str]]:
    if not s.creator_url_template:
        raise HTTPException(400, "Set a creator URL template on this source first")
    out = []
    for c in db.scalars(select(Creator).where(Creator.source_id == s.id).order_by(func.lower(Creator.name))).unique():
        if c.profile_url and not overwrite:
            continue
        new = urls.creator_url(s.creator_url_template, s.domain, c.handle, c.name)
        if new and new != c.profile_url:
            out.append((c, new))
    return out


@router.get("/sources/{source_id}/creator-urls/preview")
def preview_creator_urls(source_id: int, overwrite: bool = False, db: Session = Depends(get_db)):
    """What applying the creator URL template would change (missing URLs only, or all with overwrite)."""
    s = _source_or_404(db, source_id)
    changes = _creator_url_changes(db, s, overwrite)
    missing = db.scalar(select(func.count()).select_from(Creator).where(
        Creator.source_id == s.id, or_(Creator.profile_url.is_(None), Creator.profile_url == "")))
    return {"would_change": len(changes), "missing_url": missing,
            "examples": [{"id": c.id, "name": c.name, "handle": c.handle, "old": c.profile_url, "new": new}
                         for c, new in changes[:10]]}


class ApplyUrlsIn(BaseModel):
    overwrite: bool = False


@router.post("/sources/{source_id}/creator-urls/apply")
def apply_creator_urls(source_id: int, body: ApplyUrlsIn, db: Session = Depends(get_db)):
    s = _source_or_404(db, source_id)
    changes = _creator_url_changes(db, s, body.overwrite)
    for c, new in changes:
        c.profile_url = new
    db.commit()
    importer_client.invalidate_watch_cache()
    return {"updated": len(changes)}


@router.get("/sources/{source_id}/tags")
def source_tags(source_id: int, q: str = "", sort: str = "videos", page: int = Query(1, ge=1),
                page_size: int = Query(100, ge=1, le=500), db: Session = Depends(get_db)):
    """Every tag used by videos from this source (plus tags with a custom URL or watch here)."""
    s = _source_or_404(db, source_id)
    counts = dict(db.execute(select(VideoTag.tag_id, func.count(func.distinct(Video.id)))
                             .join(Video, Video.id == VideoTag.video_id)
                             .where(Video.source_id == s.id).group_by(VideoTag.tag_id)).all())
    overrides = {st.tag_id: st.url for st in db.scalars(select(SourceTag).where(SourceTag.source_id == s.id))}
    links = list(db.scalars(select(WatchLink).where(WatchLink.kind == "source_tag", WatchLink.source_id == s.id)))
    linked_tags = {lk.tag_id for lk in links}
    ids = set(counts) | set(overrides) | linked_tags  # used by videos, added by hand, or watched
    tags = list(db.scalars(select(Tag).where(Tag.id.in_(ids)))) if ids else []
    if q.strip():
        nq = normalize_tag(q)
        tags = [t for t in tags if nq in t.name]
    if sort == "name":
        tags.sort(key=lambda t: t.name)
    else:
        tags.sort(key=lambda t: (-counts.get(t.id, 0), t.name))
    total = len(tags)
    chunk = tags[(page - 1) * page_size: page * page_size]
    all_w = importer_client.all_watches() if linked_tags else []
    items = []
    for t in chunk:
        custom = overrides.get(t.id)
        item = {"id": t.id, "name": t.name, "video_count": counts.get(t.id, 0), "custom_url": custom,
                "added": t.id in overrides,
                "url": custom or urls.tag_url(s.tag_url_template, s.domain, t.name)}
        if t.id in linked_tags:
            item["watch"] = watch_targets.summarize(
                None if all_w is None else watch_targets.source_tag_watches(db, s.id, t.id, all_w))
        else:
            item["watch"] = {"state": "offline" if all_w is None else "none", "watches": []}
        items.append(item)
    return {"items": items, "total": total, "page": page, "page_size": page_size,
            "tag_url_template": s.tag_url_template}


class SourceTagUrlIn(BaseModel):
    url: str | None = None  # null or "" clears the override (falls back to the template)


@router.put("/sources/{source_id}/tags/{tag_id}")
def set_source_tag_url(source_id: int, tag_id: int, body: SourceTagUrlIn, db: Session = Depends(get_db)):
    s = _source_or_404(db, source_id)
    t = db.get(Tag, tag_id)
    if t is None:
        raise HTTPException(404, "Tag not found")
    url = urls.validate_url(body.url)
    st = db.get(SourceTag, {"source_id": s.id, "tag_id": t.id})
    if st is None:
        if url:
            db.add(SourceTag(source_id=s.id, tag_id=t.id, url=url))
    else:
        st.url = url  # clearing keeps the row, so a manually added tag stays on the source
    db.commit()
    return {"id": t.id, "name": t.name, "custom_url": url, "url": watch_targets.source_tag_url(db, s, t)}


class AddSourceTagIn(BaseModel):
    name: str
    url: str | None = None


@router.post("/sources/{source_id}/tags", status_code=201)
def add_source_tag(source_id: int, body: AddSourceTagIn, db: Session = Depends(get_db)):
    """Add a tag to a source by hand (e.g. to watch its tag page before any video has it).

    The name goes through the normal resolver, so a merged name maps to the surviving tag.
    """
    s = _source_or_404(db, source_id)
    norm = normalize_tag(body.name)
    if not norm:
        raise HTTPException(400, "Enter a tag name")
    url = urls.validate_url(body.url)
    existing, via_alias = find_tag(db, norm)
    from ..services.resolve import resolve_tag

    t = existing or resolve_tag(db, norm)
    st = db.get(SourceTag, {"source_id": s.id, "tag_id": t.id})
    already = st is not None
    if st is None:
        db.add(SourceTag(source_id=s.id, tag_id=t.id, url=url))
    elif url:
        st.url = url
    db.commit()
    search_index.invalidate()
    msg = f"'{norm}' is merged into '{t.name}'" if via_alias else None
    return {"id": t.id, "name": t.name, "created_tag": existing is None, "already_on_source": already,
            "via_alias": via_alias, "message": msg, "url": watch_targets.source_tag_url(db, s, t)}


@router.delete("/sources/{source_id}/tags/{tag_id}")
def remove_source_tag(source_id: int, tag_id: int, db: Session = Depends(get_db)):
    """Remove a hand-added tag (and its custom URL) from a source. Videos and the tag itself are kept;
    the tag still shows on the source while its videos or a watch use it."""
    s = _source_or_404(db, source_id)
    db.execute(delete(SourceTag).where(SourceTag.source_id == s.id, SourceTag.tag_id == tag_id))
    db.commit()
    return {"ok": True}


def _source_tag(db: Session, source_id: int, tag_id: int) -> tuple[Source, Tag]:
    s = _source_or_404(db, source_id)
    t = db.get(Tag, tag_id)
    if t is None:
        raise HTTPException(404, "Tag not found")
    return s, t


@router.get("/sources/{source_id}/tags/{tag_id}/watch")
def get_source_tag_watch(source_id: int, tag_id: int, db: Session = Depends(get_db)):
    s, t = _source_tag(db, source_id, tag_id)
    out = watch_targets.summarize(watch_targets.source_tag_watches(db, s.id, t.id))
    out["url"] = watch_targets.source_tag_url(db, s, t)
    return out


@router.post("/sources/{source_id}/tags/{tag_id}/watch")
def toggle_source_tag_watch(source_id: int, tag_id: int, body: WatchToggleIn, db: Session = Depends(get_db)):
    """Watch a tag page on a source: same Off->On create/resume and On->Off pause rules as creators.

    Imports from the watch get the tag itself by default, since the site may not tag them.
    """
    s, t = _source_tag(db, source_id, tag_id)
    watch_targets.toggle(db, watch_targets.source_tag_watches(db, s.id, t.id), body,
                         watch_targets.source_tag_url(db, s, t), f"{s.name}: #{t.name}", [t.name],
                         {"kind": "source_tag", "source_id": s.id, "tag_id": t.id})
    return get_source_tag_watch(source_id, tag_id, db)


@router.get("/sources/{source_id}/delete-preview")
def source_delete_preview(source_id: int, db: Session = Depends(get_db)):
    return maintenance.delete_preview(db, "source", source_id)


@router.delete("/sources/{source_id}")
def delete_source(source_id: int, db: Session = Depends(get_db)):
    """Delete a source with all of its videos and creators (files included)."""
    res = maintenance.delete_source(db, source_id)
    importer_client.invalidate_watch_cache()
    return {"ok": True, **res}


@router.post("/sources/{source_id}/icon")
def upload_icon(source_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    s = db.get(Source, source_id)
    if s is None:
        raise HTTPException(404)
    s.icon_path = _save_upload("icons", s.domain, file)
    db.commit()
    search_index.invalidate()
    return source_dict(s)


# ---- merges -----------------------------------------------------------------------------------

@router.get("/merges")
def merges(db: Session = Depends(get_db)):
    out = []
    for m in db.scalars(select(MergeLog).order_by(MergeLog.id.desc()).limit(100)):
        model = Tag if m.entity_type == "tag" else Creator
        into = db.get(model, m.into_id)
        out.append({"id": m.id, "entity_type": m.entity_type, "from_ids": m.from_ids, "from_names": m.from_names,
                    "into_id": m.into_id, "into_name": into.name if into else None,
                    "merged_at": m.merged_at.isoformat() + "Z",
                    "undone_at": m.undone_at.isoformat() + "Z" if m.undone_at else None})
    return {"items": out}


@router.post("/merges/{merge_id}/undo")
def undo(merge_id: int, db: Session = Depends(get_db)):
    merge.undo_merge(db, merge_id)
    importer_client.invalidate_watch_cache()
    return {"ok": True}


# ---- import / watches proxy -------------------------------------------------------------------

class ImportIn(BaseModel):
    urls: list[str]
    tags: list[str] = []
    expand_playlists: bool = False
    max_items: int | None = None


@router.post("/import")
def submit_import(body: ImportIn):
    urls = [u.strip() for u in body.urls if u.strip()]
    if not urls:
        raise HTTPException(400, "No URLs")
    if body.expand_playlists:
        ids = []
        for u in urls:
            payload = {"url": u, "tags": body.tags, "expand_playlists": True}
            if body.max_items:
                payload["max_items"] = body.max_items
            r = importer_client.request("POST", "/api/imports", json=payload)
            ids.append(r.get("job_id"))
        return {"job_ids": ids}
    return importer_client.request("POST", "/api/imports/batch", json={"urls": urls, "tags": body.tags})


@router.get("/imports")
def list_imports(status: str | None = None, limit: int = 50):
    params = {"limit": limit}
    if status:
        params["status"] = status
    return importer_client.request("GET", "/api/imports", params=params)


@router.post("/imports/{job_id}/retry")
def retry_import(job_id: int):
    return importer_client.request("POST", f"/api/imports/{job_id}/retry")


@router.delete("/imports/{job_id}/record")
def delete_import_record(job_id: int):
    """Remove a finished job from the import history (imported videos are kept)."""
    return importer_client.request("DELETE", f"/api/imports/{job_id}/record")


class ClearImportsIn(BaseModel):
    ids: list[int] | None = None
    statuses: list[str] | None = None
    older_than_days: int | None = None


@router.post("/imports/clear")
def clear_imports(body: ClearImportsIn):
    """Bulk-remove finished jobs from the import history."""
    return importer_client.request("POST", "/api/imports/clear", json=body.model_dump(exclude_none=True))


@router.delete("/imports/{job_id}")
def cancel_import(job_id: int):
    return importer_client.request("DELETE", f"/api/imports/{job_id}")


@router.get("/importer/health")
def importer_health():
    try:
        return {"online": True, **(importer_client.request("GET", "/api/health") or {})}
    except HTTPException as exc:
        return {"online": False, "error": str(exc.detail)}


@router.api_route("/watches", methods=["GET", "POST"])
@router.api_route("/watches/{rest:path}", methods=["GET", "POST", "PATCH", "DELETE"])
async def watches_proxy(request_: Request, rest: str = ""):
    body = None
    if request_.method in ("POST", "PATCH"):
        raw = await request_.body()
        body = raw or None
    path = "/api/watches" + (f"/{rest}" if rest else "")
    params = dict(request_.query_params)
    from starlette.concurrency import run_in_threadpool

    result = await run_in_threadpool(importer_client.request, request_.method, path, params=params,
                                     content=body, headers={"Content-Type": "application/json"} if body else None)
    importer_client.invalidate_watch_cache()
    return result


# ---- maintenance ------------------------------------------------------------------------------

@router.get("/cleanup")
def cleanup_preview(db: Session = Depends(get_db)):
    """List tags, creators and sources that have no videos."""
    return maintenance.find_empty(db)


class CleanupIn(BaseModel):
    types: list[str] = ["tag", "creator", "source"]


@router.post("/cleanup")
def cleanup(body: CleanupIn | None = None, db: Session = Depends(get_db)):
    """Delete tags, creators and sources that have no videos."""
    types = set(body.types if body else ["tag", "creator", "source"]) & {"tag", "creator", "source"}
    return maintenance.cleanup_empty(db, types)


WIPE_PHRASE = "DELETE ALL"


class WipeIn(BaseModel):
    confirm: str
    include_user_data: bool = False  # also the user's own collections, categories, recent searches
    clear_importer: bool = True  # cancel active imports, delete all watches and the import history


@router.get("/wipe-all/preview")
def wipe_preview(db: Session = Depends(get_db)):
    q = lambda t: db.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar()  # noqa: E731
    return {"videos": q("videos"), "creators": q("creators"), "sources": q("sources"), "tags": q("tags"),
            "ratings": q("ratings"), "collections": q("collections WHERE system_type IS NULL"),
            "categories": q("categories"), "phrase": WIPE_PHRASE}


@router.post("/wipe-all")
def wipe_all(body: WipeIn, db: Session = Depends(get_db)):
    """Delete all library content and media files (for testing). Requires confirm == "DELETE ALL"."""
    if body.confirm.strip() != WIPE_PHRASE:
        raise HTTPException(400, f'Type "{WIPE_PHRASE}" to confirm')
    importer: dict = {"cleared": False}
    if body.clear_importer:
        # Stop queued/running imports first so nothing is ingested after the wipe.
        try:
            cancelled = 0
            for status in ("queued", "running"):
                jobs = importer_client.request("GET", "/api/imports", params={"status": status, "limit": 1000}) or []
                for j in jobs if isinstance(jobs, list) else jobs.get("items", []):
                    importer_client.request("DELETE", f"/api/imports/{j['id']}", allow=(404, 409), timeout=30)
                    cancelled += 1
            watches = importer_client.all_watches() or []
            for w in watches:
                importer_client.request("DELETE", f"/api/watches/{w['id']}", allow=(404,))
            hist = importer_client.request("POST", "/api/imports/clear", json={}) or {}
            importer = {"cleared": True, "jobs_cancelled": cancelled, "watches_deleted": len(watches),
                        "history_deleted": hist.get("deleted", 0)}
        except HTTPException as exc:
            importer = {"cleared": False, "error": str(exc.detail)}
        importer_client.invalidate_watch_cache()
    res = maintenance.wipe_all_content(db, body.include_user_data)
    return {**res, "importer": importer}


@router.post("/recompute-stats")
def recompute_stats(db: Session = Depends(get_db)):
    recompute_all(db)
    return {"ok": True}


@router.post("/rescan-media")
def rescan_media(db: Session = Depends(get_db)):
    return maintenance.rescan_media(db)


@router.post("/rebuild-from-media")
def rebuild_from_media(db: Session = Depends(get_db)):
    return maintenance.rebuild_from_media(db)


@router.post("/reread-all")
def reread_all(db: Session = Depends(get_db)):
    ok = failed = 0
    for v in db.scalars(select(Video)).unique().all():
        try:
            maintenance.reread_metadata(db, v)
            ok += 1
        except Exception:  # noqa: BLE001
            db.rollback()
            failed += 1
    return {"ok": ok, "failed": failed}

