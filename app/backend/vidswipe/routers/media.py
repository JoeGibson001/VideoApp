"""Media serving with HTTP Range support and traversal protection (DESIGN.md 10)."""
import mimetypes
import os
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..security import Auth, get_auth
from ..services import visibility

router = APIRouter()

ALLOWED_ROOTS = {"videos", "posters", "avatars", "icons"}
CHUNK = 256 * 1024
_RANGE = re.compile(r"bytes=(\d*)-(\d*)$")


def safe_media_path(rel: str) -> Path:
    base = get_settings().media_dir.resolve()
    parts = rel.split("/")
    if not parts or parts[0] not in ALLOWED_ROOTS or any(p in ("", ".", "..") for p in parts):
        raise HTTPException(404)
    target = (base / rel).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        raise HTTPException(404)
    if not target.is_file():
        raise HTTPException(404)
    return target


def _iter_file(path: Path, start: int, length: int):
    with open(path, "rb") as fh:
        fh.seek(start)
        remaining = length
        while remaining > 0:
            data = fh.read(min(CHUNK, remaining))
            if not data:
                break
            remaining -= len(data)
            yield data


PER_SOURCE_ROOTS = {"videos", "posters", "avatars"}  # laid out as <root>/<source domain>/...


def _check_access(rel: str, auth: Auth, db: Session) -> None:
    """Files of a source the viewer may not see are reported as missing."""
    parts = rel.split("/")
    if parts[0] not in PER_SOURCE_ROOTS or len(parts) < 3:
        return
    allowed = visibility.allowed_for(db, auth.user)
    if allowed is None:
        return
    sid = visibility.source_id_for_domain(db, parts[1])
    if not visibility.can_see_source(allowed, sid):
        raise HTTPException(404)


@router.api_route("/media/{rel:path}", methods=["GET", "HEAD"])
def serve_media(rel: str, request: Request, auth: Auth = Depends(get_auth), db: Session = Depends(get_db)):
    path = safe_media_path(rel)
    _check_access(rel, auth, db)
    size = path.stat().st_size
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    mtime = int(path.stat().st_mtime)
    headers = {
        "Accept-Ranges": "bytes",
        # private: access depends on who is asking, so shared caches must not keep a copy
        "Cache-Control": "private, max-age=86400" if not rel.startswith("avatars/") else "private, max-age=3600",
        "ETag": f'"{mtime:x}-{size:x}"',
    }
    rng = request.headers.get("range")
    if rng:
        m = _RANGE.match(rng.strip())
        if not m or (m.group(1) == "" and m.group(2) == ""):
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        if m.group(1) == "":
            length = min(int(m.group(2)), size)
            start, end = size - length, size - 1
        else:
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else size - 1
            end = min(end, size - 1)
        if start >= size or start > end:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        length = end - start + 1
        headers.update({"Content-Range": f"bytes {start}-{end}/{size}", "Content-Length": str(length)})
        if request.method == "HEAD":
            return Response(status_code=206, headers=headers, media_type=ctype)
        return StreamingResponse(_iter_file(path, start, length), status_code=206, headers=headers, media_type=ctype)
    headers["Content-Length"] = str(size)
    if request.method == "HEAD":
        return Response(status_code=200, headers=headers, media_type=ctype)
    return StreamingResponse(_iter_file(path, 0, size), status_code=200, headers=headers, media_type=ctype)


def delete_media_file(rel: str | None) -> None:
    """Delete one media file, then remove any folders it leaves empty.

    Pruning stops at the top-level folders (videos/, posters/, ...), which always stay.
    """
    if not rel:
        return
    base = get_settings().media_dir.resolve()
    target = (base / rel).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        return
    if target.is_file():
        os.remove(target)
    prune_empty_dirs(target.parent)


def prune_empty_dirs(start: Path) -> int:
    """Remove `start` and its parents while they are empty, never a top-level media folder."""
    base = get_settings().media_dir.resolve()
    removed = 0
    d = start.resolve()
    while True:
        try:
            rel = d.relative_to(base)
        except ValueError:
            break
        if len(rel.parts) < 2:  # media root or a top-level folder such as videos/
            break
        try:
            d.rmdir()  # only succeeds when empty
        except OSError:
            break
        removed += 1
        d = d.parent
    return removed


MEDIA_ROOTS = ("videos", "posters", "avatars", "metadata")


def remove_domain_dirs(domain: str) -> dict:
    """Delete <root>/<domain>/ under each media folder, including any files still in them.

    Used when a whole source is deleted: everything under its domain folders belonged to it.
    """
    base = get_settings().media_dir.resolve()
    out = {"folders_removed": 0, "leftover_files_removed": 0}
    if not domain or "/" in domain or "\\" in domain or domain in (".", "..") or domain.startswith("."):
        return out
    for root in MEDIA_ROOTS:
        d = (base / root / domain).resolve()
        if d.parent != (base / root).resolve() or not d.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(d, topdown=False):
            for f in filenames:
                try:
                    os.remove(os.path.join(dirpath, f))
                    out["leftover_files_removed"] += 1
                except OSError:
                    pass
            for sub in dirnames:
                try:
                    os.rmdir(os.path.join(dirpath, sub))
                except OSError:
                    pass
        try:
            d.rmdir()
            out["folders_removed"] += 1
        except OSError:
            pass
    return out
