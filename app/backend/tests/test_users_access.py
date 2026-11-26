"""Local users, admin user management and per-user / visitor source access."""
from fastapi.testclient import TestClient

from .conftest import ADMIN_PW, ingest, login, tag_id


def _setup(admin, env):
    """Two sources with a shared tag; files on disk for both."""
    yt = ingest(admin, domain="youtube.com", creator="alice", tags=["cats", "shared"], vid="y1")
    vm = ingest(admin, domain="vimeo.com", creator="bob", tags=["dogs", "shared"], vid="v1")
    for dom, vid in (("youtube.com", "y1"), ("vimeo.com", "v1")):
        for root, ext in (("videos", "mp4"), ("posters", "jpg")):
            f = env["media"] / root / dom / f"{vid}.{ext}"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"x" * 10)
    yt_src = admin.get(f"/api/videos/{yt}").json()["source"]["id"]
    vm_src = admin.get(f"/api/videos/{vm}").json()["source"]["id"]
    return yt, vm, yt_src, vm_src


def _new_client(client):
    return TestClient(client.app)


def _as(client, username, password):
    c = _new_client(client)
    login(c, username, password)
    return c


def test_admin_creates_users_and_admins(admin):
    r = admin.post("/api/admin/users", json={"username": "carol", "password": "short"})
    assert r.status_code == 400
    assert admin.post("/api/admin/users", json={"username": "bad name!", "password": "longenough"}).status_code == 400
    r = admin.post("/api/admin/users", json={"username": "carol", "password": "longenough"})
    assert r.status_code == 201 and r.json()["is_admin"] is False and r.json()["all_sources"] is True
    assert admin.post("/api/admin/users", json={"username": "Carol", "password": "longenough"}).status_code == 409
    r = admin.post("/api/admin/users", json={"username": "dave", "password": "longenough", "is_admin": True})
    assert r.json()["is_admin"] is True
    names = [u["username"] for u in admin.get("/api/admin/users").json()["items"]]
    assert set(names) == {"admin", "carol", "dave"}
    me = next(u for u in admin.get("/api/admin/users").json()["items"] if u["is_me"])
    assert me["username"] == "admin"
    # the new admin can manage users; a standard user can't
    dave = _as(admin, "dave", "longenough")
    assert dave.get("/api/admin/users").status_code == 200
    carol = _as(admin, "carol", "longenough")
    assert carol.get("/api/admin/users").status_code == 403
    assert carol.post("/api/admin/users", json={"username": "x", "password": "longenough"}).status_code == 403
    assert carol.get("/api/collections").status_code == 200  # has their own system collections


def test_admin_guards(admin):
    me = next(u for u in admin.get("/api/admin/users").json()["items"] if u["is_me"])
    assert admin.delete(f"/api/admin/users/{me['id']}").status_code == 400
    assert admin.patch(f"/api/admin/users/{me['id']}", json={"is_admin": False}).status_code == 400
    assert admin.patch(f"/api/admin/users/{me['id']}", json={"is_active": False}).status_code == 400
    other = admin.post("/api/admin/users", json={"username": "eve", "password": "longenough", "is_admin": True}).json()
    # with a second admin, the first can be demoted by the second
    eve = _as(admin, "eve", "longenough")
    assert eve.patch(f"/api/admin/users/{me['id']}", json={"is_admin": False}).status_code == 200
    # but eve is now the last admin
    admin2 = _as(admin, "eve", "longenough")
    assert admin2.patch(f"/api/admin/users/{other['id']}", json={"is_admin": False}).status_code == 400


def test_disable_and_password_reset_log_out(admin):
    u = admin.post("/api/admin/users", json={"username": "frank", "password": "longenough"}).json()
    frank = _as(admin, "frank", "longenough")
    assert frank.get("/api/auth/me").json()["user"]["username"] == "frank"
    admin.patch(f"/api/admin/users/{u['id']}", json={"password": "newpassword1"})
    assert frank.get("/api/auth/me").json()["user"] is None  # session ended
    c = _new_client(admin)
    assert c.post("/api/auth/login", json={"username": "frank", "password": "longenough"}).status_code == 401
    frank = _as(admin, "frank", "newpassword1")
    admin.patch(f"/api/admin/users/{u['id']}", json={"is_active": False})
    assert frank.get("/api/auth/me").json()["user"] is None
    assert c.post("/api/auth/login", json={"username": "frank", "password": "newpassword1"}).status_code == 401
    admin.patch(f"/api/admin/users/{u['id']}", json={"is_active": True})
    _as(admin, "frank", "newpassword1")
    assert admin.delete(f"/api/admin/users/{u['id']}").status_code == 200
    assert c.post("/api/auth/login", json={"username": "frank", "password": "newpassword1"}).status_code == 401


