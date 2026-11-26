import itertools
import os

import pytest
from fastapi.testclient import TestClient

INGEST_KEY = "test-ingest-key"
ADMIN_PW = "admin-pass-123"
_counter = itertools.count(1)


@pytest.fixture
def env(tmp_path, monkeypatch):
    data, media = tmp_path / "data", tmp_path / "media"
    data.mkdir()
    media.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("MEDIA_DIR", str(media))
    monkeypatch.setenv("STATIC_DIR", str(tmp_path / "no-static"))
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PW)
    monkeypatch.setenv("INGEST_API_KEY", INGEST_KEY)
    monkeypatch.setenv("IMPORTER_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("IMPORTER_API_KEY", "imp")
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("FEED_W_RANDOM", "0")
    from vidswipe.config import get_settings
    from vidswipe.db import init_engine
    from vidswipe.security import login_limiter
    from vidswipe.services import search_index

    get_settings.cache_clear()
    init_engine()
    search_index.invalidate()
    login_limiter.reset()
    yield {"data": data, "media": media}
    get_settings.cache_clear()


@pytest.fixture
def client(env):
    from vidswipe.main import create_app

    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def db(client):
    from vidswipe.db import new_session

    s = new_session()
    yield s
    s.close()


def login(client, username="admin", password=ADMIN_PW):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return r.json()


@pytest.fixture
def admin(client):
    login(client)
    return client


def make_payload(tags=None, creator="creator1", domain="youtube.com", vid=None, width=1080, height=1920,
                 request_tags=None, title=None):
    vid = vid or f"v{next(_counter)}"
    return {
        "source": {"domain": domain, "name": "YouTube"},
        "source_video_id": vid,
        "source_url": f"https://{domain}/watch?v={vid}",
        "creator": {"name": creator.title(), "handle": creator, "profile_url": f"https://{domain}/@{creator}"},
        "title": title or f"Video {vid}",
        "description": "desc",
        "tags": tags if tags is not None else ["cars"],
        "request_tags": request_tags or [],
        "upload_date": "2026-11-04",
        "file": {"path": f"videos/{domain}/{vid}.mp4", "original_filename": f"{vid}.mp4", "size_bytes": 1234,
                 "width": width, "height": height, "duration_s": 42.3, "fps": 30, "video_codec": "h264",
                 "audio_codec": "aac", "has_audio": True, "container": "mp4"},
        "poster_path": f"posters/{domain}/{vid}.jpg",
        "info_json_path": f"metadata/{domain}/{vid}.json",
    }


def ingest(client, **kw) -> int:
    r = client.post("/api/internal/ingest", json=make_payload(**kw), headers={"X-Ingest-Key": INGEST_KEY})
    assert r.status_code == 200, r.text
    return r.json()["video_id"]


def tag_id(client, name):
    r = client.get("/api/admin/tags/resolve", params={"name": name})
    t = r.json()["tag"]
    return t["id"] if t else None


os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
