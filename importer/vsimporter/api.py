"""HTTP API (DESIGN 11.1)."""

from __future__ import annotations

import hmac
import os
from contextlib import asynccontextmanager
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from .config import IMPORTER_VERSION
from .jobs import Conflict
from .proxy import masked as masked_proxy
from .models import JOB_STATUSES, Job, Watch, WatchItem, WatchRun
from .service import Importer
from .ui import INDEX_HTML
from .watches import AlreadyRunning, DuplicateWatch, item_to_dict, run_to_dict

# ------------------------------------------------------------------ models


def _check_url(v: str) -> str:
    v = (v or "").strip()
    if not v.startswith(("http://", "https://")) or len(v) > 4000:
        raise ValueError("url must be an http(s) URL")
    return v


class ImportIn(BaseModel):
    url: str
    tags: list[str] = Field(default_factory=list)
    expand_playlists: bool = False
    max_items: int = Field(50, ge=1, le=1000)

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        return _check_url(v)


class ClearIn(BaseModel):
    """Which finished jobs to remove from the history. All filters are optional and combine."""

    ids: list[int] | None = None
    statuses: list[Literal["done", "failed", "skipped"]] | None = None
    older_than_days: int | None = Field(default=None, ge=0)


class BatchIn(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=1000)
    tags: list[str] = Field(default_factory=list)
    expand_playlists: bool = False
    max_items: int = Field(50, ge=1, le=1000)

    @field_validator("urls")
    @classmethod
    def _urls(cls, v: list[str]) -> list[str]:
        return [_check_url(u) for u in v]


class Filters(BaseModel):
    max_duration_s: float | None = Field(None, ge=0)
    min_duration_s: float | None = Field(None, ge=0)
    title_include: list[str] | None = None
    title_exclude: list[str] | None = None
    vertical_only: bool = False
    uploaded_after: date | None = None

    @field_validator("title_include", "title_exclude", mode="before")
    @classmethod
    def _split(cls, v: Any) -> Any:
        if isinstance(v, str):
            return [x.strip() for x in v.split(",") if x.strip()]
        return v

    def as_dict(self) -> dict:
        d = self.model_dump(exclude_none=True)
        if not d.get("vertical_only"):
            d.pop("vertical_only", None)
        if "uploaded_after" in d:
            d["uploaded_after"] = d["uploaded_after"].isoformat()
        return d


class WatchIn(BaseModel):
    url: str
    name: str | None = None
    tags: list[str] = Field(default_factory=list)
    initial_mode: Literal["new_only", "backfill_latest", "backfill_all"] = "new_only"
    backfill_count: int = Field(20, ge=1, le=5000)
    filters: Filters | None = None
    enabled: bool = True

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        return _check_url(v)


class WatchPatch(BaseModel):
    name: str | None = None
    tags: list[str] | None = None
    filters: Filters | None = None
    enabled: bool | None = None
    resume_mode: Literal["import_missed", "only_new"] = "import_missed"
    initial_mode: Literal["new_only", "backfill_latest", "backfill_all"] | None = None
    backfill_count: int | None = Field(None, ge=1, le=5000)


# ------------------------------------------------------------------ app


def svc_of(request: Request) -> Importer:
    return request.app.state.svc


def require_key(request: Request, x_api_key: str | None = Header(default=None)) -> None:
    expected = svc_of(request).settings.importer_api_key
    if not expected:
        raise HTTPException(503, "IMPORTER_API_KEY is not configured")
    if not x_api_key or not hmac.compare_digest(x_api_key.encode(), expected.encode()):
        raise HTTPException(401, "Invalid or missing X-API-Key")


