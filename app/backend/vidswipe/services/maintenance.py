"""Maintenance: delete video, rescan media, rebuild / re-read from sidecars (DESIGN.md 9, 11.6)."""
import logging
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import delete, exists, func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import CategoryItem, Creator, CreatorAlias, Source, SourceTag, Tag, Video, VideoTag, WatchLink
from ..routers.media import MEDIA_ROOTS, delete_media_file, prune_empty_dirs, remove_domain_dirs
from . import search_index
from .ingest import attach_tags, ingest, iter_sidecars, payload_from_sidecar, read_sidecar
from .ratings import recompute_all, recompute_creators, recompute_sources, recompute_tags
from .resolve import resolve_creator, upsert_source

log = logging.getLogger(__name__)


def delete_video(db: Session, video: Video) -> None:
    delete_videos(db, [video.id])


def delete_videos(db: Session, video_ids: list[int], commit: bool = True) -> dict:
    """Delete videos, their DB rows (tags, ratings, collection links cascade) and media files.

    Stats for every affected tag, creator and source are recomputed once from the ratings table.
    Files are removed after commit, and only if no remaining video still references them.
    """
    video_ids = list(dict.fromkeys(int(i) for i in video_ids))
    if not video_ids:
        return {"videos_deleted": 0, "files_deleted": 0}
    tag_ids: set[int] = set()
    creator_ids: set[int] = set()
    source_ids: set[int] = set()
    paths: set[str] = set()
    for chunk in _chunks(video_ids):
        tag_ids.update(db.scalars(select(VideoTag.tag_id).where(VideoTag.video_id.in_(chunk))))
        for v in db.execute(select(Video.creator_id, Video.source_id, Video.file_path, Video.poster_path,
                                   Video.info_json_path).where(Video.id.in_(chunk))):
            creator_ids.add(v.creator_id)
            source_ids.add(v.source_id)
            paths.update(p for p in (v.file_path, v.poster_path, v.info_json_path) if p)
    for chunk in _chunks(video_ids):
        db.execute(delete(Video).where(Video.id.in_(chunk)))
    db.flush()
    recompute_tags(db, tag_ids)
    recompute_creators(db, creator_ids)
    recompute_sources(db, source_ids)
    still_used: set[str] = set()
    for col in (Video.file_path, Video.poster_path, Video.info_json_path):
        for chunk in _chunks(sorted(paths)):
            still_used.update(db.scalars(select(col).where(col.in_(chunk))))
    if commit:
        db.commit()
    files = 0
    for p in sorted(paths - still_used):
        try:
            delete_media_file(p)
            files += 1
        except OSError as exc:  # pragma: no cover
            log.warning("could not delete %s: %s", p, exc)
    search_index.invalidate()
    return {"videos_deleted": len(video_ids), "files_deleted": files}


def _chunks(items: list, size: int = 500):
    items = list(items)
    for i in range(0, len(items), size):
        yield items[i:i + size]


# ---- deleting whole sources / creators / tags ---------------------------------------------------

def _delete_category_items(db: Session, item_type: str, ids: list[int]) -> None:
    for chunk in _chunks(ids):
        db.execute(delete(CategoryItem).where(CategoryItem.item_type == item_type, CategoryItem.item_id.in_(chunk)))


def _delete_creators(db: Session, creator_ids: list[int]) -> list[str]:
    """Delete creator rows (no videos may remain). Returns avatar paths to remove."""
    avatars = [p for p in db.scalars(select(Creator.avatar_path).where(Creator.id.in_(creator_ids))) if p]
    _delete_category_items(db, "creator", creator_ids)
    for chunk in _chunks(creator_ids):
        db.execute(delete(CreatorAlias).where(CreatorAlias.creator_id.in_(chunk)))
        db.execute(delete(Creator).where(Creator.id.in_(chunk)))
    return avatars


def _remove_files(paths: list[str]) -> None:
    for p in paths:
        try:
            delete_media_file(p)
        except OSError as exc:  # pragma: no cover
            log.warning("could not delete %s: %s", p, exc)


def delete_preview(db: Session, kind: str, item_id: int) -> dict:
    if kind == "source":
        src = db.get(Source, item_id)
        if src is None:
            raise HTTPException(404, "Source not found")
        n = db.scalar(select(func.count()).select_from(Video).where(Video.source_id == item_id))
        c = db.scalar(select(func.count()).select_from(Creator).where(Creator.source_id == item_id))
        return {"type": kind, "id": item_id, "name": src.name, "videos": n, "creators": c}
    if kind == "creator":
        cr = db.get(Creator, item_id)
        if cr is None:
            raise HTTPException(404, "Creator not found")
        n = db.scalar(select(func.count()).select_from(Video).where(Video.creator_id == item_id))
        return {"type": kind, "id": item_id, "name": cr.name, "videos": n}
    tag = db.get(Tag, item_id)
    if tag is None:
        raise HTTPException(404, "Tag not found")
    n = db.scalar(select(func.count()).select_from(VideoTag).where(VideoTag.tag_id == item_id))
    return {"type": kind, "id": item_id, "name": tag.name, "videos": n}


