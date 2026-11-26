"""Media serving with HTTP Range support and traversal protection (DESIGN.md 10)."""
import mimetypes
import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from ..config import get_settings

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


@router.api_route("/media/{rel:path}", methods=["GET", "HEAD"])
def serve_media(rel: str, request: Request):
    path = safe_media_path(rel)
    size = path.stat().st_size
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    mtime = int(path.stat().st_mtime)
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "public, max-age=86400" if not rel.startswith("avatars/") else "public, max-age=3600",
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
