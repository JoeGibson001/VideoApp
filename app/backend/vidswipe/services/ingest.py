"""Ingest of importer results (DESIGN.md 11.5) and sidecar mapping (11.3, 11.6)."""
import json
import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Video, VideoTag
from . import search_index
from .resolve import resolve_creator, resolve_tag, upsert_source
from .text import normalize_tag

log = logging.getLogger(__name__)


class SourceIn(BaseModel):
    domain: str
    name: str | None = None


class CreatorIn(BaseModel):
    name: str | None = None
    handle: str | None = None
    profile_url: str | None = None
    avatar_path: str | None = None


class FileIn(BaseModel):
    path: str
    original_filename: str | None = None
    size_bytes: int | None = None
    width: int | None = None
    height: int | None = None
    duration_s: float | None = None
    fps: float | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    has_audio: bool = True
    container: str | None = None


class IngestPayload(BaseModel):
    source: SourceIn
    source_video_id: str
    source_url: str | None = None
    creator: CreatorIn = Field(default_factory=CreatorIn)
    title: str = ""
    description: str | None = ""
    tags: list[str] = Field(default_factory=list)
    request_tags: list[str] = Field(default_factory=list)
    upload_date: str | None = None
    file: FileIn
    poster_path: str | None = None
    info_json_path: str | None = None


def _parse_date(val: str | None) -> date | None:
    if not val:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(val[:10] if "-" in val else val[:8], fmt).date()
        except ValueError:
            continue
    return None


def attach_tags(db: Session, video: Video, names: list[str], origin: str) -> list[int]:
    """Resolve names through aliases and attach. Returns tag ids that were newly attached."""
    added = []
    for raw in names:
        if not normalize_tag(raw):
            continue
        tag = resolve_tag(db, raw)
        if tag is None:
            continue
        if db.get(VideoTag, {"video_id": video.id, "tag_id": tag.id}):
            continue
        db.add(VideoTag(video_id=video.id, tag_id=tag.id, origin_name=raw.strip(), origin=origin))
        db.flush()
        added.append(tag.id)
    return added


def ingest(db: Session, p: IngestPayload, imported_at: datetime | None = None) -> tuple[Video, bool]:
    source = upsert_source(db, p.source.domain, p.source.name)
    existing = db.scalar(select(Video).where(Video.source_id == source.id,
                                             Video.source_video_id == p.source_video_id))
    if existing:
        return existing, False
    handle = (p.creator.handle or p.creator.name or "unknown").strip()
    creator = resolve_creator(db, source, handle, p.creator.name, p.creator.profile_url, p.creator.avatar_path)
    f = p.file
    video = Video(
        title=p.title or "",
        description=p.description or "",
        info_json_path=p.info_json_path,
        creator_id=creator.id,
        creator_origin_handle=handle,
        source_id=source.id,
        source_url=p.source_url,
        source_video_id=p.source_video_id,
        file_path=f.path.lstrip("/"),
        filename=os.path.basename(f.path),
        original_filename=f.original_filename,
        poster_path=p.poster_path.lstrip("/") if p.poster_path else None,
        size_bytes=f.size_bytes,
        width=f.width,
        height=f.height,
        duration_s=f.duration_s,
        fps=f.fps,
        video_codec=f.video_codec,
        audio_codec=f.audio_codec,
        has_audio=f.has_audio,
        container=f.container,
        upload_date=_parse_date(p.upload_date),
    )
    if imported_at:
        video.imported_at = imported_at
    db.add(video)
    db.flush()
    req = {normalize_tag(t) for t in p.request_tags}
    site_tags = [t for t in p.tags if normalize_tag(t) not in req]
    attach_tags(db, video, site_tags, "site")
    attach_tags(db, video, p.request_tags, "import_request")
    if not db.scalar(select(VideoTag).where(VideoTag.video_id == video.id).limit(1)):
        attach_tags(db, video, [source.domain], "site")
    db.commit()
    search_index.invalidate()
    return video, True


# ---- sidecar mapping (11.3) -------------------------------------------------------------------

def _first(info: dict, *keys):
    for k in keys:
        v = info.get(k)
        if v not in (None, "", []):
            return v
    return None


def _prettify_extractor(key: str | None, domain: str) -> str:
    if not key:
        return domain
    known = {"youtube": "YouTube", "youtubetab": "YouTube", "tiktok": "TikTok", "vimeo": "Vimeo",
             "instagram": "Instagram", "twitter": "X", "reddit": "Reddit", "facebook": "Facebook",
             "dailymotion": "Dailymotion", "twitch": "Twitch"}
    k = key.lower()
    for prefix, name in known.items():
        if k.startswith(prefix):
            return name
    return key


def payload_from_sidecar(info: dict, info_json_path: str) -> tuple[IngestPayload, datetime | None]:
    imp = info.get("_import") or {}
    url = _first(info, "webpage_url", "original_url") or ""
    domain = (imp.get("source") or {}).get("domain") or urlparse(url).netloc.lower().removeprefix("www.")
    source_name = (imp.get("source") or {}).get("name") or _prettify_extractor(info.get("extractor_key"), domain)
    creator = imp.get("creator") or {
        "name": _first(info, "uploader", "channel", "creator", "uploader_id"),
        "handle": _first(info, "uploader_id", "channel_id", "uploader"),
        "profile_url": _first(info, "uploader_url", "channel_url"),
        "avatar_path": None,
    }
    request_tags = list(imp.get("request_tags") or [])
    tags = list(info.get("tags") or []) + list(info.get("categories") or []) + request_tags
    upload = info.get("upload_date")
    if upload and len(str(upload)) == 8:
        upload = f"{upload[:4]}-{upload[4:6]}-{upload[6:8]}"
    elif not upload and info.get("timestamp"):
        upload = datetime.fromtimestamp(info["timestamp"], tz=timezone.utc).date().isoformat()
    file_info = imp.get("file") or {}
    if not file_info.get("path"):
        file_info["path"] = f"videos/{domain}/{info.get('id')}.mp4"
    imported_at = None
    if imp.get("imported_at"):
        try:
            imported_at = datetime.fromisoformat(imp["imported_at"].replace("Z", "+00:00")).astimezone(
                timezone.utc).replace(tzinfo=None)
        except ValueError:
            imported_at = None
    payload = IngestPayload(
        source=SourceIn(domain=domain, name=source_name),
        source_video_id=str(info.get("id")),
        source_url=url or None,
        creator=CreatorIn(**{k: creator.get(k) for k in ("name", "handle", "profile_url", "avatar_path")}),
        title=info.get("title") or "",
        description=info.get("description") or "",
        tags=tags,
        request_tags=request_tags,
        upload_date=upload,
        file=FileIn(**file_info),
        poster_path=imp.get("poster_path"),
        info_json_path=imp.get("info_json_path") or info_json_path,
    )
    return payload, imported_at


def read_sidecar(rel_path: str) -> dict:
    base = get_settings().media_dir
    with open(base / rel_path, encoding="utf-8") as fh:
        return json.load(fh)


def iter_sidecars() -> list[str]:
    base: Path = get_settings().media_dir / "metadata"
    if not base.exists():
        return []
    return sorted(str(p.relative_to(get_settings().media_dir)).replace("\\", "/") for p in base.rglob("*.json"))