def delete_source(db: Session, source_id: int) -> dict:
    """Delete a source with all of its videos and creators."""
    src = db.get(Source, source_id)
    if src is None:
        raise HTTPException(404, "Source not found")
    vids = list(db.scalars(select(Video.id).where(Video.source_id == source_id)))
    res = delete_videos(db, vids, commit=False)
    creator_ids = list(db.scalars(select(Creator.id).where(Creator.source_id == source_id)))
    files = _delete_creators(db, creator_ids)
    db.execute(delete(CreatorAlias).where(CreatorAlias.source_id == source_id))
    _delete_category_items(db, "source", [source_id])
    if src.icon_path:
        files.append(src.icon_path)
    domain = src.domain
    db.delete(src)
    db.commit()
    _remove_files(files)
    # Everything under videos/<domain>/, posters/<domain>/, ... belonged to this source:
    # remove those folders, including stray files the database did not know about.
    dirs = remove_domain_dirs(domain)
    search_index.invalidate()
    return {**res, **dirs, "creators_deleted": len(creator_ids), "sources_deleted": 1}


def delete_creator(db: Session, creator_id: int) -> dict:
    """Delete a creator with all of their videos."""
    if db.get(Creator, creator_id) is None:
        raise HTTPException(404, "Creator not found")
    vids = list(db.scalars(select(Video.id).where(Video.creator_id == creator_id)))
    res = delete_videos(db, vids, commit=False)
    files = _delete_creators(db, [creator_id])
    db.commit()
    _remove_files(files)
    search_index.invalidate()
    return {**res, "creators_deleted": 1}


def delete_tag(db: Session, tag_id: int, with_videos: bool) -> dict:
    """Delete a tag. With with_videos, every video carrying the tag is deleted too."""
    tag = db.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(404, "Tag not found")
    res = {"videos_deleted": 0, "files_deleted": 0}
    if with_videos:
        vids = list(db.scalars(select(VideoTag.video_id).where(VideoTag.tag_id == tag_id)))
        res = delete_videos(db, vids, commit=False)
    _delete_category_items(db, "tag", [tag_id])
    db.execute(delete(Tag).where(Tag.id == tag_id))  # aliases and stats cascade
    db.commit()
    search_index.invalidate()
    return {**res, "tags_deleted": 1}


# ---- cleanup of items with no videos ------------------------------------------------------------

def find_empty(db: Session) -> dict:
    """Tags, creators and sources that have no videos (hidden videos count as videos).

    A source counts as empty when it has no videos; its creators are then empty too.
    """
    # tags added to a source by hand or watched on a source are in use even without videos
    tags = db.execute(select(Tag.id, Tag.name).where(
        ~exists().where(VideoTag.tag_id == Tag.id),
        ~exists().where(SourceTag.tag_id == Tag.id),
        ~exists().where(WatchLink.tag_id == Tag.id)).order_by(Tag.name)).all()
    creators = db.execute(select(Creator.id, Creator.name, Creator.handle).where(
        ~exists().where(Video.creator_id == Creator.id)).order_by(Creator.name)).all()
    sources = db.execute(select(Source.id, Source.name, Source.domain).where(
        ~exists().where(Video.source_id == Source.id)).order_by(Source.name)).all()
    return {
        "tags": [{"id": t.id, "name": t.name} for t in tags],
        "creators": [{"id": c.id, "name": c.name, "handle": c.handle} for c in creators],
        "sources": [{"id": s.id, "name": s.name, "domain": s.domain} for s in sources],
    }