def test_change_own_password(admin):
    admin.post("/api/admin/users", json={"username": "gina", "password": "longenough"})
    gina = _as(admin, "gina", "longenough")
    other_device = _as(admin, "gina", "longenough")
    assert gina.post("/api/auth/password", json={"current_password": "wrong", "new_password": "x" * 9}).status_code == 400
    assert gina.post("/api/auth/password", json={"current_password": "longenough", "new_password": "short"}).status_code == 400
    assert gina.post("/api/auth/password", json={"current_password": "longenough",
                                                 "new_password": "brandnewpw"}).status_code == 200
    assert gina.get("/api/auth/me").json()["user"]["username"] == "gina"  # this device stays in
    assert other_device.get("/api/auth/me").json()["user"] is None
    _as(admin, "gina", "brandnewpw")


def test_user_sees_only_allowed_sources(admin, env):
    yt, vm, yt_src, vm_src = _setup(admin, env)
    admin.post("/api/admin/users", json={"username": "hal", "password": "longenough", "all_sources": False,
                                         "source_ids": [yt_src]})
    hal = _as(admin, "hal", "longenough")
    # feed
    assert [v["id"] for v in hal.get("/api/feed").json()["items"]] == [yt]
    # videos, ratings, favorites, collections on the other source
    assert hal.get(f"/api/videos/{vm}").status_code == 404
    assert hal.put(f"/api/videos/{vm}/rating", json={"value": 1}).status_code == 404
    assert hal.put(f"/api/videos/{vm}/favorite").status_code == 404
    col = hal.post("/api/collections", json={"name": "c"}).json()["id"]
    assert hal.post(f"/api/collections/{col}/videos", json={"video_id": vm}).status_code == 404
    # library lists: only tags/creators/sources from allowed sources, with counts for those only
    tags = {t["name"]: t["video_count"] for t in hal.get("/api/tags").json()["items"]}
    assert tags == {"cats": 1, "shared": 1}
    assert [c["handle"] for c in hal.get("/api/creators").json()["items"]] == ["alice"]
    assert [s["domain"] for s in hal.get("/api/sources").json()["items"]] == ["youtube.com"]
    # search
    g = hal.get("/api/search", params={"q": "dogs"}).json()["groups"]
    assert g["tag"]["items"] == []
    assert hal.get("/api/search", params={"q": "bob"}).json()["groups"]["creator"]["items"] == []
    # direct item and feed lookups for hidden items 404 (names don't leak)
    assert hal.get(f"/api/tags/{tag_id(admin, 'dogs')}").status_code == 404
    assert hal.get("/api/feed", params={"type": "tag", "id": tag_id(admin, "dogs")}).status_code == 404
    assert hal.get("/api/feed", params={"type": "source", "id": vm_src}).status_code == 404
    shared = hal.get("/api/feed", params={"type": "tag", "id": tag_id(admin, "shared")}).json()["items"]
    assert [v["id"] for v in shared] == [yt]
    # media files
    assert hal.get("/media/videos/youtube.com/y1.mp4").status_code == 200
    assert hal.get("/media/videos/vimeo.com/v1.mp4").status_code == 404
    assert hal.get("/media/posters/vimeo.com/v1.jpg").status_code == 404
    # admin pages still show everything to admins
    assert len(admin.get("/api/admin/videos").json()["items"]) == 2


def test_access_change_and_new_sources(admin, env):
    yt, vm, yt_src, vm_src = _setup(admin, env)
    u = admin.post("/api/admin/users", json={"username": "ivy", "password": "longenough", "all_sources": False,
                                             "source_ids": [vm_src]}).json()
    ivy = _as(admin, "ivy", "longenough")
    # a video from a brand new source: "all sources" users see it, "selected" users don't
    new = ingest(admin, domain="tiktok.com", creator="tk", tags=["new"])
    assert new in [v["id"] for v in admin.get("/api/feed").json()["items"]]
    assert [v["id"] for v in ivy.get("/api/feed").json()["items"]] == [vm]
    # categories keep items but only show visible ones
    ivy.put(f"/api/videos/{vm}/favorite")
    cat = ivy.post("/api/categories", json={"name": "c"}).json()["id"]
    ivy.post(f"/api/categories/{cat}/items", json={"item_type": "tag", "item_id": tag_id(admin, "dogs")})
    assert ivy.post(f"/api/categories/{cat}/items",
                    json={"item_type": "tag", "item_id": tag_id(admin, "cats")}).status_code == 404
    # switch access: vm hidden, yt visible
    r = admin.patch(f"/api/admin/users/{u['id']}", json={"source_ids": [yt_src]}).json()
    assert r["source_ids"] == [yt_src] and r["all_sources"] is False
    assert [v["id"] for v in ivy.get("/api/feed").json()["items"]] == [yt]
    fav = next(c for c in ivy.get("/api/collections").json()["items"] if c["system_type"] == "favorites")
    assert fav["video_count"] == 0 and ivy.get(f"/api/collections/{fav['id']}").json()["videos"] == []
    assert ivy.get(f"/api/categories/{cat}").json()["item_count"] == 0
    # back to all sources
    admin.patch(f"/api/admin/users/{u['id']}", json={"all_sources": True})
    assert len(ivy.get("/api/feed").json()["items"]) == 3
    assert fav["id"] in [c["id"] for c in ivy.get("/api/collections").json()["items"] if c["video_count"] == 1]


