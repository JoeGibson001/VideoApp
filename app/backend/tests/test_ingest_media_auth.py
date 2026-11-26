"""Ingest idempotency, media Range requests and auth/CSRF (DESIGN.md 8, 10, 11.5, 14)."""
from .conftest import INGEST_KEY, ingest, login, make_payload


def test_ingest_idempotent_and_details(admin):
    p = make_payload(tags=["Cars", " cars ", "Drift"], vid="abc123")
    h = {"X-Ingest-Key": INGEST_KEY}
    r1 = admin.post("/api/internal/ingest", json=p, headers=h).json()
    r2 = admin.post("/api/internal/ingest", json=p, headers=h).json()
    assert r1["video_id"] == r2["video_id"] and r1["created"] and not r2["created"]
    d = admin.get(f"/api/videos/{r1['video_id']}").json()
    assert d["width"] == 1080 and d["height"] == 1920 and d["duration_s"] == 42.3
    assert d["video_codec"] == "h264" and d["has_audio"] is True and d["fps"] == 30
    assert sorted(t["name"] for t in d["tags"]) == ["cars", "drift"]
    assert d["video_url"] == "/media/videos/youtube.com/abc123.mp4"
    ex = admin.get("/api/internal/exists", params={"source_domain": "www.youtube.com", "source_video_id": "abc123"},
                   headers=h).json()
    assert ex == {"exists": True, "video_id": r1["video_id"]}


def test_ingest_requires_key(client):
    assert client.post("/api/internal/ingest", json=make_payload()).status_code == 401
    assert client.post("/api/internal/ingest", json=make_payload(),
                       headers={"X-Ingest-Key": "wrong"}).status_code == 401


def test_no_tags_gets_domain_tag(admin):
    v = ingest(admin, tags=[], domain="vimeo.com")
    assert [t["name"] for t in admin.get(f"/api/videos/{v}").json()["tags"]] == ["vimeo.com"]


def test_request_tags_origin(admin):
    v = ingest(admin, tags=["a"], request_tags=["test-import"])
    links = {t["name"]: t["origin"] for t in admin.get(f"/api/admin/videos/{v}").json()["tag_links"]}
    assert links == {"a": "site", "test-import": "import_request"}


def _media_file(env):
    p = env["media"] / "videos" / "youtube.com"
    p.mkdir(parents=True)
    data = bytes(range(256)) * 40  # 10240 bytes
    (p / "x.mp4").write_bytes(data)
    return data


def test_range_request_206(admin, env):
    client = admin  # visitors see no sources by default
    data = _media_file(env)
    r = client.get("/media/videos/youtube.com/x.mp4", headers={"Range": "bytes=100-199"})
    assert r.status_code == 206
    assert r.headers["content-range"] == f"bytes 100-199/{len(data)}"
    assert r.headers["content-length"] == "100"
    assert r.content == data[100:200]
    r = client.get("/media/videos/youtube.com/x.mp4", headers={"Range": "bytes=10000-"})
    assert r.status_code == 206 and r.headers["content-range"] == f"bytes 10000-10239/{len(data)}"
    r = client.get("/media/videos/youtube.com/x.mp4", headers={"Range": "bytes=-40"})
    assert r.status_code == 206 and r.content == data[-40:]
    r = client.get("/media/videos/youtube.com/x.mp4", headers={"Range": "bytes=999999-"})
    assert r.status_code == 416
    r = client.get("/media/videos/youtube.com/x.mp4")
    assert r.status_code == 200 and r.content == data and r.headers["accept-ranges"] == "bytes"


def test_media_traversal_blocked(client, env):
    _media_file(env)
    (env["media"] / "secret.txt").write_text("nope")
    for path in ["/media/videos/../secret.txt", "/media/videos/%2e%2e/secret.txt", "/media/secret.txt",
                 "/media/metadata/x.json", "/media/videos/youtube.com/../../secret.txt"]:
        assert client.get(path).status_code == 404, path


def test_login_logout_and_csrf(client):
    assert client.get("/api/auth/me").json()["user"] is None
    assert client.post("/api/auth/login", json={"username": "admin", "password": "bad"}).status_code == 401
    login(client)
    me = client.get("/api/auth/me").json()
    assert me["user"]["username"] == "admin" and me["user"]["is_admin"]
    token = client.headers.pop("X-CSRF-Token")
    assert client.post("/api/collections", json={"name": "x"}).status_code == 403
    client.headers["X-CSRF-Token"] = token
    assert client.post("/api/collections", json={"name": "x"}).status_code == 201
    cookie = client.cookies.get("vs_session")
    assert cookie
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").json()["user"] is None


def test_session_cookie_flags(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin-pass-123"})
    sc = [h for h in r.headers.get_list("set-cookie") if h.startswith("vs_session=")][0].lower()
    assert "httponly" in sc and "samesite=lax" in sc


def test_login_rate_limited(client):
    codes = [client.post("/api/auth/login", json={"username": "admin", "password": "x"}).status_code
             for _ in range(12)]
    assert 429 in codes


def test_admin_endpoints_require_admin(client):
    assert client.get("/api/admin/videos").status_code == 401
    from vidswipe.db import new_session
    from vidswipe.services.users import create_user

    s = new_session()
    create_user(s, "bob", "bobpass")
    s.close()
    login(client, "bob", "bobpass")
    assert client.get("/api/admin/videos").status_code == 403
    assert client.post("/api/admin/recompute-stats").status_code == 403
    v = ingest(client)
    assert client.get(f"/api/creators/{client.get(f'/api/videos/{v}').json()['creator']['id']}").json().get(
        "watch") is None


def test_user_data_isolated(client):
    from vidswipe.db import new_session
    from vidswipe.services.users import create_user

    s = new_session()
    create_user(s, "bob", "bobpass")
    s.close()
    login(client)
    v = ingest(client)
    client.put(f"/api/videos/{v}/rating", json={"value": 1})
    mine = client.post("/api/collections", json={"name": "mine"}).json()["id"]
    client.post("/api/auth/logout")
    login(client, "bob", "bobpass")
    assert client.get(f"/api/videos/{v}").json()["my_rating"] is None
    assert client.get(f"/api/collections/{mine}").status_code == 404
    assert [x["id"] for x in client.get("/api/feed").json()["items"]] == [v]