def cleanup_empty(db: Session, types: set[str] | None = None) -> dict:
    """Delete tags, creators and sources with zero videos. Returns what was removed."""
    types = types or {"tag", "creator", "source"}
    empty = find_empty(db)
    files: list[str] = []
    if "tag" in types and empty["tags"]:
        ids = [t["id"] for t in empty["tags"]]
        _delete_category_items(db, "tag", ids)
        for chunk in _chunks(ids):
            db.execute(delete(Tag).where(Tag.id.in_(chunk)))
    if "creator" in types and empty["creators"]:
        files += _delete_creators(db, [c["id"] for c in empty["creators"]])
    removed_sources = []
    if "source" in types:
        for s in empty["sources"]:
            # only remove a source once nothing references it
            if db.scalar(select(func.count()).select_from(Creator).where(Creator.source_id == s["id"])):
                continue
            src = db.get(Source, s["id"])
            db.execute(delete(CreatorAlias).where(CreatorAlias.source_id == s["id"]))
            _delete_category_items(db, "source", [s["id"]])
            if src.icon_path:
                files.append(src.icon_path)
            db.delete(src)
            removed_sources.append(s)
            for root in MEDIA_ROOTS:  # only empty folders here; stray files are left alone
                d = get_settings().media_dir / root / s["domain"]
                if s["domain"] and "/" not in s["domain"] and not s["domain"].startswith(".") and d.is_dir():
                    prune_empty_dirs(d)
    db.commit()
    _remove_files(files)
    search_index.invalidate()
    return {
        "tags": empty["tags"] if "tag" in types else [],
        "creators": empty["creators"] if "creator" in types else [],
        "sources": removed_sources,
        "tags_deleted": len(empty["tags"]) if "tag" in types else 0,
        "creators_deleted": len(empty["creators"]) if "creator" in types else 0,
        "sources_deleted": len(removed_sources),
    }


def rescan_media(db: Session) -> dict:
    base: Path = get_settings().media_dir
    on_disk = set()
    vdir = base / "videos"
    if vdir.exists():
        on_disk = {str(p.relative_to(base)).replace("\\", "/") for p in vdir.rglob("*") if p.is_file()}
    in_db = dict(db.execute(select(Video.file_path, Video.id)).all())
    orphans = sorted(on_disk - set(in_db))
    missing = sorted(({"video_id": vid, "file_path": fp} for fp, vid in in_db.items() if fp not in on_disk),
                     key=lambda d: d["video_id"])
    sidecars = set(iter_sidecars())
    importable = []
    for fp in orphans:
        parts = fp.split("/")
        if len(parts) == 3:
            sc = f"metadata/{parts[1]}/{Path(parts[2]).stem}.json"
            if sc in sidecars:
                importable.append(sc)
    return {"files_on_disk": len(on_disk), "videos_in_db": len(in_db), "orphan_files": orphans,
            "missing_files": missing, "orphans_with_sidecar": importable}


def rebuild_from_media(db: Session) -> dict:
    created = existing = failed = 0
    errors = []
    for rel in iter_sidecars():
        try:
            info = read_sidecar(rel)
            payload, imported_at = payload_from_sidecar(info, rel)
            _, was_created = ingest(db, payload, imported_at=imported_at)
            created += was_created
            existing += not was_created
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            failed += 1
            errors.append({"sidecar": rel, "error": str(exc)[:300]})
    recompute_all(db)
    return {"created": created, "existing": existing, "failed": failed, "errors": errors[:50]}


def reread_metadata(db: Session, video: Video) -> dict:
    """Re-apply the 11.3 mapping from the sidecar. Admin-added tags are kept."""
    if not video.info_json_path:
        return {"video_id": video.id, "status": "no_sidecar"}
    info = read_sidecar(video.info_json_path)
    p, _ = payload_from_sidecar(info, video.info_json_path)
    old_tags = set(db.scalars(select(VideoTag.tag_id).where(VideoTag.video_id == video.id)))
    old_creator, old_source = video.creator_id, video.source_id
    source = upsert_source(db, p.source.domain, p.source.name)
    creator = resolve_creator(db, source, p.creator.handle or p.creator.name or "unknown", p.creator.name,
                              p.creator.profile_url, p.creator.avatar_path)
    video.title = p.title
    video.description = p.description or ""
    video.source_url = p.source_url
    video.source_id = source.id
    video.creator_id = creator.id
    f = p.file
    for k in ("size_bytes", "width", "height", "duration_s", "fps", "video_codec", "audio_codec", "has_audio",
              "container"):
        val = getattr(f, k)
        if val is not None:
            setattr(video, k, val)
    if p.poster_path:
        video.poster_path = p.poster_path
    db.execute(delete(VideoTag).where(VideoTag.video_id == video.id, VideoTag.origin != "admin"))
    db.flush()
    attach_tags(db, video, p.tags, "site")
    if not db.scalar(select(VideoTag).where(VideoTag.video_id == video.id).limit(1)):
        attach_tags(db, video, [source.domain], "site")
    new_tags = set(db.scalars(select(VideoTag.tag_id).where(VideoTag.video_id == video.id)))
    recompute_tags(db, old_tags | new_tags)
    recompute_creators(db, {old_creator, creator.id})
    recompute_sources(db, {old_source, source.id})
    db.commit()
    search_index.invalidate()
    return {"video_id": video.id, "status": "ok"}
