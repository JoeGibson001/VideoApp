"""Source URL templates, per-source tag lists/URLs and tag/creator watches via App-side links."""
import pytest
from fastapi import HTTPException

from .conftest import ingest, make_payload, INGEST_KEY, tag_id


class FakeImporter:
    """In-memory stand-in for the Importer's /api/watches endpoints."""

    def __init__(self):
        self.watches: dict[int, dict] = {}
        self.next_id = 1
        self.online = True

    def request(self, method, path, allow=(), params=None, json=None, **_):
        if not self.online:
            raise HTTPException(503, "Importer offline")
        parts = path.strip("/").split("/")  # api, watches, [id]
        if parts[:2] != ["api", "watches"]:
            raise HTTPException(404, "not faked")
        if method == "GET" and len(parts) == 2:
            return list(self.watches.values())
        if method == "POST" and len(parts) == 2:
            for w in self.watches.values():
                if w["url"].rstrip("/") == json["url"].rstrip("/"):
                    if 409 in allow:
                        return {"detail": "duplicate", "watch_id": w["id"]}
                    raise HTTPException(409, "duplicate")
            w = {"id": self.next_id, "url": json["url"], "name": json.get("name"), "tags": json.get("tags", []),
                 "enabled": True, "erroring": False, "creator_source_domain": None, "creator_handle": None,
                 "initial_mode": json.get("initial_mode"), "filters": json.get("filters", {})}
            self.watches[w["id"]] = w
            self.next_id += 1
            return w
        wid = int(parts[2])
        if wid not in self.watches:
            raise HTTPException(404, "Watch not found")
        if method == "GET":
            return self.watches[wid]
        if method == "PATCH":
            self.watches[wid]["enabled"] = json.get("enabled", self.watches[wid]["enabled"])
            return self.watches[wid]
        raise HTTPException(405, "not faked")


@pytest.fixture
def fake(monkeypatch, client):
    from vidswipe.services import importer_client

    f = FakeImporter()
    monkeypatch.setattr(importer_client, "request", f.request)
    importer_client.invalidate_watch_cache()
    return f


def _payload_no_url(**kw):
    p = make_payload(**kw)
    p["creator"]["profile_url"] = None
    return p


def _ingest_raw(admin, p):
    r = admin.post("/api/internal/ingest", json=p, headers={"X-Ingest-Key": INGEST_KEY})
    assert r.status_code == 200, r.text
    return r.json()["video_id"]


def source_id(admin, vid):
    return admin.get(f"/api/videos/{vid}").json()["source"]["id"]


