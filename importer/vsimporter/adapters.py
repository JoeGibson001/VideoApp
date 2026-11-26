"""Adapters around external tools (yt-dlp, ffmpeg/ffprobe) and services (App, HTTP).

Tests replace these with fakes, so the pipeline and watch logic run offline.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Protocol

import httpx

from .config import Settings

log = logging.getLogger("vsimporter.adapters")

FORMAT = "bv*[vcodec^=avc1][ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b"
ProgressCb = Callable[[float], Awaitable[None] | None]


class ToolError(Exception):
    """An external tool failed. `tail` holds the last lines of its stderr."""

    def __init__(self, message: str, tail: str | None = None):
        super().__init__(message)
        self.message = message
        self.tail = tail


class AppError(Exception):
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


# ---------------------------------------------------------------- interfaces


class YtDlp(Protocol):
    async def version(self) -> str: ...

    async def probe(self, url: str, *, flat: bool = False, playlist_end: int | None = None,
                    no_playlist: bool = False) -> dict: ...

    async def download(self, url: str, out_dir: Path, progress: ProgressCb | None = None) -> tuple[Path, dict | None]: ...


class Media(Protocol):
    async def ffprobe(self, path: Path) -> dict: ...

    async def convert(self, src: Path, dst: Path, *, video_copy: bool, audio_copy: bool, has_audio: bool) -> None: ...

    async def image_to_jpeg(self, src: Path, dst: Path, max_side: int | None = None) -> None: ...

    async def frame_at(self, video: Path, seconds: float, dst: Path) -> None: ...


class AppClient(Protocol):
    async def exists(self, domain: str, video_id: str) -> tuple[bool, int | None]: ...

    async def ingest(self, payload: dict) -> int: ...


class Fetcher(Protocol):
    async def fetch(self, url: str, dst: Path) -> bool: ...


@dataclass
class Adapters:
    ytdlp: YtDlp
    media: Media
    app: AppClient
    fetcher: Fetcher


# ---------------------------------------------------------------- subprocess helper


async def run_process(
    args: list[str],
    *,
    on_stdout_line: Callable[[str], Awaitable[None] | None] | None = None,
    timeout: float | None = None,
    tail_lines: int = 30,
) -> tuple[int, str, str]:
    """Run a process, returning (returncode, stdout, stderr_tail). Kills it on cancel/timeout."""
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=64 * 1024 * 1024
    )
    out_chunks: list[str] = []
    err_tail: deque[str] = deque(maxlen=tail_lines)

    async def read_stdout() -> None:
        assert proc.stdout
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", "replace")
            if on_stdout_line:
                r = on_stdout_line(text.rstrip("\r\n"))
                if asyncio.iscoroutine(r):
                    await r
            else:
                out_chunks.append(text)

    async def read_stderr() -> None:
        assert proc.stderr
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            err_tail.append(line.decode("utf-8", "replace").rstrip("\r\n"))

    try:
        await asyncio.wait_for(asyncio.gather(read_stdout(), read_stderr(), proc.wait()), timeout)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        raise
    return proc.returncode or 0, "".join(out_chunks), "\n".join(err_tail)


# ---------------------------------------------------------------- real implementations

_PCT = re.compile(r"\[download\]\s+(\d+(?:\.\d+)?)%")


class RealYtDlp:
    def __init__(self, settings: Settings):
        self.s = settings
        self._version: str | None = None

    def _base(self) -> list[str]:
        args = [self.s.ytdlp_bin, "--no-warnings", "--ignore-config", "--no-cache-dir"]
        cookies = self.s.cookies_path
        if cookies:
            args += ["--cookies", str(cookies)]
        return args

    async def version(self) -> str:
        if self._version is None:
            try:
                code, out, _ = await run_process([self.s.ytdlp_bin, "--version"], timeout=60)
                self._version = out.strip() if code == 0 else "unknown"
            except Exception:  # noqa: BLE001
                self._version = "unknown"
        return self._version

    async def probe(self, url: str, *, flat: bool = False, playlist_end: int | None = None,
                    no_playlist: bool = False) -> dict:
        args = self._base() + ["--dump-single-json", "--no-download"]
        if flat:
            args.append("--flat-playlist")
        if playlist_end:
            args += ["--playlist-end", str(playlist_end)]
        if no_playlist:
            args.append("--no-playlist")
        args += ["--", url]
        code, out, err = await run_process(args, timeout=600)
        if code != 0 or not out.strip():
            from .mapping import last_error_line

            raise ToolError(last_error_line(err) or f"yt-dlp exited with code {code}", err)
        return json.loads(out)

    async def download(self, url: str, out_dir: Path, progress: ProgressCb | None = None) -> tuple[Path, dict | None]:
        out_dir.mkdir(parents=True, exist_ok=True)
        args = self._base() + [
            "-f", FORMAT,
            "--merge-output-format", "mp4",
            "--no-playlist",
            "--newline",
            "--no-mtime",
            "--write-info-json",
            "-o", str(out_dir / "video.%(ext)s"),
        ]
        if self.s.max_file_bytes:
            args += ["--max-filesize", str(self.s.max_file_bytes)]
        args += ["--", url]

        async def on_line(line: str) -> None:
            m = _PCT.search(line)
            if m and progress:
                r = progress(float(m.group(1)))
                if asyncio.iscoroutine(r):
                    await r

        code, _, err = await run_process(args, on_stdout_line=on_line, timeout=6 * 3600)
        if code != 0:
            from .mapping import last_error_line

            raise ToolError(last_error_line(err) or f"yt-dlp exited with code {code}", err)
        files = [
            p for p in out_dir.iterdir()
            if p.name.startswith("video.") and not p.name.endswith((".json", ".part", ".ytdl", ".temp"))
        ]
        if not files:
            if self.s.max_file_bytes:
                raise ToolError(f"Download produced no file (larger than MAX_FILE_MB={self.s.max_file_mb}?)", err)
            raise ToolError("Download produced no file", err)
        info = None
        info_path = out_dir / "video.info.json"
        if info_path.exists():
            try:
                info = json.loads(info_path.read_text("utf-8"))
            except ValueError:
                info = None
        return max(files, key=lambda p: p.stat().st_size), info


def _fps(rate: str | None) -> float | None:
    if not rate or rate in ("0/0", "0"):
        return None
    try:
        if "/" in rate:
            n, d = rate.split("/")
            return round(float(n) / float(d), 3) if float(d) else None
        return round(float(rate), 3)
    except ValueError:
        return None


def parse_ffprobe(data: dict, size_bytes: int | None = None) -> dict:
    """Turn raw ffprobe JSON into the technical fields used by the payload."""
    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    v = next((s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    width = height = None
    rotation = 0
    if v:
        width, height = v.get("width"), v.get("height")
        try:
            rotation = int(float((v.get("tags") or {}).get("rotate", 0)))
        except ValueError:
            rotation = 0
        for sd in v.get("side_data_list") or []:
            if "rotation" in sd:
                try:
                    rotation = int(float(sd["rotation"]))
                except (TypeError, ValueError):
                    pass
        if abs(rotation) % 180 == 90 and width and height:
            width, height = height, width
    duration = None
    for cand in (fmt.get("duration"), (v or {}).get("duration")):
        try:
            if cand is not None:
                duration = round(float(cand), 3)
                break
        except ValueError:
            pass
    fps = _fps((v or {}).get("avg_frame_rate")) or _fps((v or {}).get("r_frame_rate"))
    format_name = fmt.get("format_name") or ""
    if size_bytes is None:
        try:
            size_bytes = int(fmt.get("size")) if fmt.get("size") else None
        except ValueError:
            size_bytes = None
    return {
        "width": width,
        "height": height,
        "duration_s": duration,
        "fps": fps,
        "video_codec": (v or {}).get("codec_name"),
        "audio_codec": (a or {}).get("codec_name"),
        "has_audio": a is not None,
        "container": "mp4" if "mp4" in format_name else (format_name.split(",")[0] or None),
        "size_bytes": size_bytes,
        "raw_format_name": format_name,
    }


class RealMedia:
    def __init__(self, settings: Settings):
        self.s = settings

    async def _ffmpeg(self, args: list[str], timeout: float = 6 * 3600) -> None:
        code, _, err = await run_process([self.s.ffmpeg_bin, "-hide_banner", "-nostdin", "-y", *args], timeout=timeout)
        if code != 0:
            raise ToolError(f"ffmpeg failed (code {code})", err)

    async def ffprobe(self, path: Path) -> dict:
        code, out, err = await run_process(
            [self.s.ffprobe_bin, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            timeout=300,
        )
        if code != 0:
            raise ToolError("ffprobe failed", err)
        raw = json.loads(out or "{}")
        result = parse_ffprobe(raw, path.stat().st_size)
        fmt = {k: v for k, v in (raw.get("format") or {}).items() if k != "filename"}
        result["raw"] = {"format": fmt, "streams": raw.get("streams")}
        return result

    async def convert(self, src: Path, dst: Path, *, video_copy: bool, audio_copy: bool, has_audio: bool) -> None:
        args = ["-i", str(src), "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn"]
        args += ["-c:v", "copy"] if video_copy else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p"]
        if has_audio:
            args += ["-c:a", "copy"] if audio_copy else ["-c:a", "aac", "-b:a", "160k"]
        args += ["-movflags", "+faststart", "-f", "mp4", str(dst)]
        await self._ffmpeg(args)

    async def image_to_jpeg(self, src: Path, dst: Path, max_side: int | None = None) -> None:
        vf = []
        if max_side:
            vf = ["-vf", f"scale='min({max_side},iw)':'min({max_side},ih)':force_original_aspect_ratio=decrease"]
        await self._ffmpeg(["-i", str(src), *vf, "-frames:v", "1", "-q:v", "2", str(dst)], timeout=120)

    async def frame_at(self, video: Path, seconds: float, dst: Path) -> None:
        await self._ffmpeg(["-ss", f"{max(seconds, 0):.3f}", "-i", str(video), "-frames:v", "1", "-q:v", "2", str(dst)], timeout=300)


class RealAppClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.s = settings
        self.client = client or httpx.AsyncClient(base_url=settings.app_internal_url.rstrip("/"), timeout=60)

    def _headers(self) -> dict[str, str]:
        return {"X-Ingest-Key": self.s.ingest_api_key}

    async def exists(self, domain: str, video_id: str) -> tuple[bool, int | None]:
        last: Exception | None = None
        for delay in (0, 1, 3, 8):
            if delay:
                await asyncio.sleep(delay)
            try:
                r = await self.client.get(
                    "/api/internal/exists",
                    params={"source_domain": domain, "source_video_id": video_id},
                    headers=self._headers(),
                )
            except httpx.HTTPError as e:
                last = e
                continue
            if r.status_code >= 500:
                last = AppError(f"App exists check returned {r.status_code}")
                continue
            if r.status_code != 200:
                raise AppError(f"App exists check returned {r.status_code}: {r.text[:300]}", retryable=False)
            data = r.json()
            return bool(data.get("exists")), data.get("video_id")
        raise AppError(f"App unreachable for exists check: {last}")

    async def ingest(self, payload: dict) -> int:
        try:
            r = await self.client.post("/api/internal/ingest", json=payload, headers=self._headers())
        except httpx.HTTPError as e:
            raise AppError(f"App unreachable: {e}") from e
        if r.status_code >= 500 or r.status_code == 429:
            raise AppError(f"App ingest returned {r.status_code}: {r.text[:300]}")
        if r.status_code >= 400:
            raise AppError(f"App ingest rejected ({r.status_code}): {r.text[:500]}", retryable=False)
        return int(r.json()["video_id"])


class RealFetcher:
    MAX_BYTES = 20 * 1024 * 1024

    def __init__(self, client: httpx.AsyncClient | None = None):
        self.client = client or httpx.AsyncClient(
            timeout=30, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (VidSwipe importer)"}
        )

    async def fetch(self, url: str, dst: Path) -> bool:
        try:
            async with self.client.stream("GET", url) as r:
                if r.status_code != 200:
                    return False
                total = 0
                with open(dst, "wb") as fh:
                    async for chunk in r.aiter_bytes():
                        total += len(chunk)
                        if total > self.MAX_BYTES:
                            return False
                        fh.write(chunk)
            return total > 0
        except (httpx.HTTPError, OSError) as e:
            log.info("fetch failed", extra={"url": url, "error": str(e)})
            return False


def real_adapters(settings: Settings) -> Adapters:
    return Adapters(
        ytdlp=RealYtDlp(settings),
        media=RealMedia(settings),
        app=RealAppClient(settings),
        fetcher=RealFetcher(),
    )
