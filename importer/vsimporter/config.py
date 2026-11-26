"""Runtime configuration, read from environment variables."""

from __future__ import annotations

from datetime import time
from functools import cached_property
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

IMPORTER_VERSION = "1.0.0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    importer_api_key: str = ""
    ingest_api_key: str = ""
    app_internal_url: str = "http://app:8080"

    import_concurrency: int = 2
    tz: str = "UTC"
    watch_run_time: str = "03:00"
    watch_scan_limit: int = 100
    watch_max_per_run: int = 50
    watch_backfill_max: int = 500
    # Random gap between watches in a scheduled run (seconds).
    watch_gap_min_s: float = 30
    watch_gap_max_s: float = 120

    transcode_incompatible: bool = True
    max_file_mb: int = 4096  # 0 disables the limit
    max_duration_min: int = 240  # 0 disables the limit

    ytdlp_auto_update: bool = False
    ytdlp_cookies_file: str = "/data/cookies.txt"
    ytdlp_bin: str = "yt-dlp"
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"

    data_dir: Path = Path("/data")
    media_dir: Path = Path("/media")

    host: str = "0.0.0.0"
    port: int = 8081
    log_level: str = "INFO"

    # Backoff (seconds) between App ingest attempts.
    ingest_backoff_s: list[float] = [2, 5, 15, 30, 60, 120]

    @field_validator("watch_run_time")
    @classmethod
    def _check_time(cls, v: str) -> str:
        hh, mm = v.strip().split(":")[:2]
        time(int(hh), int(mm))
        return f"{int(hh):02d}:{int(mm):02d}"

    @field_validator("tz")
    @classmethod
    def _check_tz(cls, v: str) -> str:
        v = (v or "UTC").strip() or "UTC"
        ZoneInfo(v)
        return v

    @cached_property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)

    @property
    def run_time(self) -> time:
        hh, mm = self.watch_run_time.split(":")
        return time(int(hh), int(mm))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "jobs.db"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path.as_posix()}"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def cookies_path(self) -> Path | None:
        if not self.ytdlp_cookies_file:
            return None
        p = Path(self.ytdlp_cookies_file)
        return p if p.is_file() else None

    @property
    def max_file_bytes(self) -> int | None:
        return self.max_file_mb * 1024 * 1024 if self.max_file_mb > 0 else None

    @property
    def max_duration_s(self) -> float | None:
        return self.max_duration_min * 60.0 if self.max_duration_min > 0 else None
