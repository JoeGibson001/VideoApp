"""Tag and creator merging (DESIGN.md 9.1, Phase 4 acceptance)."""
from sqlalchemy import text

from .conftest import ingest, tag_id
from .test_ratings import all_stats


def tag_links(db):
    db.expire_all()
    return sorted(tuple(r) for r in db.execute(text(
        "SELECT vt.video_id, t.name, vt.origin_name FROM video_tags vt JOIN tags t ON t.id = vt.tag_id")).all())


def aliases(db):
    db.expire_all()
    return sorted(tuple(r) for r in db.execute(text(
        "SELECT a.alias, t.name FROM tag_aliases a LEFT JOIN tags t ON t.id = a.tag_id")).all())


def test_merge_dogs_into_dog_and_undo(admin, db):
    v1 = ingest(admin, tags=["dogs"])
    v2 = ingest(admin, tags=["dogs", "dog"])  # has both
    v3 = ingest(admin, tags=["dog"])
    v4 = ingest(admin, tags=["doggo", "park"])
    for v, val in [(v1, 1), (v2, 1), (v3, -1), (v4, 1)]:
        admin.put(f"/api/videos/{v}/rating", json={"value": val})
    cat = admin.post("/api/categories", json={"name": "Pets"}).json()["id"]
    admin.post(f"/api/categories/{cat}/items", json={"item_type": "tag", "item_id": tag_id(admin, "dogs")})
    links_before, stats_before = tag_links(db), all_stats(db)
    cat_before = admin.get(f"/api/categories/{cat}").json()["items"]["tag"]

    dog, dogs, doggo = tag_id(admin, "dog"), tag_id(admin, "dogs"), tag_id(admin, "doggo")
    prev = admin.get("/api/admin/tags/merge-preview", params={"from": f"{dogs},{doggo}", "into": dog}).json()
    assert prev["videos_affected"] == 3 and prev["videos_with_both"] == 1 and prev["categories_affected"] == 1
    assert prev["users"][0]["score_after"] == 2  # v1 +1, v2 +1, v3 -1, v4 +1
    r = admin.post("/api/admin/tags/merge", json={"from_ids": [dogs, doggo], "into_id": dog})
    assert r.status_code == 200, r.text
    merge_id = r.json()["merge_id"]

    links = tag_links(db)
    assert [l for l in links if l[1] in ("dogs", "doggo")] == []
    assert len([l for l in links if l[0] == v2 and l[1] == "dog"]) == 1
    stats_inc = all_stats(db)
    admin.post("/api/admin/recompute-stats")
    assert all_stats(db) == stats_inc
    assert ("dogs", "dog") in aliases(db) and ("doggo", "dog") in aliases(db)

    # fresh import tagged dogs gets dog, no dogs tag created
    v5 = ingest(admin, tags=["dogs"])
    assert [t["name"] for t in admin.get(f"/api/videos/{v5}").json()["tags"]] == ["dog"]
    assert tag_id(admin, "dogs") == dog  # via alias only
    assert db.execute(text("SELECT COUNT(*) FROM tags WHERE name='dogs'")).scalar() == 0
    # search finds dog via alias
    res = admin.get("/api/search", params={"q": "dogs", "types": "tag"}).json()["groups"]["tag"]["items"]
    assert res[0]["name"] == "dog" and res[0]["match_rank"] == 2

    # undo
    assert admin.post(f"/api/admin/merges/{merge_id}/undo").status_code == 200
    links_after = tag_links(db)
    assert [l for l in links_after if l[0] != v5] == links_before
    assert (v5, "dogs", "dogs") in links_after  # post-merge import moved back
    admin.post("/api/admin/recompute-stats")
    stats_after = all_stats(db)
    # same stats except new v5 (unrated) changes nothing
    assert stats_after == stats_before
    assert aliases(db) == []
    assert [t["name"] for t in admin.get(f"/api/categories/{cat}").json()["items"]["tag"]] == \
        [t["name"] for t in cat_before]
    assert tag_id(admin, "dogs") == dogs  # original id restored


