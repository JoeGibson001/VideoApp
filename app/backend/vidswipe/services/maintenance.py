"""Maintenance: delete video, rescan media, rebuild / re-read from sidecars (DESIGN.md 9, 11.6)."""
import logging
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Video, VideoTag
from ..routers.media import delete_media_file
from . import search_index
from .ingest import attach_tags, ingest, iter_sidecars, payload_from_sidecar, read_sidecar
from .ratings import recompute_all, recompute_creators, recompute_sources, recompute_tags
from .resolve import resolve_creator, upsert_source

log = logging.getLogger(__name__)


def delete_video(db: Session, video: Video) -> None:
    tag_ids = list(db.scalars(select(VideoTag.tag_id).where(VideoTag.video_id == video.id)))
    creator_id, source_id = video.creator_id, video.source_id
    paths = [video.file_path, video.poster_path, video.info_json_path]
    db.delete(video)
    db.flush()
    recompute_tags(db, tag_ids)
    recompute_creators(db, [creator_id])
    recompute_sources(db, [source_id])
    db.commit()
    for p in paths:
        try:
            delete_media_file(p)
        except OSError as exc:  # pragma: no cover
            log.warning("could not delete %s: %s", p, exc)
    search_index.invalidate()


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
