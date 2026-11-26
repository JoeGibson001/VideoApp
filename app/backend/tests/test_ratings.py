"""Rating rules, stats and the Liked collection (DESIGN.md 4, Phase 3/4 acceptance)."""
from sqlalchemy import text

from .conftest import ingest


def stats(db, table, key):
    db.expire_all()
    rows = db.execute(text(f"SELECT user_id, {key}, likes, dislikes, score, last_rated_at FROM {table} "
                           f"ORDER BY user_id, {key}")).all()
    return [tuple(r) for r in rows]


def all_stats(db):
    return (stats(db, "user_tag_stats", "tag_id"), stats(db, "user_creator_stats", "creator_id"),
            stats(db, "user_source_stats", "source_id"))


def liked(db):
    db.expire_all()
    return [tuple(r) for r in db.execute(text(
        "SELECT cv.video_id, cv.added_at FROM collection_videos cv JOIN collections c ON c.id = cv.collection_id "
        "WHERE c.system_type = 'liked' ORDER BY cv.video_id")).all()]


def tag_row(db, name):
    db.expire_all()
    return db.execute(text("SELECT s.likes, s.dislikes, s.score FROM user_tag_stats s JOIN tags t ON t.id = s.tag_id "
                           "WHERE t.name = :n"), {"n": name}).first()


def rate(client, vid, value):
    return client.put(f"/api/videos/{vid}/rating", json={"value": value})


def test_like_dislike_flip_clear(admin, db):
    v = ingest(admin, tags=["drift", "cars"])
    assert rate(admin, v, 1).json()["status"] == "created"
    assert tuple(tag_row(db, "drift")) == (1, 0, 1)
    assert rate(admin, v, 1).json()["status"] == "unchanged"
    assert tuple(tag_row(db, "drift")) == (1, 0, 1)
    assert rate(admin, v, -1).json()["status"] == "changed"
    assert tuple(tag_row(db, "drift")) == (0, 1, -1)
    assert admin.delete(f"/api/videos/{v}/rating").json()["status"] == "cleared"
    assert tag_row(db, "drift") is None
    c = db.execute(text("SELECT COUNT(*) FROM user_creator_stats")).scalar()
    assert c == 0


def test_recompute_matches_incremental(admin, db):
    vids = [ingest(admin, tags=t, creator=c) for t, c in [
        (["a", "b"], "x"), (["b", "c"], "x"), (["a"], "y"), (["c", "d"], "y"), (["a", "d"], "z")]]
    rate(admin, vids[0], 1)
    rate(admin, vids[1], -1)
    rate(admin, vids[2], 1)
    rate(admin, vids[2], -1)  # flip
    rate(admin, vids[3], 1)
    admin.delete(f"/api/videos/{vids[3]}/rating")  # clear
    rate(admin, vids[4], 1)
    before, liked_before = all_stats(db), liked(db)
    assert admin.post("/api/admin/recompute-stats").status_code == 200
    assert all_stats(db) == before
    assert liked(db) == liked_before


def test_liked_collection_sync_and_favorites(admin, db):
    v = ingest(admin)
    cols = {c["system_type"]: c for c in admin.get("/api/collections").json()["items"]}
    fav, lk = cols["favorites"]["id"], cols["liked"]["id"]
    # heart does not rate
    assert admin.put(f"/api/videos/{v}/favorite").json()["is_favorite"] is True
    assert admin.get(f"/api/videos/{v}").json()["my_rating"] is None
    assert [x["id"] for x in admin.get(f"/api/collections/{fav}").json()["videos"]] == [v]
    assert admin.get(f"/api/collections/{lk}").json()["videos"] == []
    # like adds to Liked, not to Favorites again
    rate(admin, v, 1)
    assert [x["id"] for x in admin.get(f"/api/collections/{lk}").json()["videos"]] == [v]
    # flip removes
    rate(admin, v, -1)
    assert admin.get(f"/api/collections/{lk}").json()["videos"] == []
    rate(admin, v, 1)
    admin.delete(f"/api/videos/{v}/rating")
    assert admin.get(f"/api/collections/{lk}").json()["videos"] == []
    # cannot manually add to liked
    assert admin.post(f"/api/collections/{lk}/videos", json={"video_id": v}).status_code == 400
    # removing from Liked clears the rating
    rate(admin, v, 1)
    admin.delete(f"/api/collections/{lk}/videos/{v}")
    assert admin.get(f"/api/videos/{v}").json()["my_rating"] is None
    # favorites still has it throughout
    assert [x["id"] for x in admin.get(f"/api/collections/{fav}").json()["videos"]] == [v]


def test_liked_order_most_recent_first(admin):
    a, b = ingest(admin), ingest(admin)
    rate(admin, a, 1)
    rate(admin, b, 1)
    lk = next(c for c in admin.get("/api/collections").json()["items"] if c["system_type"] == "liked")
    assert [x["id"] for x in admin.get(f"/api/collections/{lk['id']}").json()["videos"]] == [b, a]
    feed = admin.get("/api/feed", params={"type": "collection", "id": lk["id"]}).json()["items"]
    assert [x["id"] for x in feed] == [b, a]


def test_system_collections_protected(admin):
    for c in admin.get("/api/collections").json()["items"]:
        assert admin.patch(f"/api/collections/{c['id']}", json={"name": "x"}).status_code == 400
        assert admin.delete(f"/api/collections/{c['id']}").status_code == 400


def test_tag_edit_recomputes_stats(admin, db):
    v = ingest(admin, tags=["one"])
    rate(admin, v, 1)
    admin.patch(f"/api/admin/videos/{v}", json={"tags": ["one", "two"]})
    assert tuple(tag_row(db, "two")) == (1, 0, 1)
    admin.patch(f"/api/admin/videos/{v}", json={"tags": ["two"]})
    assert tag_row(db, "one") is None
