"""Job queue, worker pool and the import pipeline (DESIGN 11.2)."""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import mapping as m
from .adapters import AppError, ToolError
from .config import IMPORTER_VERSION
from .models import Job, Watch
from .util import iso, place_file, write_json_atomic

if TYPE_CHECKING:
    from .service import Importer

log = logging.getLogger("vsimporter.jobs")

FINAL = ("done", "failed", "skipped")


class JobFailed(Exception):
    def __init__(self, message: str, outcome: str = "error", tail: str | None = None):
        super().__init__(message)
        self.message, self.outcome, self.tail = message, outcome, tail


class JobSkipped(Exception):
    def __init__(self, outcome: str, detail: str, video_id: int | None = None):
        super().__init__(detail)
        self.outcome, self.detail, self.video_id = outcome, detail, video_id


class Conflict(Exception):
    pass


def job_to_dict(job: Job, watch_name: str | None = None) -> dict[str, Any]:
    return {
        "id": job.id,
        "url": job.url,
        "status": job.status,
        "progress": job.progress,
        "error": job.error,
        "detail": job.detail,
        "outcome": job.outcome,
        "stderr_tail": job.stderr_tail,
        "video_id": job.video_id,
        "tags": list(job.tags or []),
        "expand_playlists": job.expand_playlists,
        "max_items": job.max_items,
        "parent_id": job.parent_id,
        "watch_id": job.watch_id,
        "watch_name": watch_name,
        "source_domain": job.source_domain,
        "source_video_id": job.source_video_id,
        "title": job.title,
        "attempts": job.attempts,
        "created_at": iso(job.created_at),
        "started_at": iso(job.started_at),
        "finished_at": iso(job.finished_at),
    }