def test_admin_can_hide_sources_from_self(admin, env):
    yt, vm, yt_src, vm_src = _setup(admin, env)
    me = next(u for u in admin.get("/api/admin/users").json()["items"] if u["is_me"])
    assert admin.patch(f"/api/admin/users/{me['id']}", json={"all_sources": False,
                                                            "source_ids": [vm_src]}).status_code == 200
    assert [v["id"] for v in admin.get("/api/feed").json()["items"]] == [vm]
    assert [s["domain"] for s in admin.get("/api/sources").json()["items"]] == ["vimeo.com"]
    # admin pages are unaffected
    assert {s["domain"] for s in admin.get("/api/admin/sources").json()["items"]} == {"youtube.com", "vimeo.com"}
    assert admin.get(f"/api/admin/videos/{yt}").status_code == 200


def test_anonymous_access(admin, env):
    yt, vm, yt_src, vm_src = _setup(admin, env)
    anon = _new_client(admin)
    assert admin.get("/api/admin/visibility/anonymous").json() == {"all_sources": False, "source_ids": []}
    r = anon.get("/api/feed").json()  # default: visitors see nothing and are asked to log in
    assert r["items"] == [] and r["login_required"] is True
    assert anon.get(f"/api/videos/{yt}").status_code == 404 and anon.get("/api/tags").json()["items"] == []
    assert admin.get("/api/feed").json()["login_required"] is False
    admin.put("/api/admin/visibility/anonymous", json={"all_sources": True})
    r = anon.get("/api/feed").json()
    assert len(r["items"]) == 2 and r["login_required"] is False
    assert admin.put("/api/admin/visibility/anonymous", json={"all_sources": False,
                                                             "source_ids": [999]}).status_code == 400
    admin.put("/api/admin/visibility/anonymous", json={"all_sources": False, "source_ids": [vm_src]})
    assert [v["id"] for v in anon.get("/api/feed").json()["items"]] == [vm]
    assert {t["name"] for t in anon.get("/api/tags").json()["items"]} == {"dogs", "shared"}
    assert anon.get(f"/api/videos/{yt}").status_code == 404
    assert anon.get("/media/videos/youtube.com/y1.mp4").status_code == 404
    assert anon.get("/media/videos/vimeo.com/v1.mp4").status_code == 200
    # nothing at all
    admin.put("/api/admin/visibility/anonymous", json={"all_sources": False, "source_ids": []})
    assert anon.get("/api/feed").json()["items"] == [] and anon.get("/api/tags").json()["items"] == []
    assert anon.get("/api/search", params={"q": "cats"}).json()["groups"]["tag"]["items"] == []
    # logged-in users are unaffected by the visitor setting
    assert len(admin.get("/api/feed").json()["items"]) == 2
    # media responses are never shared-cacheable
    assert "private" in admin.get("/media/videos/youtube.com/y1.mp4").headers["cache-control"]


def test_seed_does_not_recreate_deleted_admin(admin):
    from vidswipe.db import new_session
    from vidswipe.services.users import seed_admin

    admin.post("/api/admin/users", json={"username": "boss", "password": "longenough", "is_admin": True})
    boss = _as(admin, "boss", "longenough")
    me = next(u for u in admin.get("/api/admin/users").json()["items"] if u["username"] == "admin")
    assert boss.delete(f"/api/admin/users/{me['id']}").status_code == 200
    s = new_session()
    seed_admin(s)  # as on the next restart
    s.close()
    assert "admin" not in [u["username"] for u in boss.get("/api/admin/users").json()["items"]]
    c = _new_client(admin)
    assert c.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PW}).status_code == 401
