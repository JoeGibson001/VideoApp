"""Test fixtures: fake yt-dlp / ffmpeg / App / HTTP adapters so tests run offline."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from vsimporter.adapters import Adapters, AppError, ToolError
from vsimporter.config import Settings
from vsimporter.service import Importer

API_KEY = "test-importer-key"


def yt_video(vid: str, *, title: str | None = None, ts: int = 1_700_000_000, tags=None, categories=None,
             width: int = 1080, height: int = 1920, duration: float = 42.0, **extra) -> dict:
    info = {
        "id": vid,
        "title": title or f"Video {vid}",
        "description": f"Description of {vid}",
        "webpage_url": f"https://www.youtube.com/watch?v={vid}",
        "extractor_key": "Youtube",
        "extractor": "youtube",
        "uploader": "Some Creator",
        "uploader_id": "@somecreator",
        "uploader_url": "https://www.youtube.com/@somecreator",
        "channel": "Some Creator",
        "channel_id": "UCabc",
        "channel_url": "https://www.youtube.com/channel/UCabc",
        "tags": ["Cars", "  Drift  "] if tags is None else tags,
        "categories": ["Autos & Vehicles"] if categories is None else categories,
        "upload_date": datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y%m%d"),
        "timestamp": ts,
        "duration": duration,
        "width": width,
        "height": height,
        "thumbnail": f"https://i.ytimg.com/vi/{vid}/maxresdefault.jpg",
        "thumbnails": [
            {"id": "0", "url": f"https://i.ytimg.com/vi/{vid}/default.jpg"},
            {"id": "1", "url": f"https://i.ytimg.com/vi/{vid}/maxresdefault.jpg"},
        ],
        "format_id": "137+140",
        "formats": [
            {"format_id": "140", "url": "https://rr1.googlevideo.com/videoplayback?expire=1&sig=x", "ext": "m4a"},
            {"format_id": "137", "url": "https://rr1.googlevideo.com/videoplayback?expire=1&sig=y", "ext": "mp4",
             "http_headers": {"Cookie": "secret"}},
            {"format_id": "248", "url": "https://rr1.googlevideo.com/videoplayback?expire=1", "ext": "webm"},
        ],
        "http_headers": {"User-Agent": "x"},
        "live_status": "not_live",
    }
    info.update(extra)
    return info


class FakeYtDlp:
    def __init__(self):
        self.videos: dict[str, dict] = {}  # url -> info
        self.playlists: dict[str, dict] = {}  # url -> flat listing
        self.errors: dict[str, str] = {}  # url -> stderr
        self.downloads: list[str] = []
        self.probes: list[tuple[str, bool]] = []

    def add_video(self, info: dict, *urls: str) -> dict:
        for u in (info["webpage_url"], *urls):
            self.videos[u] = info
        return info

    def set_playlist(self, url: str, entries: list[dict], **extra) -> None:
        listing = {"_type": "playlist", "id": extra.pop("id", "PL1"), "title": extra.pop("title", "A playlist"),
                   "webpage_url": url, "extractor_key": "YoutubeTab", "entries": entries}
        listing.update(extra)
        self.playlists[url] = listing

    async def version(self) -> str:
        return "2099.01.01"

    async def plugins(self):
        return []

    async def probe(self, url, *, flat=False, playlist_end=None, no_playlist=False):
        self.probes.append((url, flat))
        if url in self.errors:
            raise ToolError("ERROR: " + self.errors[url].splitlines()[-1], self.errors[url])
        if url in self.playlists:
            listing = copy.deepcopy(self.playlists[url])
            if playlist_end:
                listing["entries"] = listing["entries"][:playlist_end]
            return listing
        if url in self.videos:
            return copy.deepcopy(self.videos[url])
        raise ToolError("ERROR: Unsupported URL: " + url, "ERROR: Unsupported URL")

    async def download(self, url, out_dir: Path, progress=None):
        self.downloads.append(url)
        info = self.videos[url]
        out_dir.mkdir(parents=True, exist_ok=True)
        p = out_dir / "video.mp4"
        p.write_bytes(b"\x00" * 2048)
        if progress:
            progress(50.0)
            progress(100.0)
        return p, copy.deepcopy(info)


class FakeMedia:
    def __init__(self):
        self.probe_result = {"width": 1080, "height": 1920, "duration_s": 42.3, "fps": 30.0, "video_codec": "h264",
                             "audio_codec": "aac", "has_audio": True, "container": "mp4"}
        self.source_probe: dict | None = None
        self.converts: list[dict] = []
        self.convert_failures = 0  # fail this many converts with ffmpeg's "end of file" error

    async def ffprobe(self, path: Path) -> dict:
        base = self.source_probe if (self.source_probe and path.name != "final.mp4") else self.probe_result
        r = dict(base)
        r["size_bytes"] = path.stat().st_size
        r["raw"] = {"format": {}, "streams": []}
        return r

    async def convert(self, src, dst, *, video_copy, audio_copy, has_audio):
        self.converts.append({"video_copy": video_copy, "audio_copy": audio_copy})
        if self.convert_failures:
            self.convert_failures -= 1
            raise ToolError("ffmpeg failed (code 187: unexpected end of file, the download is probably incomplete)",
                            "[mov,mp4] stream 0, offset 0x1234: partial file")
        Path(dst).write_bytes(Path(src).read_bytes())

    async def image_to_jpeg(self, src, dst, max_side=None):
        Path(dst).write_bytes(b"\xff\xd8jpeg")

    async def frame_at(self, video, seconds, dst):
        Path(dst).write_bytes(b"\xff\xd8frame")


class FakeApp:
    def __init__(self):
        self.library: dict[tuple[str, str], int] = {}
        self.ingested: list[dict] = []
        self.exists_calls: list[tuple[str, str]] = []
        self.fail_ingest = 0
        self._next = 100

    async def exists(self, domain, video_id):
        self.exists_calls.append((domain, video_id))
        vid = self.library.get((domain, video_id))
        return (vid is not None), vid

    async def ingest(self, payload):
        if self.fail_ingest:
            self.fail_ingest -= 1
            raise AppError("App unreachable")
        self.ingested.append(payload)
        key = (payload["source"]["domain"], payload["source_video_id"])
        if key not in self.library:
            self._next += 1
            self.library[key] = self._next
        return self.library[key]


class FakeFetcher:
    def __init__(self):
        self.fetched: list[str] = []
        self.fail = False

    async def fetch(self, url, dst):
        self.fetched.append(url)
        if self.fail:
            return False
        Path(dst).write_bytes(b"img")
        return True


class FakeClock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        importer_api_key=API_KEY,
        ingest_api_key="ingest",
        data_dir=tmp_path / "data",
        media_dir=tmp_path / "media",
        tz="UTC",
        watch_run_time="03:00",
        ingest_backoff_s=[0, 0],
        ytdlp_cookies_file="",
    )


@pytest.fixture
def fakes():
    return Adapters(ytdlp=FakeYtDlp(), media=FakeMedia(), app=FakeApp(), fetcher=FakeFetcher())


@pytest.fixture
def clock():
    return FakeClock(datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc))


@pytest.fixture
def gaps():
    return []


@pytest.fixture
def svc(settings, fakes, clock, gaps) -> Importer:
    async def fake_sleep(seconds: float) -> None:
        gaps.append(seconds)

    s = Importer(settings, fakes, clock=clock, sleep=fake_sleep, gap=lambda: 45.0)
    s.init()
    yield s
    s.engine.dispose()