class JobQueue:
    def __init__(self, svc: "Importer"):
        self.svc = svc
        self.pipeline = Pipeline(svc)
        self._event = asyncio.Event()
        self._tasks: dict[int, asyncio.Task] = {}
        self._workers: list[asyncio.Task] = []
        self.shutting_down = False

    # ------------------------------------------------------------ crud

    def create(
        self,
        s: Session,
        url: str,
        *,
        tags: list[str] | None = None,
        expand_playlists: bool = False,
        max_items: int = 50,
        watch_id: int | None = None,
        watch_item_id: str | None = None,
        options: dict | None = None,
        parent_id: int | None = None,
        priority: int = 10,
    ) -> Job:
        job = Job(
            url=url.strip(),
            status="queued",
            priority=priority,
            progress=0,
            tags=m.normalize_tags(tags),
            expand_playlists=expand_playlists,
            max_items=max_items,
            watch_id=watch_id,
            watch_item_id=watch_item_id,
            options=options or {},
            parent_id=parent_id,
            attempts=0,
            created_at=self.svc.now(),
        )
        s.add(job)
        s.flush()
        self.notify()
        return job

    def serialize(self, s: Session, jobs: list[Job]) -> list[dict]:
        ids = {j.watch_id for j in jobs if j.watch_id}
        names: dict[int, str | None] = {}
        if ids:
            names = dict(s.execute(select(Watch.id, Watch.name).where(Watch.id.in_(ids))).all())
        return [job_to_dict(j, names.get(j.watch_id) if j.watch_id else None) for j in jobs]

    def notify(self) -> None:
        self._event.set()

    def counts(self) -> dict[str, int]:
        with self.svc.Session() as s:
            return dict(s.execute(select(Job.status, func.count()).group_by(Job.status)).all())

    # ------------------------------------------------------------ lifecycle

    def recover(self) -> None:
        """On startup, jobs left running by a crash go back to the queue."""
        with self.svc.Session() as s:
            for job in s.scalars(select(Job).where(Job.status == "running")):
                job.status, job.progress, job.started_at = "queued", 0, None
            s.commit()

    def start(self, n: int) -> None:
        for i in range(max(1, n)):
            self._workers.append(asyncio.create_task(self._worker(i), name=f"worker-{i}"))

    async def stop(self) -> None:
        self.shutting_down = True
        for t in self._workers:
            t.cancel()
        for t in list(self._tasks.values()):
            t.cancel()
        await asyncio.gather(*self._workers, *self._tasks.values(), return_exceptions=True)
        self._workers.clear()

    async def _worker(self, n: int) -> None:
        while True:
            self._event.clear()
            job_id = self.claim()
            if job_id is None:
                try:
                    await asyncio.wait_for(self._event.wait(), 5)
                except asyncio.TimeoutError:
                    pass
                continue
            await self.execute(job_id)

    def claim(self) -> int | None:
        with self.svc.Session() as s:
            job = s.scalars(
                select(Job).where(Job.status == "queued").order_by(Job.priority.desc(), Job.id).limit(1)
            ).first()
            if job is None:
                return None
            job.status, job.started_at, job.progress = "running", self.svc.now(), 0
            job.finished_at = None
            job.attempts = (job.attempts or 0) + 1
            s.commit()
            return job.id

    async def execute(self, job_id: int) -> None:
        task = asyncio.create_task(self.pipeline.run(job_id), name=f"job-{job_id}")
        self._tasks[job_id] = task
        try:
            await asyncio.wait({task})
        finally:
            self._tasks.pop(job_id, None)
        if not task.cancelled() and task.exception() is not None:
            log.error("job task crashed", extra={"job_id": job_id, "error": repr(task.exception())})

    async def run_until_idle(self, max_jobs: int = 10_000) -> int:
        """Process queued jobs one by one until none remain (used by tests)."""
        n = 0
        while n < max_jobs:
            job_id = self.claim()
            if job_id is None:
                return n
            await self.execute(job_id)
            n += 1
        return n

    # ------------------------------------------------------------ actions

    async def cancel(self, job_id: int) -> Job | None:
        with self.svc.Session() as s:
            job = s.get(Job, job_id)
            if job is None:
                return None
            status = job.status
        if status == "queued":
            self.pipeline.finish(job_id, "failed", "cancelled", error="Cancelled")
        elif status == "running":
            task = self._tasks.get(job_id)
            if task is not None:
                task.cancel()
                await asyncio.wait({task}, timeout=15)
            self.pipeline.finish(job_id, "failed", "cancelled", error="Cancelled")
        else:
            raise Conflict(f"Job is already {status}")
        with self.svc.Session() as s:
            return s.get(Job, job_id)

    FINISHED = ("done", "failed", "skipped")

    def delete_records(self, job_ids: list[int] | None = None, statuses: list[str] | None = None,
                       older_than_days: int | None = None, strict: bool = False) -> list[int]:
        """Remove finished jobs from the history. Queued or running jobs are never touched.

        Only the job rows go: imported videos stay in the App, and watch items keep their status
        (that is what stops a watch re-importing), with their job link cleared.
        With strict, asking to delete an unfinished job raises Conflict.
        """
        from .models import WatchItem

        statuses = [st for st in (statuses or self.FINISHED) if st in self.FINISHED]
        with self.svc.Session() as s:
            q = select(Job).where(Job.status.in_(statuses))
            if job_ids is not None:
                if strict:
                    for job in s.scalars(select(Job).where(Job.id.in_(job_ids))):
                        if job.status not in self.FINISHED:
                            raise Conflict(f"Job {job.id} is {job.status}; cancel it first")
                q = q.where(Job.id.in_(job_ids))
            if older_than_days is not None:
                from datetime import timedelta

                cutoff = self.svc.now() - timedelta(days=older_than_days)
                q = q.where(func.coalesce(Job.finished_at, Job.created_at) < cutoff)
            ids = list(s.scalars(q.with_only_columns(Job.id)))
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                for item in s.scalars(select(WatchItem).where(WatchItem.job_id.in_(chunk))):
                    item.job_id = None
                for job in s.scalars(select(Job).where(Job.parent_id.in_(chunk))):
                    job.parent_id = None
                s.execute(Job.__table__.delete().where(Job.id.in_(chunk)))
            s.commit()
        if ids:
            log.info("job records deleted", extra={"count": len(ids)})
        return ids

    def retry(self, job_id: int) -> Job | None:
        with self.svc.Session() as s:
            job = s.get(Job, job_id)
            if job is None:
                return None
            if job.status not in ("failed", "skipped"):
                raise Conflict(f"Only failed or skipped jobs can be retried (job is {job.status})")
            job.status, job.progress = "queued", 0
            job.error = job.detail = job.stderr_tail = job.outcome = None
            job.started_at = job.finished_at = None
            job.video_id = None
            self.svc.watches.on_job_requeued(s, job)
            s.commit()
        self.notify()
        with self.svc.Session() as s:
            return s.get(Job, job_id)


