"""Small shared helpers."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


def iso(dt: datetime | None) -> str | None:
    """Format a naive-UTC (or aware) datetime as ISO 8601 with a Z suffix."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat(timespec="seconds") + "Z"


def to_naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def next_run_after(now: datetime, run_time, zone: ZoneInfo) -> datetime:
    """Next occurrence (strictly after `now`) of the local wall-clock `run_time`, as naive UTC."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local = now.astimezone(zone)
    day: date = local.date()
    for _ in range(3):
        cand = datetime.combine(day, run_time, tzinfo=zone)
        if cand > local:
            return to_naive_utc(cand)
        day = day + timedelta(days=1)
    return to_naive_utc(datetime.combine(day, run_time, tzinfo=zone))


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1, default=str)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def place_file(src: Path, dst: Path) -> None:
    """Move src to dst atomically (copy to a hidden temp name in the target dir, then rename)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(src, dst)
        return
    except OSError:
        pass
    tmp = dst.with_name(f".{dst.name}.part")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)
    try:
        src.unlink()
    except OSError:
        pass


def clean_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for child in path.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child, ignore_errors=True)
        else:
            try:
                child.unlink()
            except OSError:
                pass
