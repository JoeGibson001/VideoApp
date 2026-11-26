"""Feed exclusion rules, filtered feeds and personalisation (DESIGN.md 5.4, 5.5)."""
from .conftest import ingest


def ids(resp):
    assert resp.status_code == 200, resp.text
    return [v["id"] for v in resp.json()["items"]]


def test_main_feed_excludes_rated(admin):
    a, b, c = ingest(admin), ingest(admin), ingest(admin)
    admin.put(f"/api/videos/{a}/rating", json={"value": 1})
    admin.put(f"/api/videos/{b}/rating", json={"value": -1})
    assert ids(admin.get("/api/feed")) == [c]


def test_exclude_param_and_pagination(admin):
    vids = {ingest(admin) for _ in range(7)}
    first = ids(admin.get("/api/feed", params={"limit": 4}))
    second = ids(admin.get("/api/feed", params={"limit": 4, "exclude": ",".join(map(str, first))}))
    assert len(first) == 4 and len(second) == 3
    assert set(first) | set(second) == vids


def test_tag_feed_includes_rated_and_unrated_only(admin):
    a, b = ingest(admin, tags=["drift"]), ingest(admin, tags=["drift"])
    ingest(admin, tags=["ski"])
    admin.put(f"/api/videos/{a}/rating", json={"value": 1})
    tid = admin.get("/api/admin/tags/resolve", params={"name": "drift"}).json()["tag"]["id"]
    assert set(ids(admin.get("/api/feed", params={"type": "tag", "id": tid}))) == {a, b}
    assert ids(admin.get("/api/feed", params={"type": "tag", "id": tid, "unrated_only": True})) == [b]


def test_creator_feed_newest_first(admin):
    a = ingest(admin, creator="zed")
    b = ingest(admin, creator="zed")
    cid = admin.get(f"/api/videos/{a}").json()["creator"]["id"]
    got = ids(admin.get("/api/feed", params={"type": "creator", "id": cid}))
    assert set(got) == {a, b}


def test_category_union_no_duplicates(admin):
    a = ingest(admin, tags=["drift"], creator="alice")  # matches both tag and creator
    b = ingest(admin, tags=["drift"], creator="bob")
    c = ingest(admin, tags=["ski"], creator="alice")
    ingest(admin, tags=["ski"], creator="bob")
    tid = admin.get("/api/admin/tags/resolve", params={"name": "drift"}).json()["tag"]["id"]
    cid = admin.get(f"/api/videos/{a}").json()["creator"]["id"]
    cat = admin.post("/api/categories", json={"name": "Mix"}).json()["id"]
    admin.post(f"/api/categories/{cat}/items", json={"item_type": "tag", "item_id": tid})
    admin.post(f"/api/categories/{cat}/items", json={"item_type": "creator", "item_id": cid})
    got = ids(admin.get("/api/feed", params={"type": "category", "id": cat, "limit": 50}))
    assert sorted(got) == sorted([a, b, c])
    assert len(got) == len(set(got))


def test_personalisation_raises_liked_tag(admin, monkeypatch):
    monkeypatch.setenv("FEED_EXPLORE_EVERY", "0")
    from vidswipe.config import get_settings

    get_settings.cache_clear()
    liked = [ingest(admin, tags=["cats"], creator=f"c{i}") for i in range(4)]
    for v in liked:
        admin.put(f"/api/videos/{v}/rating", json={"value": 1})
    cats_new = [ingest(admin, tags=["cats"], creator=f"n{i}") for i in range(3)]
    for i in range(6):
        ingest(admin, tags=["other"], creator=f"o{i}")
    got = ids(admin.get("/api/feed", params={"limit": 3}))
    assert set(got) == set(cats_new)


def test_anonymous_feed_and_restrictions(client):
    from .conftest import login

    login(client)
    v = ingest(client)
    client.put("/api/admin/visibility/anonymous", json={"all_sources": True})
    client.post("/api/auth/logout")
    client.headers.pop("X-CSRF-Token", None)
    assert ids(client.get("/api/feed")) == [v]
    assert client.put(f"/api/videos/{v}/rating", json={"value": 1}).status_code == 401
    assert client.get("/api/collections").status_code == 401
    assert client.get("/api/feed", params={"type": "collection", "id": 1}).status_code == 401
    lst = client.get("/api/tags").json()["items"]
    assert "score" not in lst[0]


def test_hidden_videos_excluded(admin):
    a, b = ingest(admin), ingest(admin)
    admin.patch(f"/api/admin/videos/{a}", json={"is_hidden": True})
    assert ids(admin.get("/api/feed")) == [b]
