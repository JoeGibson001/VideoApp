"""Watches: automatic imports of new content (DESIGN 11.7)."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import TYPE_CHECKING, Any, Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import mapping as m
from .models import Job, Watch, WatchItem, WatchRun
from .util import iso, next_run_after

if TYPE_CHECKING:
    from .service import Importer

log = logging.getLogger("vsimporter.watches")

INITIAL_MODES = ("new_only", "backfill_latest", "backfill_all")
CHANNEL_EARLY_STOP = 20
AUTO_RETRIES = 2


class DuplicateWatch(Exception):
    def __init__(self, watch_id: int):
        super().__init__(f"A watch for this URL already exists (id {watch_id})")
        self.watch_id = watch_id


class AlreadyRunning(Exception):
    pass


def run_to_dict(r: WatchRun | None) -> dict | None:
    if r is None:
        return None
    return {
        "id": r.id,
        "watch_id": r.watch_id,
        "trigger": r.trigger,
        "started_at": iso(r.started_at),
        "finished_at": iso(r.finished_at),
        "listed_count": r.listed_count,
        "new_count": r.new_count,
        "queued_count": r.queued_count,
        "error": r.error,
    }


def item_to_dict(i: WatchItem) -> dict:
    return {
        "watch_id": i.watch_id,
        "source_video_id": i.source_video_id,
        "url": i.url,
        "title": i.title,
        "status": i.status,
        "job_id": i.job_id,
        "detail": i.detail,
        "retry_count": i.retry_count,
        "first_seen_at": iso(i.first_seen_at),
        "updated_at": iso(i.updated_at),
    }


def _handle_key(h: str | None) -> str:
    return (h or "").strip().lstrip("@").lower()


class WatchService:
    def __init__(self, svc: "Importer"):
        self.svc = svc
        self._locks: dict[int, asyncio.Lock] = {}
        self._bg: set[asyncio.Task] = set()

    @property
    def s(self):
        return self.svc.settings

    # ------------------------------------------------------------ serialisation

    def serialize(self, s: Session, watches: list[Watch]) -> list[dict]:
        ids = [w.id for w in watches]
        counts: dict[int, dict[str, int]] = {i: {} for i in ids}
        last_runs: dict[int, WatchRun] = {}
        if ids:
            for wid, status, n in s.execute(
                select(WatchItem.watch_id, WatchItem.status, func.count())
                .where(WatchItem.watch_id.in_(ids))
                .group_by(WatchItem.watch_id, WatchItem.status)
            ):
                counts[wid][status] = n
            sub = select(func.max(WatchRun.id)).where(WatchRun.watch_id.in_(ids)).group_by(WatchRun.watch_id)
            for r in s.scalars(select(WatchRun).where(WatchRun.id.in_(sub))):
                last_runs[r.watch_id] = r
        out = []
        for w in watches:
            c = counts.get(w.id, {})
            out.append(
                {
                    "id": w.id,
                    "url": w.url,
                    "kind": w.kind,
                    "name": w.name,
                    "source_domain": w.source_domain,
                    "enabled": w.enabled,
                    "paused_at": iso(w.paused_at),
                    "tags": list(w.tags or []),
                    "filters": dict(w.filters or {}),
                    "initial_mode": w.initial_mode,
                    "backfill_count": w.backfill_count,
                    "created_at": iso(w.created_at),
                    "last_checked_at": iso(w.last_checked_at),
                    "last_success_at": iso(w.last_success_at),
                    "last_new_count": w.last_new_count,
                    "last_error": w.last_error,
                    "consecutive_failures": w.consecutive_failures,
                    "next_run_at": iso(w.next_run_at),
                    "creator_source_domain": w.creator_source_domain,
                    "creator_handle": w.creator_handle,
                    "imported_count": c.get("imported", 0),
                    "erroring": (w.consecutive_failures or 0) >= 3,
                    "running": self.is_running(w.id),
                    "item_counts": c,
                    "last_run": run_to_dict(last_runs.get(w.id)),
                }
            )
        return out

    def list(self, s: Session, creator_source_domain: str | None = None, creator_handle: str | None = None) -> list[Watch]:
        q = select(Watch).order_by(Watch.id)
        if creator_source_domain:
            dom = creator_source_domain.strip().lower()
            if dom.startswith("www."):
                dom = dom[4:]
            q = q.where(func.lower(Watch.creator_source_domain) == dom)
        watches = list(s.scalars(q))
        if creator_handle:
            key = _handle_key(creator_handle)
            watches = [w for w in watches if w.creator_handle and _handle_key(w.creator_handle) == key]
        return watches

    # ------------------------------------------------------------ crud

    def create(
        self,
        s: Session,
        *,
        url: str,
        name: str | None = None,
        tags: list[str] | None = None,
        initial_mode: str = "new_only",
        backfill_count: int = 20,
        filters: dict | None = None,
        enabled: bool = True,
    ) -> Watch:
        norm = m.normalize_url(url)
        existing = s.scalars(select(Watch).where(Watch.normalized_url == norm)).first()
        if existing is not None:
            raise DuplicateWatch(existing.id)
        now = self.svc.now()
        domain = m.domain_of(url)
        kind = m.guess_kind(url)
        w = Watch(
            url=url.strip(),
            normalized_url=norm,
            kind=kind,
            name=name or None,
            source_domain=domain,
            enabled=enabled,
            paused_at=None if enabled else now,
            tags=m.normalize_tags(tags),
            filters=filters or {},
            initial_mode=initial_mode,
            backfill_count=backfill_count,
            initial_done=False,
            rebaseline_pending=False,
            created_at=now,
            consecutive_failures=0,
            next_run_at=next_run_after(now, self.s.run_time, self.s.zone),
        )
        if kind == "channel":
            w.creator_source_domain = domain
            handle = self._handle_from_url(url)
            if handle:
                w.creator_handle = handle
        s.add(w)
        s.flush()
        return w

    @staticmethod
    def _handle_from_url(url: str) -> str | None:
        import re

        mt = re.search(r"/(@[A-Za-z0-9._-]+)", url)
        return mt.group(1) if mt else None

    def update(self, s: Session, w: Watch, data: dict) -> Watch:
        now = self.svc.now()
        if "name" in data and data["name"] is not None:
            w.name = data["name"] or None
        if "tags" in data and data["tags"] is not None:
            w.tags = m.normalize_tags(data["tags"])
        if "filters" in data and data["filters"] is not None:
            w.filters = data["filters"]
        if not w.initial_done:
            if data.get("initial_mode"):
                w.initial_mode = data["initial_mode"]
            if data.get("backfill_count"):
                w.backfill_count = data["backfill_count"]
        if "enabled" in data and data["enabled"] is not None:
            if not data["enabled"] and w.enabled:
                w.enabled, w.paused_at = False, now
            elif data["enabled"] and not w.enabled:
                w.enabled, w.paused_at = True, None
                if (data.get("resume_mode") or "import_missed") == "only_new" and w.initial_done:
                    w.rebaseline_pending = True
                    for item in s.scalars(
                        select(WatchItem).where(WatchItem.watch_id == w.id, WatchItem.status == "pending")
                    ):
                        item.status, item.updated_at, item.detail = "baseline", now, "re-baselined on resume"
                if w.next_run_at is None or w.next_run_at < now:
                    w.next_run_at = now
        return w

    def delete(self, s: Session, w: Watch) -> None:
        for job in s.scalars(select(Job).where(Job.watch_id == w.id, Job.status == "queued")):
            job.status, job.outcome, job.error, job.finished_at = "failed", "cancelled", "Watch deleted", self.svc.now()
        s.delete(w)

    # ------------------------------------------------------------ job hooks

    def on_job_requeued(self, s: Session, job: Job) -> None:
        if job.watch_id and job.watch_item_id:
            item = s.get(WatchItem, (job.watch_id, job.watch_item_id))
            if item is not None:
                item.status, item.job_id, item.updated_at, item.detail = "queued", job.id, self.svc.now(), None

    def on_job_finished(self, s: Session, job: Job) -> None:
        if not job.watch_id or not job.watch_item_id:
            return
        item = s.get(WatchItem, (job.watch_id, job.watch_item_id))
        if item is None or item.job_id != job.id:
            return
        item.updated_at = self.svc.now()
        if job.status == "done":
            item.status, item.detail = "imported", f"video {job.video_id}"
            w = s.get(Watch, job.watch_id)
            creator = (job.payload or {}).get("creator") or {}
            source = (job.payload or {}).get("source") or {}
            if w is not None and w.kind == "channel" and creator.get("handle"):
                # The video's own creator handle is what the App stores, so prefer it.
                w.creator_handle = creator["handle"]
                w.creator_source_domain = source.get("domain") or w.creator_source_domain
        elif job.status == "skipped":
            item.status = "skipped_exists" if job.outcome == "exists" else "filtered"
            item.detail = job.detail
        elif job.outcome == "unavailable":
            item.status, item.detail = "unavailable", job.error
        elif job.outcome == "live":
            # Live/upcoming: forget it so the next run sees it again once it is a normal video.
            s.delete(item)
        else:
            item.status, item.detail = "failed", job.error

    # ------------------------------------------------------------ runs

    def is_running(self, watch_id: int) -> bool:
        lock = self._locks.get(watch_id)
        return bool(lock and lock.locked())

    def run_in_background(self, watch_id: int, trigger: str = "manual") -> None:
        async def _go() -> None:
            try:
                await self.run_watch(watch_id, trigger)
            except AlreadyRunning:
                pass
            except Exception:  # noqa: BLE001
                log.exception("background watch run failed", extra={"watch_id": watch_id})

        t = asyncio.create_task(_go())
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)

    async def run_watch(self, watch_id: int, trigger: str = "manual") -> WatchRun | None:
        lock = self._locks.setdefault(watch_id, asyncio.Lock())
        if lock.locked():
            raise AlreadyRunning(f"Watch {watch_id} is already running")
        async with lock:
            return await self._run(watch_id, trigger)

    async def _list(self, url: str, limit: int) -> tuple[dict, list[list[dict]]]:
        """List a watch target; nested tabs (e.g. a channel root) are listed one level deep."""
        y = self.svc.adapters.ytdlp
        listing = await y.probe(url, flat=True, playlist_end=limit)
        if listing.get("_type") != "playlist" and listing.get("entries") is None:
            raise ValueError("URL is a single video, not a channel or playlist")
        groups: list[list[dict]] = [[]]
        for e in listing.get("entries") or []:
            if not isinstance(e, dict):
                continue
            if m.entry_is_playlist(e):
                sub_url = m.entry_url(e)
                if not sub_url:
                    continue
                sub = await y.probe(sub_url, flat=True, playlist_end=limit)
                groups.append([x for x in sub.get("entries") or [] if isinstance(x, dict) and not m.entry_is_playlist(x)])
            else:
                groups[0].append(e)
        return listing, [g for g in groups if g]

    async def _run(self, watch_id: int, trigger: str) -> WatchRun | None:
        now = self.svc.now()
        with self.svc.Session() as s:
            w = s.get(Watch, watch_id)
            if w is None:
                return None
            run = WatchRun(watch_id=w.id, trigger=trigger, started_at=now, listed_count=0, new_count=0, queued_count=0)
            s.add(run)
            s.commit()
            run_id = run.id
            url, first = w.url, not w.initial_done
            mode, backfill_count = w.initial_mode, w.backfill_count
            rebaseline, filters = w.rebaseline_pending, dict(w.filters or {})
            kind_hint = w.kind
            items = {i.source_video_id: i for i in s.scalars(select(WatchItem).where(WatchItem.watch_id == w.id))}
            known = set(items)
            pending = sorted(
                [(i.position, i.first_seen_at, i.source_video_id, i.url, i.title) for i in items.values() if i.status == "pending"],
                key=lambda x: (x[1], x[0]),
            )
            retries = [
                (i.source_video_id, i.url, i.title)
                for i in items.values()
                if i.status == "failed" and (i.retry_count or 0) < AUTO_RETRIES and i.url
            ]
        log.info("watch run started", extra={"watch_id": watch_id, "trigger": trigger})

        try:
            backfill_all = first and mode == "backfill_all"
            limit = self.s.watch_backfill_max if backfill_all else self.s.watch_scan_limit
            listing, groups = await self._list(url, limit)
            kind = m.guess_kind(url, listing) if kind_hint in ("page", None) or first else kind_hint

            listed = 0
            new: list[dict] = []
            new_ids: set[str] = set()
            for group in groups:
                consecutive = 0
                for pos, e in enumerate(group):
                    vid = str(e.get("id") or "")
                    if not vid:
                        continue
                    listed += 1
                    if m.is_live(e):
                        continue
                    if vid in known:
                        consecutive += 1
                        if kind == "channel" and not backfill_all and consecutive >= CHANNEL_EARLY_STOP:
                            break
                        continue
                    consecutive = 0
                    if vid in new_ids:
                        continue
                    new_ids.add(vid)
                    e = dict(e)
                    e["_pos"] = pos
                    new.append(e)

            to_baseline: list[dict] = []
            consider: list[dict] = []
            if first:
                if mode == "backfill_latest":
                    ordered = self._newest_first(new, kind)
                    consider, to_baseline = ordered[:backfill_count], ordered[backfill_count:]
                elif mode == "backfill_all":
                    cap = self.s.watch_backfill_max
                    consider, to_baseline = new[:cap], new[cap:]
                else:
                    to_baseline = new
            elif rebaseline:
                to_baseline = new
            else:
                consider = new
            if not rebaseline:
                consider += [
                    {"id": vid, "url": u, "title": t, "_pending": True} for (_p, _f, vid, u, t) in pending
                ]

            # Network phase: exists checks, outside any DB transaction.
            cap = self.s.watch_max_per_run
            decisions: list[tuple[dict, str, str | None]] = []
            queued = 0
            app = self.svc.adapters.app
            for e in consider:
                if queued >= cap:
                    decisions.append((e, "pending", None))
                    continue
                e_url = m.entry_url(e) or e.get("url")
                domain = m.domain_of(e_url) or m.domain_of(listing.get("webpage_url")) or m.domain_of(url) or ""
                exists, existing_id = await app.exists(domain, str(e["id"]))
                if exists:
                    decisions.append((e, "skipped_exists", f"already in library (video {existing_id})"))
                    continue
                reason = m.filter_reason(filters, e)
                if reason:
                    decisions.append((e, "filtered", reason))
                    continue
                decisions.append((e, "queue", None))
                queued += 1
            retry_now = []
            for vid, u, _t in retries:
                if queued >= cap:
                    break
                retry_now.append((vid, u))
                queued += 1
        except Exception as e:  # noqa: BLE001
            msg = getattr(e, "message", None) or str(e) or type(e).__name__
            self._record_failure(watch_id, run_id, msg)
            log.warning("watch run failed", extra={"watch_id": watch_id, "error": msg})
            with self.svc.Session() as s:
                return s.get(WatchRun, run_id)

        # Write phase: one transaction, no awaits.
        now = self.svc.now()
        with self.svc.Session() as s:
            w = s.get(Watch, watch_id)
            run = s.get(WatchRun, run_id)
            if w is None or run is None:
                return None
            self._apply_listing(w, listing, kind, url)

            def upsert(e: dict, status: str, detail: str | None = None) -> WatchItem:
                vid = str(e["id"])
                item = s.get(WatchItem, (w.id, vid))
                if item is None:
                    item = WatchItem(
                        watch_id=w.id,
                        source_video_id=vid,
                        first_seen_at=now,
                        retry_count=0,
                        position=int(e.get("_pos") or 0),
                    )
                    s.add(item)
                item.url = m.entry_url(e) or item.url
                item.title = e.get("title") or item.title
                item.status, item.detail, item.updated_at = status, detail, now
                return item

            for e in to_baseline:
                upsert(e, "baseline")
            for e, decision, detail in decisions:
                if decision == "queue":
                    item = upsert(e, "queued")
                    if not item.url:
                        item.status, item.detail = "failed", "no URL for this entry"
                        continue
                    job = self._queue_job(s, w, item)
                    item.job_id = job.id
                else:
                    upsert(e, decision, detail)
            for vid, _u in retry_now:
                item = s.get(WatchItem, (w.id, vid))
                if item is None:
                    continue
                item.retry_count = (item.retry_count or 0) + 1
                item.status, item.updated_at, item.detail = "queued", now, f"automatic retry {item.retry_count}"
                job = self._queue_job(s, w, item)
                item.job_id = job.id

            considered_new = len([e for e in consider if not e.get("_pending")])
            w.initial_done = True
            w.rebaseline_pending = False
            w.last_checked_at = w.last_success_at = now
            w.last_new_count = considered_new
            w.last_error = None
            w.consecutive_failures = 0
            run.finished_at = now
            run.listed_count = listed
            run.new_count = considered_new
            run.queued_count = queued
            s.commit()
            s.refresh(run)
            log.info(
                "watch run finished",
                extra={"watch_id": watch_id, "listed": listed, "new": considered_new, "queued": queued},
            )
            return run

    def _queue_job(self, s: Session, w: Watch, item: WatchItem) -> Job:
        options: dict[str, Any] = {"filters": dict(w.filters or {})}
        if w.avatar_url and w.kind == "channel":
            options["avatar_url"] = w.avatar_url
        return self.svc.jobs.create(
            s,
            item.url or "",
            tags=list(w.tags or []),
            watch_id=w.id,
            watch_item_id=item.source_video_id,
            options=options,
            priority=0,
        )

    @staticmethod
    def _newest_first(entries: list[dict], kind: str) -> list[dict]:
        keys = [_entry_time(e) for e in entries]
        if entries and all(k is not None for k in keys):
            return [e for _, e in sorted(zip(keys, entries, strict=True), key=lambda x: x[0], reverse=True)]
        if kind == "playlist":
            # Playlists usually append new videos at the end.
            return list(reversed(entries))
        return list(entries)

    def _apply_listing(self, w: Watch, listing: dict, kind: str, url: str) -> None:
        w.kind = kind
        if not w.name:
            w.name = listing.get("title") or listing.get("channel") or listing.get("uploader") or w.name
        domain = m.domain_of(listing.get("webpage_url")) or m.domain_of(url)
        if domain:
            w.source_domain = domain
        avatar = m.avatar_url_of(listing)
        if avatar:
            w.avatar_url = avatar
        if kind == "channel":
            w.creator_source_domain = w.creator_source_domain or domain
            if not w.creator_handle:
                handle = listing.get("uploader_id") or listing.get("channel_id") or listing.get("uploader")
                if handle:
                    w.creator_handle = str(handle)
        else:
            w.creator_source_domain = None
            w.creator_handle = None

    def _record_failure(self, watch_id: int, run_id: int, error: str) -> None:
        now = self.svc.now()
        with self.svc.Session() as s:
            w = s.get(Watch, watch_id)
            run = s.get(WatchRun, run_id)
            if run is not None:
                run.finished_at, run.error = now, error[:2000]
            if w is not None:
                w.last_checked_at = now
                w.last_error = error[:2000]
                w.consecutive_failures = (w.consecutive_failures or 0) + 1
            s.commit()


def _entry_time(e: dict) -> float | None:
    ts = e.get("timestamp") or e.get("release_timestamp")
    if isinstance(ts, (int, float)):
        return float(ts)
    d = m.upload_date_of(e)
    if d:
        from datetime import datetime, timezone

        return datetime.fromisoformat(d).replace(tzinfo=timezone.utc).timestamp()
    return None


class Scheduler:
    """Runs every enabled watch once a day at WATCH_RUN_TIME (in TZ), one at a time."""

    def __init__(self, svc: "Importer", gap: Callable[[], float] | None = None):
        self.svc = svc
        self.gap = gap or (lambda: random.uniform(svc.settings.watch_gap_min_s, svc.settings.watch_gap_max_s))
        self._task: asyncio.Task | None = None

    def due(self) -> list[int]:
        now = self.svc.now()
        with self.svc.Session() as s:
            return list(
                s.scalars(
                    select(Watch.id)
                    .where(Watch.enabled.is_(True), Watch.next_run_at.is_not(None), Watch.next_run_at <= now)
                    .order_by(Watch.next_run_at, Watch.id)
                )
            )

    async def tick(self) -> list[int]:
        ran: list[int] = []
        for wid in self.due():
            if ran:
                await self.svc.sleep(self.gap())
            with self.svc.Session() as s:
                w = s.get(Watch, wid)
                if w is None or not w.enabled:
                    continue
            try:
                await self.svc.watches.run_watch(wid, trigger="schedule")
                ran.append(wid)
            except AlreadyRunning:
                pass
            except Exception:  # noqa: BLE001
                log.exception("scheduled watch run crashed", extra={"watch_id": wid})
            finally:
                with self.svc.Session() as s:
                    w = s.get(Watch, wid)
                    if w is not None:
                        w.next_run_at = next_run_after(self.svc.now(), self.svc.settings.run_time, self.svc.settings.zone)
                        s.commit()
        return ran

    async def loop(self, interval: float = 30) -> None:
        while True:
            try:
                await self.tick()
            except Exception:  # noqa: BLE001
                log.exception("scheduler tick failed")
            await asyncio.sleep(interval)

    def start(self) -> None:
        self._task = asyncio.create_task(self.loop(), name="scheduler")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
