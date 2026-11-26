"""Admin API (DESIGN.md 9, 10, 11.8)."""
import os
import re
import time

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import (
    Creator,
    CreatorAlias,
    MergeLog,
    Source,
    Tag,
    TagAlias,
    User,
    Video,
    VideoTag,
)
from ..security import require_admin
from ..services import importer_client, maintenance, merge, search_index
from ..services.ingest import attach_tags
from ..services.ratings import recompute_all, recompute_creators, recompute_sources, recompute_tags
from ..services.resolve import find_tag
from ..services.serialize import creator_dict, media_url, source_dict, video_detail
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

@router.get("/creators")
def admin_creators(q: str = "", db: Session = Depends(get_db)):
    query = select(Creator)
    if q:
        query = query.where(or_(Creator.name.ilike(f"%{q}%"), Creator.handle.ilike(f"%{q}%")))
    counts = dict(db.execute(select(Video.creator_id, func.count()).group_by(Video.creator_id)).all())
    out = []
    for c in db.scalars(query.order_by(func.lower(Creator.name)).limit(500)).unique():
        d = creator_dict(c)
        d["source"] = source_dict(c.source)
        d["video_count"] = counts.get(c.id, 0)
        d["aliases"] = list(db.scalars(select(CreatorAlias.handle).where(CreatorAlias.creator_id == c.id)))
        out.append(d)
    return {"items": out}


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
    status = creator_watch_status(db, c)
    if status["state"] == "offline":
        raise HTTPException(503, "Importer offline")
    watches = status["watches"]
    targets = [w for w in watches if body.watch_id is None or w["id"] == body.watch_id]
    if body.enabled:
        paused = [w for w in targets if not w.get("enabled")]
        if paused:
            for w in paused:
                importer_client.request("PATCH", f"/api/watches/{w['id']}",
                                        json={"enabled": True, "resume_mode": body.resume_mode})
        elif not targets:
            if not c.profile_url:
                raise HTTPException(400, "Creator has no profile URL to watch")
            payload = {"url": c.profile_url, "name": c.name, "tags": body.tags, "filters": body.filters,
                       "initial_mode": body.initial_mode, "enabled": True}
            if body.backfill_count:
                payload["backfill_count"] = body.backfill_count
            importer_client.request("POST", "/api/watches", json=payload)
    else:
        for w in targets:
            if w.get("enabled"):
                importer_client.request("PATCH", f"/api/watches/{w['id']}", json={"enabled": False})
    importer_client.invalidate_watch_cache()
    return creator_watch_status(db, c)


# ---- tags -------------------------------------------------------------------------------------

@router.get("/tags")
def admin_tags(q: str = "", db: Session = Depends(get_db)):
    query = select(Tag)
    if q:
        query = query.where(Tag.name.ilike(f"%{normalize_tag(q)}%"))
    counts = dict(db.execute(select(VideoTag.tag_id, func.count()).group_by(VideoTag.tag_id)).all())
    aliases: dict[int, list[str]] = {}
    for a in db.scalars(select(TagAlias)):
        aliases.setdefault(a.tag_id, []).append(a.alias)
    items = [{"id": t.id, "name": t.name, "video_count": counts.get(t.id, 0), "aliases": sorted(aliases.get(t.id, [])),
              "created_at": t.created_at.isoformat() + "Z"}
             for t in db.scalars(query.order_by(Tag.name).limit(1000))]
    return {"items": items}


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

@router.get("/sources")
def admin_sources(db: Session = Depends(get_db)):
    counts = dict(db.execute(select(Video.source_id, func.count()).group_by(Video.source_id)).all())
    return {"items": [dict(source_dict(s), video_count=counts.get(s.id, 0))
                      for s in db.scalars(select(Source).order_by(Source.name))]}


class SourcePatch(BaseModel):
    name: str


@router.patch("/sources/{source_id}")
def patch_source(source_id: int, body: SourcePatch, db: Session = Depends(get_db)):
    s = db.get(Source, source_id)
    if s is None:
        raise HTTPException(404)
    s.name = body.name.strip() or s.name
    db.commit()
    search_index.invalidate()
    return source_dict(s)


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

