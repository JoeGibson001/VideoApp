"""Deleting whole sources / creators / tags with their videos, and empty-item cleanup."""
from sqlalchemy import text

from .conftest import ingest, tag_id
from .test_ratings import all_stats


def count(db, table):
    db.expire_all()
    return db.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()


def media_files(env, vid_id):
    """Create the media files the fixture payload points at, so deletion can be checked."""
    paths = [f"videos/youtube.com/{vid_id}.mp4", f"posters/youtube.com/{vid_id}.jpg",
             f"metadata/youtube.com/{vid_id}.json"]
    for p in paths:
        f = env["media"] / p
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x")
    return [env["media"] / p for p in paths]


def creator_of(admin, vid):
    return admin.get(f"/api/videos/{vid}").json()["creator"]["id"]


def source_of(admin, vid):
    return admin.get(f"/api/videos/{vid}").json()["source"]["id"]


def test_delete_source_removes_videos_creators_and_files(admin, db, env):
    files = media_files(env, "yt1")
    a = ingest(admin, vid="yt1", creator="alice", tags=["cats"])
    b = ingest(admin, creator="bob", tags=["cats"])
    keep = ingest(admin, domain="vimeo.com", creator="carol", tags=["cats"])
    admin.put(f"/api/videos/{a}/rating", json={"value": 1})
    admin.put(f"/api/videos/{keep}/rating", json={"value": 1})
    src = source_of(admin, a)
    cat = admin.post("/api/categories", json={"name": "c"}).json()["id"]
    admin.post(f"/api/categories/{cat}/items", json={"item_type": "source", "item_id": src})
    admin.post(f"/api/categories/{cat}/items", json={"item_type": "creator", "item_id": creator_of(admin, b)})

    prev = admin.get(f"/api/admin/sources/{src}/delete-preview").json()
    assert prev["videos"] == 2 and prev["creators"] == 2
    r = admin.delete(f"/api/admin/sources/{src}").json()
    assert r["videos_deleted"] == 2 and r["creators_deleted"] == 2 and r["sources_deleted"] == 1
    assert admin.get(f"/api/videos/{a}").status_code == 404
    assert admin.get(f"/api/videos/{keep}").status_code == 200
    assert not any(f.exists() for f in files)
    assert admin.get(f"/api/categories/{cat}").json()["item_count"] == 0
    assert [c["name"] for c in admin.get("/api/creators").json()["items"]] == ["Carol"]
    # stats and Liked stay consistent with a full recompute
    before = all_stats(db)
    admin.post("/api/admin/recompute-stats")
    assert all_stats(db) == before
    assert admin.get("/api/tags", params={"q": "cats"}).json()["items"][0]["score"] == 1
    lk = next(c for c in admin.get("/api/collections").json()["items"] if c["system_type"] == "liked")
    assert [v["id"] for v in admin.get(f"/api/collections/{lk['id']}").json()["videos"]] == [keep]


def test_delete_creator_with_videos(admin, db):
    a = ingest(admin, creator="alice")
    ingest(admin, creator="alice")
    other = ingest(admin, creator="bob")
    cid = creator_of(admin, a)
    assert admin.get(f"/api/admin/creators/{cid}/delete-preview").json()["videos"] == 2
    r = admin.delete(f"/api/admin/creators/{cid}").json()
    assert r["videos_deleted"] == 2 and r["creators_deleted"] == 1
    assert admin.get(f"/api/creators/{cid}").status_code == 404
    assert [v["id"] for v in admin.get("/api/feed").json()["items"]] == [other]
    # a new import from that handle creates a fresh creator rather than failing
    again = ingest(admin, creator="alice")
    assert creator_of(admin, again) != cid


def test_delete_tag_with_and_without_videos(admin, db):
    a = ingest(admin, tags=["drift", "cars"])
    b = ingest(admin, tags=["drift"])
    c = ingest(admin, tags=["cars"])
    # without videos: only the tag goes
    admin.delete(f"/api/admin/tags/{tag_id(admin, 'cars')}")
    assert admin.get(f"/api/videos/{c}").status_code == 200
    assert [t["name"] for t in admin.get(f"/api/videos/{a}").json()["tags"]] == ["drift"]
    # with videos: every video carrying the tag is deleted
    tid = tag_id(admin, "drift")
    assert admin.get(f"/api/admin/tags/{tid}/delete-preview").json()["videos"] == 2
    r = admin.delete(f"/api/admin/tags/{tid}", params={"delete_videos": True}).json()
    assert r["videos_deleted"] == 2
    assert admin.get(f"/api/videos/{a}").status_code == 404 and admin.get(f"/api/videos/{b}").status_code == 404
    assert admin.get(f"/api/videos/{c}").status_code == 200


