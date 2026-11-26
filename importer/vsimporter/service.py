"""The Importer service object: wires settings, database, adapters, queue, watches and scheduler."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable

from .adapters import Adapters, real_adapters
from .config import Settings
from .db import make_engine, make_sessionmaker, run_migrations
from .jobs import JobQueue
from .util import clean_dir, to_naive_utc
from .watches import Scheduler, WatchService

log = logging.getLogger("vsimporter")


class Importer:
    def __init__(
        self,
        settings: Settings,
        adapters: Adapters | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        gap: Callable[[], float] | None = None,
    ):
        self.settings = settings
        self.adapters = adapters or real_adapters(settings)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.sleep = sleep or asyncio.sleep
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.engine = make_engine(settings.db_url)
        self.Session = make_sessionmaker(self.engine)
        self.jobs = JobQueue(self)
        self.watches = WatchService(self)
        self.scheduler = Scheduler(self, gap=gap)
        self._started = False

    def now(self) -> datetime:
        """Current time as naive UTC (the storage format)."""
        return to_naive_utc(self.clock())

    def init(self) -> None:
        """Run migrations, clean temp files and recover interrupted jobs."""
        for sub in ("videos", "metadata", "posters", "avatars"):
            try:
                (self.settings.media_dir / sub).mkdir(parents=True, exist_ok=True)
            except OSError as e:
                log.warning("cannot create media dir", extra={"dir": sub, "error": str(e)})
        run_migrations(self.settings.db_url)
        clean_dir(self.settings.tmp_dir)
        self.jobs.recover()

    async def start(self, workers: bool = True, scheduler: bool = True) -> None:
        if workers:
            self.jobs.start(self.settings.import_concurrency)
        if scheduler:
            self.scheduler.start()
        self._started = True
        log.info(
            "importer started",
            extra={"concurrency": self.settings.import_concurrency, "tz": self.settings.tz,
                   "watch_run_time": self.settings.watch_run_time},
        )

    async def stop(self) -> None:
        await self.scheduler.stop()
        await self.jobs.stop()
        self.engine.dispose()