class Pipeline:
    def __init__(self, svc: "Importer"):
        self.svc = svc
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def s(self):
        return self.svc.settings

    # ------------------------------------------------------------ db helpers

    def _update(self, job_id: int, **fields: Any) -> None:
        with self.svc.Session() as s:
            job = s.get(Job, job_id)
            if job is None:
                return
            for k, v in fields.items():
                setattr(job, k, v)
            s.commit()

    def _progress(self, job_id: int, pct: float) -> None:
        self._update(job_id, progress=max(0, min(100, int(pct))))

    def finish(
        self,
        job_id: int,
        status: str,
        outcome: str,
        *,
        error: str | None = None,
        detail: str | None = None,
        video_id: int | None = None,
        tail: str | None = None,
    ) -> None:
        with self.svc.Session() as s:
            job = s.get(Job, job_id)
            if job is None or job.status in FINAL:
                return
            job.status, job.outcome = status, outcome
            job.error, job.detail, job.stderr_tail = error, detail, tail
            if video_id is not None:
                job.video_id = video_id
            if status == "done":
                job.progress = 100
            job.finished_at = self.svc.now()
            self.svc.watches.on_job_finished(s, job)
            s.commit()
        log.info(
            "job finished",
            extra={"job_id": job_id, "status": status, "outcome": outcome, "error": error, "video_id": video_id},
        )

    # ------------------------------------------------------------ run

    async def run(self, job_id: int) -> None:
        tmp = self.s.tmp_dir / f"job-{job_id}"
        try:
            await self._run(job_id, tmp)
        except asyncio.CancelledError:
            if self.svc.jobs.shutting_down:
                self._update(job_id, status="queued", progress=0, started_at=None)
            else:
                self.finish(job_id, "failed", "cancelled", error="Cancelled")
            raise
        except JobSkipped as e:
            self.finish(job_id, "skipped", e.outcome, detail=e.detail, video_id=e.video_id)
        except JobFailed as e:
            self.finish(job_id, "failed", e.outcome, error=e.message, tail=e.tail)
        except ToolError as e:
            self.finish(job_id, "failed", "error", error=e.message, tail=e.tail)
        except AppError as e:
            self.finish(job_id, "failed", "app_error", error=str(e))
        except Exception as e:  # noqa: BLE001
            log.exception("job crashed", extra={"job_id": job_id})
            self.finish(job_id, "failed", "error", error=f"{type(e).__name__}: {e}")
        finally:
            await asyncio.to_thread(shutil.rmtree, tmp, True)

    async def _ytdlp(self, coro):
        try:
            return await coro
        except ToolError as e:
            kind = m.classify_error(f"{e.message}\n{e.tail or ''}")
            raise JobFailed(f"yt-dlp: {e.message}", outcome=kind, tail=e.tail) from e

    def _lock(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = self._locks[key] = asyncio.Lock()
        return lock

    async def _run(self, job_id: int, tmp: Path) -> None:
        with self.svc.Session() as s:
            job = s.get(Job, job_id)
            if job is None:
                return
            url, tags, options = job.url, list(job.tags or []), dict(job.options or {})
            expand, max_items, payload = job.expand_playlists, job.max_items, job.payload
            watch_id = job.watch_id

        # A retry after a failed ingest: files are already in place, just ingest again.
        if payload and (self.s.media_dir / payload["file"]["path"]).exists():
            await self._ingest(job_id, payload)
            return

        y = self.svc.adapters.ytdlp
        self._progress(job_id, 1)
        if expand:
            info = await self._ytdlp(y.probe(url, flat=True, playlist_end=max_items))
            if info.get("_type") == "playlist" or info.get("entries") is not None:
                n = self._expand(job_id, info)
                self.finish(job_id, "done", "expanded", detail=f"Expanded into {n} job(s)")
                return
        else:
            info = await self._ytdlp(y.probe(url, no_playlist=True))
            if info.get("_type") == "playlist":
                entries = [e for e in info.get("entries") or [] if isinstance(e, dict)]
                if len(entries) == 1 and entries[0].get("formats"):
                    info = entries[0]
                else:
                    raise JobFailed("URL is a playlist or profile; set expand_playlists=true to import its videos")
        if info.get("_type") == "url" and info.get("url"):
            info = await self._ytdlp(y.probe(info["url"], no_playlist=True))

        if m.is_live(info):
            raise JobFailed("Live streams and upcoming premieres cannot be imported until they have finished", "live")
        if not info.get("id"):
            raise JobFailed("yt-dlp returned no video id")

        source = m.map_source(info)
        domain, vid = source["domain"], str(info["id"])
        self._update(job_id, source_domain=domain, source_video_id=vid, title=info.get("title"), progress=3)

        duration = info.get("duration")
        if self.s.max_duration_s and isinstance(duration, (int, float)) and duration > self.s.max_duration_s:
            raise JobFailed(
                f"Video is {duration / 60:.1f} min long, over the limit MAX_DURATION_MIN={self.s.max_duration_min}",
                "limit",
            )
        approx = info.get("filesize") or info.get("filesize_approx")
        if self.s.max_file_bytes and isinstance(approx, (int, float)) and approx > self.s.max_file_bytes * 1.1:
            raise JobFailed(
                f"Video is about {approx / 1048576:.0f} MB, over the limit MAX_FILE_MB={self.s.max_file_mb}", "limit"
            )
        filters = options.get("filters") or {}
        reason = m.filter_reason(filters, info)
        if reason:
            raise JobSkipped("filtered", f"Filtered: {reason}")

        async with self._lock(f"{domain}/{vid}"):
            exists, existing_id = await self.svc.adapters.app.exists(domain, vid)
            if exists:
                raise JobSkipped("exists", "Already in library", existing_id)
            await self._download_and_ingest(job_id, tmp, url, info, tags, options, watch_id)

    def _expand(self, job_id: int, info: dict) -> int:
        with self.svc.Session() as s:
            parent = s.get(Job, job_id)
            assert parent is not None
            entries = [e for e in info.get("entries") or [] if isinstance(e, dict)][: parent.max_items]
            n = 0
            for e in entries:
                u = m.entry_url(e)
                if not u:
                    continue
                self.svc.jobs.create(
                    s,
                    u,
                    tags=parent.tags,
                    expand_playlists=m.entry_is_playlist(e),
                    max_items=parent.max_items,
                    parent_id=parent.id,
                    priority=parent.priority,
                    watch_id=None,
                )
                n += 1
            s.commit()
        return n

    async def _download_and_ingest(
        self, job_id: int, tmp: Path, url: str, info: dict, tags: list[str], options: dict, watch_id: int | None
    ) -> None:
        a = self.svc.adapters
        tmp.mkdir(parents=True, exist_ok=True)
        last = {"p": -1}

        def on_progress(pct: float) -> None:
            p = 5 + int(pct * 0.75)
            if p != last["p"]:
                last["p"] = p
                self._progress(job_id, p)

        # A download that yt-dlp reports as finished can still be cut short (a proxy or the site dropping
        # the connection). ffmpeg then fails part way through, so download once more before giving up.
        for attempt in (1, 2):
            video, dl_info = await self._ytdlp(a.ytdlp.download(info.get("webpage_url") or url, tmp, on_progress))
            try:
                p1, final = await self._check_and_convert(job_id, tmp, video, options)
                break
            except ToolError as e:
                if attempt == 2:
                    raise JobFailed(f"{e.message} (after downloading twice)", tail=e.tail) from e
                log.warning("unreadable download, downloading again",
                            extra={"job_id": job_id, "error": e.message})
                for f in tmp.iterdir():
                    if f.is_file() and (f.name.startswith("video.") or f.name == "final.mp4"):
                        f.unlink(missing_ok=True)
                last["p"] = -1
        full = dl_info if dl_info and str(dl_info.get("id")) == str(info["id"]) else info
        probe = await a.media.ffprobe(final)
        self._progress(job_id, 90)
        await self._finish_ingest(job_id, tmp, url, info, full, tags, options, watch_id, video, p1, final, probe)

    async def _check_and_convert(self, job_id: int, tmp: Path, video: Path, options: dict) -> tuple[dict, Path]:
        """Limits and filters, then remux/transcode. ToolError means ffmpeg could not read the file."""
        a = self.svc.adapters
        size = video.stat().st_size
        if self.s.max_file_bytes and size > self.s.max_file_bytes:
            raise JobFailed(f"Downloaded file is {size / 1048576:.0f} MB, over MAX_FILE_MB={self.s.max_file_mb}", "limit")

        p1 = await a.media.ffprobe(video)
        if not p1.get("video_codec"):
            raise JobFailed("Downloaded file has no video stream")
        filters = options.get("filters") or {}
        if filters.get("vertical_only"):
            reason = m.filter_reason({"vertical_only": True}, {"width": p1.get("width"), "height": p1.get("height")})
            if reason:
                raise JobSkipped("filtered", f"Filtered: {reason}")
        dur = p1.get("duration_s")
        if self.s.max_duration_s and dur and dur > self.s.max_duration_s:
            raise JobFailed(f"Video is {dur / 60:.1f} min long, over MAX_DURATION_MIN={self.s.max_duration_min}", "limit")

        need_v = p1.get("video_codec") != "h264"
        need_a = bool(p1.get("has_audio")) and p1.get("audio_codec") != "aac"
        transcode = self.s.transcode_incompatible
        self._progress(job_id, 81)
        final = tmp / "final.mp4"
        await a.media.convert(
            video,
            final,
            video_copy=not (need_v and transcode),
            audio_copy=not (need_a and transcode),
            has_audio=bool(p1.get("has_audio")),
        )
        return p1, final

    async def _finish_ingest(self, job_id: int, tmp: Path, url: str, info: dict, full: dict, tags: list[str],
                             options: dict, watch_id: int | None, video: Path, p1: dict, final: Path,
                             probe: dict) -> None:
        a = self.svc.adapters
        need_v = p1.get("video_codec") != "h264"
        need_a = bool(p1.get("has_audio")) and p1.get("audio_codec") != "aac"
        transcode = self.s.transcode_incompatible

        source = m.map_source(full)
        creator = m.map_creator(full, source)
        vid = str(full["id"])
        paths = m.media_paths(source["domain"], vid, creator["handle"])
        media_dir = self.s.media_dir

        # Poster: site thumbnail converted to JPEG, else a frame at 10% of the duration.
        poster_tmp = tmp / "poster.jpg"
        thumb_url = m.thumbnail_url_of(full)
        have_poster = False
        if thumb_url and await a.fetcher.fetch(thumb_url, tmp / "thumb.src"):
            try:
                await a.media.image_to_jpeg(tmp / "thumb.src", poster_tmp)
                have_poster = poster_tmp.exists()
            except ToolError as e:
                log.info("thumbnail conversion failed", extra={"job_id": job_id, "error": e.message})
        if not have_poster:
            try:
                await a.media.frame_at(final, (probe.get("duration_s") or 0) * 0.1, poster_tmp)
                have_poster = poster_tmp.exists()
            except ToolError as e:
                log.info("poster frame failed", extra={"job_id": job_id, "error": e.message})

        # Creator avatar, best effort, fetched once per creator.
        avatar_path = None
        if paths["avatar"]:
            dst = media_dir / paths["avatar"]
            if dst.exists():
                avatar_path = paths["avatar"]
            else:
                av_url = options.get("avatar_url") or m.avatar_url_of(full)
                if not av_url and creator.get("profile_url"):
                    av_url = await self._avatar_from_profile(creator["profile_url"])
                if av_url and await a.fetcher.fetch(av_url, tmp / "avatar.src"):
                    try:
                        await a.media.image_to_jpeg(tmp / "avatar.src", tmp / "avatar.jpg", max_side=256)
                        await asyncio.to_thread(place_file, tmp / "avatar.jpg", dst)
                        avatar_path = paths["avatar"]
                    except ToolError as e:
                        log.info("avatar conversion failed", extra={"job_id": job_id, "error": e.message})

        file_info = {
            "path": paths["video"],
            "original_filename": m.original_filename(full.get("title"), "mp4"),
            "size_bytes": final.stat().st_size,
            "width": probe.get("width"),
            "height": probe.get("height"),
            "duration_s": probe.get("duration_s"),
            "fps": probe.get("fps"),
            "video_codec": probe.get("video_codec"),
            "audio_codec": probe.get("audio_codec"),
            "has_audio": bool(probe.get("has_audio")),
            "container": "mp4",
        }
        poster_path = paths["poster"] if have_poster else None
        payload = m.build_payload(
            full,
            request_tags=tags,
            file_info=file_info,
            paths=paths,
            poster_path=poster_path,
            avatar_path=avatar_path,
        )

        sidecar = m.sanitize_info(full, thumb_url)
        sidecar["_import"] = {
            "imported_at": iso(self.svc.now()),
            "importer_version": IMPORTER_VERSION,
            "ytdlp_version": await a.ytdlp.version(),
            "request_tags": tags,
            "watch_id": watch_id,
            "job_id": job_id,
            "source": payload["source"],
            "creator": payload["creator"],
            "file": file_info,
            "poster_path": poster_path,
            "info_json_path": paths["metadata"],
            "transcoded": {"video": need_v and transcode, "audio": need_a and transcode},
            "ffprobe": {k: v for k, v in probe.items() if k != "raw"} | {"raw": probe.get("raw")},
            "source_ffprobe": {k: v for k, v in p1.items() if k != "raw"},
        }

        def move_all() -> None:
            place_file(final, media_dir / paths["video"])
            if have_poster:
                place_file(poster_tmp, media_dir / paths["poster"])
            write_json_atomic(media_dir / paths["metadata"], sidecar)

        await asyncio.to_thread(move_all)
        self._update(job_id, payload=payload, title=full.get("title"), progress=95)
        await self._ingest(job_id, payload)

    async def _avatar_from_profile(self, profile_url: str) -> str | None:
        """Video pages rarely expose the creator avatar; a cheap flat listing of the profile usually does."""
        try:
            listing = await asyncio.wait_for(
                self.svc.adapters.ytdlp.probe(profile_url, flat=True, playlist_end=1), timeout=90
            )
            return m.avatar_url_of(listing) if isinstance(listing, dict) else None
        except Exception as e:  # noqa: BLE001 - best effort only
            log.info("avatar lookup failed", extra={"url": profile_url, "error": str(e)[:300]})
            return None

    async def _ingest(self, job_id: int, payload: dict) -> None:
        delays = list(self.s.ingest_backoff_s)
        attempt = 0
        while True:
            try:
                video_id = await self.svc.adapters.app.ingest(payload)
                break
            except AppError as e:
                if not e.retryable or attempt >= len(delays):
                    raise JobFailed(f"Ingest failed: {e} (media files kept; retry the job to ingest again)",
                                    "ingest_error") from e
                log.warning("ingest failed, retrying", extra={"job_id": job_id, "attempt": attempt + 1, "error": str(e)})
                await asyncio.sleep(delays[attempt])
                attempt += 1
        self.finish(job_id, "done", "imported", video_id=video_id, detail=f"Imported as video {video_id}")