def test_chained_merge_no_dangling_alias(admin, db):
    ingest(admin, tags=["dogs"])
    ingest(admin, tags=["dog"])
    ingest(admin, tags=["canine"])
    admin.post("/api/admin/tags/merge", json={"from_ids": [tag_id(admin, "dogs")], "into_id": tag_id(admin, "dog")})
    admin.post("/api/admin/tags/merge", json={"from_ids": [tag_id(admin, "dog")],
                                              "into_id": tag_id(admin, "canine")})
    al = aliases(db)
    assert all(target is not None for _, target in al)
    assert set(al) == {("dogs", "canine"), ("dog", "canine")}
    # undo order is enforced: the first merge's target (dog) is gone
    first = admin.get("/api/admin/merges").json()["items"][-1]["id"]
    assert admin.post(f"/api/admin/merges/{first}/undo").status_code == 409


def test_rename_to_existing_is_merge(admin):
    ingest(admin, tags=["color", "colour"])
    r = admin.patch(f"/api/admin/tags/{tag_id(admin, 'colour')}", json={"name": "color"})
    assert r.status_code == 409 and r.json()["detail"]["merge_into"]["name"] == "color"


def test_remove_alias_split(admin, db):
    ingest(admin, tags=["dogs"])
    ingest(admin, tags=["dog"])
    admin.post("/api/admin/tags/merge", json={"from_ids": [tag_id(admin, "dogs")], "into_id": tag_id(admin, "dog")})
    v = ingest(admin, tags=["dogs"])
    r = admin.delete("/api/admin/tags/aliases/dogs", params={"split": True}).json()
    assert r["moved"] == 2
    assert [t["name"] for t in admin.get(f"/api/videos/{v}").json()["tags"]] == ["dogs"]


def test_duplicate_suggestions(admin):
    ingest(admin, tags=["hip-hop", "hip hop", "dog", "dogs", "colour", "color", "unrelated"])
    items = admin.get("/api/admin/tags/duplicates").json()["items"]
    pairs = {frozenset((d["tag_a"]["name"], d["tag_b"]["name"])) for d in items}
    assert frozenset(("hip-hop", "hip hop")) in pairs
    assert frozenset(("dog", "dogs")) in pairs
    assert frozenset(("colour", "color")) in pairs
    d = next(d for d in items if {d["tag_a"]["name"], d["tag_b"]["name"]} == {"dog", "dogs"})
    admin.post("/api/admin/tags/duplicates/dismiss", json={"tag_a": d["tag_a"]["id"], "tag_b": d["tag_b"]["id"]})
    items = admin.get("/api/admin/tags/duplicates").json()["items"]
    assert frozenset(("dog", "dogs")) not in {frozenset((d["tag_a"]["name"], d["tag_b"]["name"])) for d in items}


def test_creator_merge_and_undo(admin, db):
    a = ingest(admin, creator="bbc_earth")
    b = ingest(admin, creator="bbcearth")
    admin.put(f"/api/videos/{a}/rating", json={"value": 1})
    admin.put(f"/api/videos/{b}/rating", json={"value": 1})
    ca = admin.get(f"/api/videos/{a}").json()["creator"]["id"]
    cb = admin.get(f"/api/videos/{b}").json()["creator"]["id"]
    m = admin.post("/api/admin/creators/merge", json={"from_ids": [ca], "into_id": cb}).json()["merge_id"]
    assert admin.get(f"/api/videos/{a}").json()["creator"]["id"] == cb
    assert admin.get(f"/api/creators/{cb}").json()["score"] == 2
    c = ingest(admin, creator="bbc_earth")  # merged handle maps to survivor
    assert admin.get(f"/api/videos/{c}").json()["creator"]["id"] == cb
    admin.post(f"/api/admin/merges/{m}/undo")
    assert admin.get(f"/api/videos/{a}").json()["creator"]["id"] == ca
    assert admin.get(f"/api/videos/{c}").json()["creator"]["id"] == ca
    assert admin.get(f"/api/creators/{cb}").json()["score"] == 1


def test_creator_merge_same_source_only(admin):
    a = ingest(admin, creator="x", domain="youtube.com")
    b = ingest(admin, creator="x", domain="vimeo.com")
    ca = admin.get(f"/api/videos/{a}").json()["creator"]["id"]
    cb = admin.get(f"/api/videos/{b}").json()["creator"]["id"]
    assert admin.post("/api/admin/creators/merge", json={"from_ids": [ca], "into_id": cb}).status_code == 400
