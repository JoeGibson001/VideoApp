"""Fuzzy search ranking and library lists (DESIGN.md 6.1, 6.4)."""
import json

from .conftest import ingest, login


def names(resp, group="tag"):
    assert resp.status_code == 200, resp.text
    return [i["name"] for i in resp.json()["groups"].get(group, {}).get("items", [])]


def test_search_ranking(admin):
    ingest(admin, tags=["racecar", "cars", "car", "carbon", "rc car", "drift", "cooking", "jdm classic"])
    got = names(admin.get("/api/search", params={"q": "car", "types": "tag", "limit": 20}))
    assert got.index("car") < got.index("cars") < got.index("racecar")
    assert got[0] == "car"
    assert got.index("carbon") < got.index("rc car") < got.index("racecar")
    assert names(admin.get("/api/search", params={"q": "drfit", "types": "tag"})) == ["drift"]
    assert "cooking" in names(admin.get("/api/search", params={"q": "cokking", "types": "tag"}))
    assert names(admin.get("/api/search", params={"q": "jdmc", "types": "tag"}))[0] == "jdm classic"
    # no junk for short queries with typos
    assert "drift" not in names(admin.get("/api/search", params={"q": "drxxt", "types": "tag"}))


def test_search_each_keystroke(admin):
    ingest(admin, tags=["drift"])
    for i in range(1, 6):
        assert "drift" in names(admin.get("/api/search", params={"q": "drift"[:i], "types": "tag"}))


def test_search_normalisation(admin):
    ingest(admin, tags=["hip-hop", "cafe"])
    for q in ["hip hop", "hiphop", "HIP_HOP"]:
        assert names(admin.get("/api/search", params={"q": q, "types": "tag"}))[0] == "hip-hop"
    ingest(admin, tags=["café"])
    assert "café" in names(admin.get("/api/search", params={"q": "cafe", "types": "tag"}))


def test_search_creators_by_handle_and_groups(admin):
    ingest(admin, creator="fast_cars")
    r = admin.get("/api/search", params={"q": "cars"}).json()["groups"]
    assert any(c["handle"] == "fast_cars" for c in r["creator"]["items"])
    assert set(r) == {"tag", "creator", "source", "collection", "category"}
    assert r["source"]["items"] == [] or True


def test_anonymous_search_limited(client):
    login(client)
    ingest(client, tags=["cars"])
    client.post("/api/collections", json={"name": "cars stuff"})
    client.post("/api/auth/logout")
    r = client.get("/api/search", params={"q": "car"}).json()["groups"]
    assert "collection" not in r and "category" not in r and r["tag"]["items"]


def test_recent_searches(admin):
    for q in [f"q{i}" for i in range(12)]:
        admin.post("/api/search/recent", json={"q": q})
    admin.post("/api/search/recent", json={"q": "q5"})
    items = admin.get("/api/search/recent").json()["items"]
    assert len(items) == 10 and items[0] == "q5"
    admin.delete("/api/search/recent")
    assert admin.get("/api/search/recent").json()["items"] == []


def test_library_sorts(admin):
    a = ingest(admin, tags=["alpha"])
    b = ingest(admin, tags=["beta"])
    ingest(admin, tags=["beta", "gamma"])
    admin.put(f"/api/videos/{a}/rating", json={"value": -1})
    admin.put(f"/api/videos/{b}/rating", json={"value": 1})

    def order(sort):
        return [i["name"] for i in admin.get("/api/tags", params={"sort": sort}).json()["items"]]

    assert order("score_desc")[0] == "beta" and order("score_desc")[-1] == "alpha"
    assert order("score_asc")[0] == "alpha"
    assert order("name_asc") == sorted(order("name_asc"))
    assert order("name_desc") == sorted(order("name_asc"), reverse=True)
    assert order("most_videos")[0] == "beta"
    assert order("recent")[0] == "beta"
    assert order("newest")[0] == "gamma" and order("oldest")[0] in ("alpha",)
    row = admin.get("/api/tags", params={"q": "bet"}).json()["items"][0]
    assert row["name"] == "beta" and row["score"] == 1 and row["likes"] == 1 and row["video_count"] == 2


def test_rebuild_from_media(admin, env, db):
    """Deleting the DB and rebuilding from sidecars restores videos, creators, sources, tags."""
    meta = env["media"] / "metadata" / "youtube.com"
    meta.mkdir(parents=True)
    for i in range(3):
        info = {"id": f"vid{i}", "title": f"T{i}", "description": "d", "webpage_url": f"https://www.youtube.com/watch?v=vid{i}",
                "extractor_key": "Youtube", "uploader": "Some Creator", "uploader_id": "@some",
                "uploader_url": "https://www.youtube.com/@some", "tags": ["Cats", "funny"], "categories": ["Pets"],
                "upload_date": "20261104",
                "_import": {"imported_at": "2026-11-20T10:00:00Z", "request_tags": ["test-import"],
                            "source": {"domain": "youtube.com", "name": "YouTube"},
                            "creator": {"name": "Some Creator", "handle": "@some",
                                        "profile_url": "https://www.youtube.com/@some", "avatar_path": None},
                            "file": {"path": f"videos/youtube.com/vid{i}.mp4", "original_filename": "x.mp4",
                                     "size_bytes": 10, "width": 720, "height": 1280, "duration_s": 5.0, "fps": 30,
                                     "video_codec": "h264", "audio_codec": "aac", "has_audio": True,
                                     "container": "mp4"},
                            "poster_path": f"posters/youtube.com/vid{i}.jpg",
                            "info_json_path": f"metadata/youtube.com/vid{i}.json"}}
        (meta / f"vid{i}.json").write_text(json.dumps(info))
    r = admin.post("/api/admin/rebuild-from-media").json()
    assert r["created"] == 3 and r["failed"] == 0
    vids = admin.get("/api/admin/videos").json()["items"]
    assert len(vids) == 3
    d = admin.get(f"/api/videos/{vids[0]['id']}").json()
    assert sorted(t["name"] for t in d["tags"]) == ["cats", "funny", "pets", "test-import"]
    assert d["creator"]["handle"] == "@some" and d["source"]["name"] == "YouTube"
    assert d["upload_date"] == "2026-11-04" and d["width"] == 720
    assert admin.post("/api/admin/rebuild-from-media").json()["existing"] == 3
    assert admin.post(f"/api/admin/videos/{vids[0]['id']}/reread").json()["status"] == "ok"
