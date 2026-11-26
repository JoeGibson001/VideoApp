"""FastAPI application: API, media and the PWA build."""
import json
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import get_settings
from .db import get_engine, new_session
from .routers import admin, api, auth, internal, media
from .services.users import seed_admin


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {"ts": self.formatTime(record), "level": record.levelname, "logger": record.name,
                "msg": record.getMessage()}
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)


def run_migrations() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    with get_engine().begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")


def startup() -> None:
    s = get_settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    run_migrations()
    db = new_session()
    try:
        seed_admin(db)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    startup()
    yield


def create_app(run_startup: bool = True) -> FastAPI:
    app = FastAPI(title="VidSwipe", version=__version__, lifespan=lifespan if run_startup else None,
                  docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.include_router(auth.router)
    app.include_router(api.router)
    app.include_router(admin.router)
    app.include_router(internal.router)
    app.include_router(media.router)

    @app.get("/api/health")
    def health():
        return {"ok": True, "version": __version__}

    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def api_404(rest: str):
        raise HTTPException(404, "Not found")

    static_dir = get_settings().static_dir
    if static_dir.exists():
        assets = static_dir / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            candidate = (static_dir / path).resolve()
            if path and candidate.is_file() and static_dir.resolve() in candidate.parents:
                headers = {}
                if candidate.name in ("sw.js", "registerSW.js", "manifest.webmanifest", "index.html"):
                    headers["Cache-Control"] = "no-cache"
                return FileResponse(candidate, headers=headers)
            return FileResponse(static_dir / "index.html", headers={"Cache-Control": "no-cache"})
    else:
        @app.get("/", include_in_schema=False)
        def root():
            return JSONResponse({"app": "VidSwipe", "note": "frontend build not found"})

    return app


configure_logging()
app = create_app()