def test_template_validation(admin):
    v = ingest(admin, domain="example.com")
    sid = source_id(admin, v)
    assert admin.patch(f"/api/admin/sources/{sid}", json={"creator_url_template": "/users/"}).status_code == 400
    assert admin.patch(f"/api/admin/sources/{sid}", json={"creator_url_template": "/u/{nope}{handle}"}).status_code == 400
    assert admin.patch(f"/api/admin/sources/{sid}", json={"tag_url_template": "/browse?tags={handle}"}).status_code == 400
    r = admin.patch(f"/api/admin/sources/{sid}", json={"creator_url_template": "/users/{handle_bare}",
                                                      "tag_url_template": "/browse?tags={tag_plus}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["example_creator_url"] == "https://example.com/users/example"
    assert body["example_tag_url"] == "https://example.com/browse?tags=example+tag"
    # full URLs are used as-is; "" clears
    r = admin.patch(f"/api/admin/sources/{sid}", json={"creator_url_template": "https://www.example.com/@{handle_bare}"})
    assert r.json()["example_creator_url"] == "https://www.example.com/@example"
    assert admin.patch(f"/api/admin/sources/{sid}", json={"tag_url_template": ""}).json()["tag_url_template"] is None


def test_apply_creator_urls_and_ingest_uses_template(admin):
    a = _ingest_raw(admin, _payload_no_url(domain="example.com", creator="alice"))
    _ingest_raw(admin, make_payload(domain="example.com", creator="bob"))  # has its own profile URL
    other = _ingest_raw(admin, make_payload(domain="other.com", creator="zed"))
    sid = source_id(admin, a)

    def urls_now():
        return {c["handle"]: c["profile_url"] for c in admin.get("/api/admin/creators").json()["items"]}

    # saving a template applies it to every existing creator of the source, own URLs included
    r = admin.patch(f"/api/admin/sources/{sid}", json={"creator_url_template": "/users/{handle}"}).json()
    assert r["creators_updated"] == 2
    assert urls_now() == {"alice": "https://example.com/users/alice", "bob": "https://example.com/users/bob",
                          "zed": "https://other.com/@zed"}  # other sources untouched
    # changing it re-applies; saving the same template again changes nothing
    r = admin.patch(f"/api/admin/sources/{sid}", json={"creator_url_template": "/u/{handle_bare}"}).json()
    assert r["creators_updated"] == 2 and urls_now()["bob"] == "https://example.com/u/bob"
    assert admin.patch(f"/api/admin/sources/{sid}", json={"creator_url_template": "/u/{handle_bare}"}).json()[
        "creators_updated"] == 0
    # new creators follow the template at ingest, even when yt-dlp reports a URL
    c = _ingest_raw(admin, make_payload(domain="example.com", creator="@carol"))
    cid = admin.get(f"/api/videos/{c}").json()["creator"]["id"]
    assert admin.get(f"/api/creators/{cid}").json()["profile_url"] == "https://example.com/u/carol"
    # clearing removes the URLs the template made, but keeps ones edited by hand
    admin.patch(f"/api/admin/creators/{cid}", json={"profile_url": "https://example.com/custom/carol"})
    r = admin.patch(f"/api/admin/sources/{sid}", json={"creator_url_template": ""}).json()
    assert r["creators_updated"] == 2
    assert urls_now() == {"alice": None, "bob": None, "@carol": "https://example.com/custom/carol",
                          "zed": "https://other.com/@zed"}
    assert other


def test_source_tag_list_and_custom_urls(admin, fake):
    a = ingest(admin, domain="example.com", tags=["Big Cats", "dogs"])
    ingest(admin, domain="example.com", tags=["big cats"])
    ingest(admin, domain="other.com", tags=["dogs", "birds"])
    sid = source_id(admin, a)
    admin.patch(f"/api/admin/sources/{sid}", json={"tag_url_template": "/browse?tags={tag_plus}"})
    r = admin.get(f"/api/admin/sources/{sid}/tags").json()
    rows = {t["name"]: t for t in r["items"]}
    assert set(rows) == {"big cats", "dogs"}  # birds is only on the other source
    assert rows["big cats"]["video_count"] == 2 and rows["dogs"]["video_count"] == 1
    assert r["items"][0]["name"] == "big cats"  # most videos first
    assert rows["big cats"]["url"] == "https://example.com/browse?tags=big+cats"
    assert rows["big cats"]["watch"]["state"] == "none"
    # override one tag's URL, then clear it again
    tid = rows["dogs"]["id"]
    assert admin.put(f"/api/admin/sources/{sid}/tags/{tid}", json={"url": "not a url"}).status_code == 400
    r = admin.put(f"/api/admin/sources/{sid}/tags/{tid}", json={"url": "https://example.com/c/dogs-and-puppies"}).json()
    assert r["url"] == "https://example.com/c/dogs-and-puppies"
    assert admin.get(f"/api/admin/sources/{sid}/tags", params={"q": "dog"}).json()["items"][0]["custom_url"]
    r = admin.put(f"/api/admin/sources/{sid}/tags/{tid}", json={"url": ""}).json()
    assert r["custom_url"] is None and r["url"] == "https://example.com/browse?tags=dogs"


def test_tag_watch_toggle(admin, fake):
    a = ingest(admin, domain="example.com", tags=["drift"])
    sid, tid = source_id(admin, a), tag_id(admin, "drift")
    base = f"/api/admin/sources/{sid}/tags/{tid}/watch"
    # no URL yet
    assert admin.post(base, json={"enabled": True}).status_code == 400
    admin.patch(f"/api/admin/sources/{sid}", json={"tag_url_template": "/tags/{tag_dash}"})
    st = admin.post(base, json={"enabled": True, "initial_mode": "backfill_latest", "backfill_count": 5}).json()
    assert st["state"] == "watching" and len(st["watches"]) == 1
    w = st["watches"][0]
    assert w["url"] == "https://example.com/tags/drift" and w["tags"] == ["drift"]
    assert w["initial_mode"] == "backfill_latest"
    assert admin.get(f"/api/admin/sources/{sid}/tags").json()["items"][0]["watch"]["state"] == "watching"
    # off pauses, on resumes the same watch
    assert admin.post(base, json={"enabled": False}).json()["state"] == "paused"
    st = admin.post(base, json={"enabled": True, "resume_mode": "only_new"}).json()
    assert st["state"] == "watching" and [x["id"] for x in st["watches"]] == [w["id"]]
    assert len(fake.watches) == 1
    # extra tags replace the default
    tid2 = tag_id(admin, "drift")
    assert tid2 == tid
    # deleting the watch in the Importer drops the link
    fake.watches.clear()
    from vidswipe.services import importer_client

    importer_client.invalidate_watch_cache()
    assert admin.get(base).json()["state"] == "none"
    # offline
    fake.online = False
    importer_client.invalidate_watch_cache()
    assert admin.get(base).json()["state"] == "offline"
    assert admin.post(base, json={"enabled": True}).status_code == 503


def test_tag_watch_reuses_existing_watch_for_same_url(admin, fake):
    a = ingest(admin, domain="example.com", tags=["drift"])
    sid, tid = source_id(admin, a), tag_id(admin, "drift")
    fake.request("POST", "/api/watches", json={"url": "https://example.com/tags/drift"})
    admin.patch(f"/api/admin/sources/{sid}", json={"tag_url_template": "/tags/{tag}"})
    st = admin.post(f"/api/admin/sources/{sid}/tags/{tid}/watch", json={"enabled": True}).json()
    assert st["state"] == "watching" and len(fake.watches) == 1


def test_creator_watch_from_template_url_is_linked(admin, fake):
    a = _ingest_raw(admin, _payload_no_url(domain="example.com", creator="alice"))
    sid = source_id(admin, a)
    cid = admin.get(f"/api/videos/{a}").json()["creator"]["id"]
    assert admin.post(f"/api/admin/creators/{cid}/watch", json={"enabled": True}).status_code == 400  # no URL
    admin.patch(f"/api/admin/sources/{sid}", json={"creator_url_template": "/users/{handle}"})
    st = admin.post(f"/api/admin/creators/{cid}/watch", json={"enabled": True}).json()
    # the fake importer sets no creator handle, so this only works through the App's link
    assert st["state"] == "watching" and st["watches"][0]["url"] == "https://example.com/users/alice"
    assert admin.get(f"/api/admin/creators/{cid}/watch").json()["state"] == "watching"
    assert admin.post(f"/api/admin/creators/{cid}/watch", json={"enabled": False}).json()["state"] == "paused"
    items = admin.get("/api/creators").json()["items"]
    assert [i["watched"] for i in items] == [False]
    admin.post(f"/api/admin/creators/{cid}/watch", json={"enabled": True, "resume_mode": "only_new"})
    from vidswipe.services import importer_client

    importer_client.invalidate_watch_cache()
    assert admin.get("/api/creators", params={"watched_only": True}).json()["items"][0]["id"] == cid


def test_tag_merge_keeps_source_tag_url_and_watch(admin, fake):
    a = ingest(admin, domain="example.com", tags=["dogs", "dog"])
    sid = source_id(admin, a)
    dogs, dog = tag_id(admin, "dogs"), tag_id(admin, "dog")
    admin.put(f"/api/admin/sources/{sid}/tags/{dogs}", json={"url": "https://example.com/c/dogs"})
    admin.post(f"/api/admin/sources/{sid}/tags/{dogs}/watch", json={"enabled": True})
    admin.post("/api/admin/tags/merge", json={"from_ids": [dogs], "into_id": dog})
    rows = admin.get(f"/api/admin/sources/{sid}/tags").json()["items"]
    assert [(r["name"], r["custom_url"], r["watch"]["state"]) for r in rows] == [
        ("dog", "https://example.com/c/dogs", "watching")]


def test_delete_source_removes_links(admin, fake, db):
    from sqlalchemy import text

    a = ingest(admin, domain="example.com", tags=["drift"])
    sid, tid = source_id(admin, a), tag_id(admin, "drift")
    admin.patch(f"/api/admin/sources/{sid}", json={"tag_url_template": "/t/{tag}"})
    admin.put(f"/api/admin/sources/{sid}/tags/{tid}", json={"url": "https://example.com/x"})
    admin.post(f"/api/admin/sources/{sid}/tags/{tid}/watch", json={"enabled": True})
    assert admin.delete(f"/api/admin/sources/{sid}").status_code == 200
    db.expire_all()
    assert db.execute(text("SELECT COUNT(*) FROM source_tags")).scalar() == 0
    assert db.execute(text("SELECT COUNT(*) FROM watch_links")).scalar() == 0