def test_shared_file_kept_while_still_used(admin, env):
    p = env["media"] / "posters" / "shared.jpg"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    from .conftest import INGEST_KEY, make_payload

    ids = []
    for creator in ("x", "y"):
        pl = make_payload(creator=creator)
        pl["poster_path"] = "posters/shared.jpg"
        ids.append(admin.post("/api/internal/ingest", json=pl, headers={"X-Ingest-Key": INGEST_KEY}).json()["video_id"])
    admin.delete(f"/api/admin/creators/{creator_of(admin, ids[0])}")
    assert p.exists()
    admin.delete(f"/api/admin/creators/{creator_of(admin, ids[1])}")
    assert not p.exists()


def test_cleanup_empty_items(admin, db):
    a = ingest(admin, tags=["keep", "gone"], creator="alice")
    ingest(admin, tags=["keep"], creator="bob", domain="vimeo.com")
    # make 'gone' and bob/vimeo empty
    admin.patch(f"/api/admin/videos/{a}", json={"tags": ["keep"]})
    bob_vid = [v for v in admin.get("/api/admin/videos").json()["items"] if v["creator"]["handle"] == "bob"][0]["id"]
    admin.delete(f"/api/admin/videos/{bob_vid}")
    cat = admin.post("/api/categories", json={"name": "c"}).json()["id"]
    admin.post(f"/api/categories/{cat}/items", json={"item_type": "tag", "item_id": tag_id(admin, "gone")})

    prev = admin.get("/api/admin/cleanup").json()
    assert [t["name"] for t in prev["tags"]] == ["gone"]
    assert [c["handle"] for c in prev["creators"]] == ["bob"]
    assert [s["domain"] for s in prev["sources"]] == ["vimeo.com"]
    # preview changes nothing
    assert count(db, "tags") == 2

    r = admin.post("/api/admin/cleanup", json={}).json()
    assert r["tags_deleted"] == 1 and r["creators_deleted"] == 1 and r["sources_deleted"] == 1
    assert tag_id(admin, "gone") is None and tag_id(admin, "keep") is not None
    assert count(db, "creators") == 1 and count(db, "sources") == 1
    assert admin.get(f"/api/categories/{cat}").json()["item_count"] == 0
    assert admin.get("/api/admin/cleanup").json() == {"tags": [], "creators": [], "sources": []}


def test_cleanup_only_selected_types(admin, db):
    a = ingest(admin, tags=["x"], creator="solo", domain="vimeo.com")
    admin.delete(f"/api/admin/videos/{a}")
    r = admin.post("/api/admin/cleanup", json={"types": ["tag"]}).json()
    assert r["tags_deleted"] == 1 and r["creators_deleted"] == 0 and r["sources_deleted"] == 0
    assert count(db, "creators") == 1
    # a source with a remaining (empty) creator is not removed on its own
    r = admin.post("/api/admin/cleanup", json={"types": ["source"]}).json()
    assert r["sources_deleted"] == 0


def test_delete_endpoints_admin_only(client):
    from .conftest import login
    from vidswipe.db import new_session
    from vidswipe.services.users import create_user

    s = new_session()
    create_user(s, "bob", "bobpass")
    s.close()
    login(client, "bob", "bobpass")
    for path in ["/api/admin/sources/1", "/api/admin/creators/1", "/api/admin/tags/1"]:
        assert client.delete(path).status_code == 403
    assert client.post("/api/admin/cleanup", json={}).status_code == 403


def _touch(env, rel):
    f = env["media"] / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"x")
    return f


def test_delete_source_removes_its_media_folders(admin, env):
    media_files(env, "yt1")
    stray = _touch(env, "avatars/youtube.com/someone-not-in-db.jpg")
    nested = _touch(env, "videos/youtube.com/sub/extra.mp4")
    keep = _touch(env, "videos/vimeo.com/other.mp4")
    a = ingest(admin, vid="yt1")
    ingest(admin, domain="vimeo.com")
    r = admin.delete(f"/api/admin/sources/{source_of(admin, a)}").json()
    assert r["folders_removed"] == 2  # posters/metadata were already pruned with their last file
    assert r["leftover_files_removed"] == 2 and not stray.exists() and not nested.exists()
    for root in ("videos", "posters", "avatars", "metadata"):
        assert not (env["media"] / root / "youtube.com").exists()
        assert (env["media"] / root).is_dir()  # top-level folders always stay
    assert keep.exists()


def test_deleting_last_file_prunes_empty_folder(admin, env):
    media_files(env, "yt1")
    media_files(env, "yt2")
    a = ingest(admin, vid="yt1", creator="alice")
    b = ingest(admin, vid="yt2", creator="bob")
    admin.delete(f"/api/admin/videos/{a}")
    assert (env["media"] / "videos" / "youtube.com").is_dir()  # yt2 still there
    admin.delete(f"/api/admin/creators/{creator_of(admin, b)}")
    for root in ("videos", "posters", "metadata"):
        assert not (env["media"] / root / "youtube.com").exists()
        assert (env["media"] / root).is_dir()