def create_app(svc: Importer, *, background: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        svc.init()
        if background:
            await svc.start()
        try:
            yield
        finally:
            await svc.stop()

    app = FastAPI(title="Tastebud Importer", version=IMPORTER_VERSION, lifespan=lifespan)
    app.state.svc = svc

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index() -> str:
        return INDEX_HTML

    @app.get("/api/health")
    async def health(request: Request) -> dict:
        s = svc_of(request)
        counts = s.jobs.counts()
        return {
            "status": "ok",
            "version": IMPORTER_VERSION,
            "yt_dlp_version": await s.adapters.ytdlp.version(),
            "yt_dlp_plugins": await s.adapters.ytdlp.plugins(),
            "proxy": {
                "enabled": bool(s.settings.proxy),
                "url": masked_proxy(s.settings.proxy),
                "firewall": os.environ.get("TASTEBUD_FIREWALL") == "active",
            },
            "jobs": {k: counts.get(k, 0) for k in JOB_STATUSES},
        }

    r = APIRouter(prefix="/api", dependencies=[Depends(require_key)])

    # -------------------------------------------------------------- imports

    @r.post("/imports", status_code=202)
    async def create_import(body: ImportIn, request: Request) -> dict:
        s = svc_of(request)
        with s.Session() as db:
            job = s.jobs.create(db, body.url, tags=body.tags, expand_playlists=body.expand_playlists,
                                max_items=body.max_items)
            db.commit()
            out = s.jobs.serialize(db, [job])[0]
        return {"job_id": out["id"], "status": out["status"], "job": out}

    @r.post("/imports/batch", status_code=202)
    async def create_batch(body: BatchIn, request: Request) -> dict:
        s = svc_of(request)
        with s.Session() as db:
            jobs = [
                s.jobs.create(db, u, tags=body.tags, expand_playlists=body.expand_playlists, max_items=body.max_items)
                for u in body.urls
            ]
            db.commit()
            return {"job_ids": [j.id for j in jobs], "status": "queued"}

    @r.get("/imports")
    async def list_imports(
        request: Request,
        status: Literal["queued", "running", "done", "failed", "skipped"] | None = None,
        limit: int = Query(50, ge=1, le=1000),
        watch_id: int | None = None,
    ) -> list[dict]:
        s = svc_of(request)
        q = select(Job).order_by(Job.id.desc()).limit(limit)
        if status:
            q = q.where(Job.status == status)
        if watch_id is not None:
            q = q.where(Job.watch_id == watch_id)
        with s.Session() as db:
            return s.jobs.serialize(db, list(db.scalars(q)))

    def _job_or_404(db, job_id: int) -> Job:
        job = db.get(Job, job_id)
        if job is None:
            raise HTTPException(404, "Job not found")
        return job

    @r.get("/imports/{job_id}")
    async def get_import(job_id: int, request: Request) -> dict:
        s = svc_of(request)
        with s.Session() as db:
            return s.jobs.serialize(db, [_job_or_404(db, job_id)])[0]

    @r.post("/imports/{job_id}/retry", status_code=202)
    async def retry_import(job_id: int, request: Request) -> dict:
        s = svc_of(request)
        try:
            job = s.jobs.retry(job_id)
        except Conflict as e:
            raise HTTPException(409, str(e)) from e
        if job is None:
            raise HTTPException(404, "Job not found")
        with s.Session() as db:
            return s.jobs.serialize(db, [db.get(Job, job_id)])[0]

    @r.delete("/imports/{job_id}/record")
    async def delete_import_record(job_id: int, request: Request) -> dict:
        """Remove one finished job from the history (409 if it is queued or running)."""
        s = svc_of(request)
        with s.Session() as db:
            _job_or_404(db, job_id)
        try:
            ids = s.jobs.delete_records([job_id], strict=True)
        except Conflict as e:
            raise HTTPException(409, str(e)) from e
        return {"deleted": len(ids), "job_ids": ids}

    @r.post("/imports/clear")
    async def clear_imports(body: ClearIn, request: Request) -> dict:
        """Bulk-remove finished jobs from the history, filtered by ids, status and/or age."""
        s = svc_of(request)
        ids = s.jobs.delete_records(body.ids, body.statuses, body.older_than_days)
        return {"deleted": len(ids), "job_ids": ids}

    @r.delete("/imports/{job_id}")
    async def cancel_import(job_id: int, request: Request) -> dict:
        s = svc_of(request)
        try:
            job = await s.jobs.cancel(job_id)
        except Conflict as e:
            raise HTTPException(409, str(e)) from e
        if job is None:
            raise HTTPException(404, "Job not found")
        with s.Session() as db:
            return s.jobs.serialize(db, [db.get(Job, job_id)])[0]

    # -------------------------------------------------------------- watches

    def _watch_or_404(db, watch_id: int) -> Watch:
        w = db.get(Watch, watch_id)
        if w is None:
            raise HTTPException(404, "Watch not found")
        return w

    @r.get("/watches")
    async def list_watches(
        request: Request, creator_source_domain: str | None = None, creator_handle: str | None = None
    ) -> list[dict]:
        s = svc_of(request)
        with s.Session() as db:
            return s.watches.serialize(db, s.watches.list(db, creator_source_domain, creator_handle))

    @r.post("/watches", status_code=201)
    async def create_watch(body: WatchIn, request: Request) -> Any:
        s = svc_of(request)
        with s.Session() as db:
            try:
                w = s.watches.create(
                    db,
                    url=body.url,
                    name=body.name,
                    tags=body.tags,
                    initial_mode=body.initial_mode,
                    backfill_count=body.backfill_count,
                    filters=body.filters.as_dict() if body.filters else {},
                    enabled=body.enabled,
                )
            except DuplicateWatch as e:
                return JSONResponse({"detail": str(e), "watch_id": e.watch_id}, status_code=409)
            db.commit()
            wid, enabled = w.id, w.enabled
        if enabled and request.app.state.background:
            # First run straight away: records the baseline or starts the backfill.
            s.watches.run_in_background(wid, trigger="create")
        with s.Session() as db:
            return s.watches.serialize(db, [db.get(Watch, wid)])[0]

    @r.get("/watches/{watch_id}")
    async def get_watch(watch_id: int, request: Request) -> dict:
        s = svc_of(request)
        with s.Session() as db:
            return s.watches.serialize(db, [_watch_or_404(db, watch_id)])[0]

    @r.patch("/watches/{watch_id}")
    async def patch_watch(watch_id: int, body: WatchPatch, request: Request) -> dict:
        s = svc_of(request)
        data = body.model_dump(exclude_unset=True)
        if body.filters is not None:
            data["filters"] = body.filters.as_dict()
        data["resume_mode"] = body.resume_mode
        with s.Session() as db:
            w = _watch_or_404(db, watch_id)
            s.watches.update(db, w, data)
            db.commit()
            return s.watches.serialize(db, [w])[0]

    @r.delete("/watches/{watch_id}", status_code=204)
    async def delete_watch(watch_id: int, request: Request) -> Response:
        s = svc_of(request)
        with s.Session() as db:
            s.watches.delete(db, _watch_or_404(db, watch_id))
            db.commit()
        return Response(status_code=204)

    @r.post("/watches/{watch_id}/run", status_code=202)
    async def run_watch(watch_id: int, request: Request, wait: bool = False) -> Any:
        s = svc_of(request)
        with s.Session() as db:
            _watch_or_404(db, watch_id)
        if s.watches.is_running(watch_id):
            raise HTTPException(409, "Watch is already running")
        if wait:
            try:
                run = await s.watches.run_watch(watch_id, trigger="manual")
            except AlreadyRunning as e:
                raise HTTPException(409, str(e)) from e
            return JSONResponse(run_to_dict(run), status_code=200)
        s.watches.run_in_background(watch_id, trigger="manual")
        return {"status": "started", "watch_id": watch_id}

    @r.get("/watches/{watch_id}/runs")
    async def watch_runs(watch_id: int, request: Request, limit: int = Query(50, ge=1, le=1000)) -> list[dict]:
        s = svc_of(request)
        with s.Session() as db:
            _watch_or_404(db, watch_id)
            runs = db.scalars(
                select(WatchRun).where(WatchRun.watch_id == watch_id).order_by(WatchRun.id.desc()).limit(limit)
            )
            return [run_to_dict(x) for x in runs]

    @r.get("/watches/{watch_id}/items")
    async def watch_items(
        watch_id: int, request: Request, status: str | None = None, limit: int = Query(500, ge=1, le=10000)
    ) -> list[dict]:
        s = svc_of(request)
        with s.Session() as db:
            _watch_or_404(db, watch_id)
            q = select(WatchItem).where(WatchItem.watch_id == watch_id)
            if status:
                q = q.where(WatchItem.status == status)
            q = q.order_by(WatchItem.updated_at.desc(), WatchItem.source_video_id).limit(limit)
            return [item_to_dict(i) for i in db.scalars(q)]

    app.include_router(r)
    app.state.background = background
    return app