def test_cleanup_prunes_only_empty_source_folders(admin, env):
    a = ingest(admin, domain="vimeo.com", creator="solo")
    (env["media"] / "posters" / "vimeo.com").mkdir(parents=True)
    stray = _touch(env, "avatars/vimeo.com/keep-me.jpg")
    admin.delete(f"/api/admin/videos/{a}")
    assert admin.post("/api/admin/cleanup", json={}).json()["sources_deleted"] == 1
    assert not (env["media"] / "posters" / "vimeo.com").exists()
    assert stray.exists()


def test_prune_never_escapes_media_root(env):
    from vidswipe.routers.media import prune_empty_dirs

    outside = env["media"].parent / "outside" / "x"
    outside.mkdir(parents=True)
    assert prune_empty_dirs(outside) == 0 and outside.exists()
    top = env["media"] / "videos"
    top.mkdir(exist_ok=True)
    assert prune_empty_dirs(top) == 0 and top.exists()


def test_wipe_all_content(admin, env, db):
    files = media_files(env, "yt1")
    _touch(env, "icons/youtube.com-1.png")
    a = ingest(admin, vid="yt1", tags=["cats"])
    ingest(admin, domain="vimeo.com", tags=["dogs"])
    admin.put(f"/api/videos/{a}/rating", json={"value": 1})
    admin.put(f"/api/videos/{a}/favorite")
    mine = admin.post("/api/collections", json={"name": "mine"}).json()["id"]
    admin.post(f"/api/collections/{mine}/videos", json={"video_id": a})
    cat = admin.post("/api/categories", json={"name": "c"}).json()["id"]
    admin.post(f"/api/categories/{cat}/items", json={"item_type": "tag", "item_id": tag_id(admin, "cats")})
    admin.post("/api/search/recent", json={"q": "cats"})

    prev = admin.get("/api/admin/wipe-all/preview").json()
    assert prev["videos"] == 2 and prev["phrase"] == "DELETE ALL"
    assert admin.post("/api/admin/wipe-all", json={"confirm": "delete"}).status_code == 400
    assert count(db, "videos") == 2  # nothing happened

    # importer is unreachable in tests: the wipe still runs and reports it
    r = admin.post("/api/admin/wipe-all", json={"confirm": "DELETE ALL"}).json()
    assert r["videos_deleted"] == 2 and r["files_deleted"] >= 4 and r["importer"]["cleared"] is False
    for t in ("videos", "creators", "sources", "tags", "ratings", "video_tags", "user_tag_stats"):
        assert count(db, t) == 0, t
    assert not any(f.exists() for f in files)
    for root in ("videos", "posters", "metadata", "icons"):
        assert (env["media"] / root).is_dir() and not any((env["media"] / root).iterdir())
    # user data kept (empty) by default; the admin can still log in
    cols = {c["name"]: c for c in admin.get("/api/collections").json()["items"]}
    assert set(cols) == {"Favorites", "Liked", "mine"} and all(c["video_count"] == 0 for c in cols.values())
    assert admin.get(f"/api/categories/{cat}").json()["item_count"] == 0
    assert admin.get("/api/feed").json()["items"] == []
    assert admin.get("/api/search", params={"q": "cats"}).json()["groups"]["tag"]["items"] == []
    # the library works again afterwards
    b = ingest(admin, tags=["fresh"])
    assert admin.get("/api/feed").json()["items"][0]["id"] == b and b > a

    r = admin.post("/api/admin/wipe-all", json={"confirm": "DELETE ALL", "include_user_data": True,
                                                "clear_importer": False}).json()
    assert r["user_data_deleted"] and "error" not in r["importer"]
    assert {c["name"] for c in admin.get("/api/collections").json()["items"]} == {"Favorites", "Liked"}
    assert admin.get("/api/categories").json()["items"] == [] and admin.get("/api/search/recent").json()["items"] == []


def test_wipe_clears_importer(admin, monkeypatch):
    from vidswipe.services import importer_client

    calls = []

    def fake(method, path, allow=(), params=None, json=None, **_):
        calls.append((method, path))
        if path == "/api/imports":
            return [{"id": 7}] if params["status"] == "queued" else []
        if path == "/api/watches":
            return [{"id": 3}, {"id": 4}]
        if path == "/api/imports/clear":
            return {"deleted": 12}
        return {}

    monkeypatch.setattr(importer_client, "request", fake)
    importer_client.invalidate_watch_cache()
    r = admin.post("/api/admin/wipe-all", json={"confirm": "DELETE ALL"}).json()
    assert r["importer"] == {"cleared": True, "jobs_cancelled": 1, "watches_deleted": 2, "history_deleted": 12}
    assert ("DELETE", "/api/imports/7") in calls and ("DELETE", "/api/watches/4") in calls
    # active imports are cancelled before history is cleared
    assert calls.index(("DELETE", "/api/imports/7")) < calls.index(("POST", "/api/imports/clear"))


def test_wipe_admin_only(client):
    from .conftest import login
    from vidswipe.db import new_session
    from vidswipe.services.users import create_user

    s = new_session()
    create_user(s, "bob", "bobpass")
    s.close()
    login(client, "bob", "bobpass")
    assert client.post("/api/admin/wipe-all", json={"confirm": "DELETE ALL"}).status_code == 403
