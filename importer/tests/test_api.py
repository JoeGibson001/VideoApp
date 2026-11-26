import time

import pytest
from fastapi.testclient import TestClient

from vsimporter.api import create_app

from .conftest import API_KEY, yt_video

H = {"X-API-Key": API_KEY}
JOB_KEYS = {"id", "url", "status", "progress", "error", "video_id", "tags", "watch_id", "watch_name", "created_at",
            "started_at", "finished_at", "title"}
WATCH_KEYS = {"id", "url", "kind", "name", "source_domain", "enabled", "paused_at", "tags", "filters", "initial_mode",
              "backfill_count", "created_at", "last_checked_at", "last_success_at", "last_new_count", "last_error",
              "consecutive_failures", "next_run_at", "creator_source_domain", "creator_handle", "imported_count",
              "erroring"}


@pytest.fixture
def client(svc):
    with TestClient(create_app(svc, background=False)) as c:
        yield c


def drain(client, svc):
    client.portal.call(svc.jobs.run_until_idle)


def test_health_is_public(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["yt_dlp_version"] == "2099.01.01"


def test_auth_required(client):
    assert client.get("/api/imports").status_code == 401
    assert client.get("/api/imports", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.post("/api/imports", json={"url": "https://x.com/v"}).status_code == 401
    assert client.get("/api/watches").status_code == 401
    assert client.get("/api/imports", headers=H).status_code == 200


def test_unconfigured_key_rejects(svc, settings):
    settings.importer_api_key = ""
    with TestClient(create_app(svc, background=False)) as c:
        assert c.get("/api/imports", headers={"X-API-Key": ""}).status_code == 503


def test_index_page(client):
    r = client.get("/")
    assert r.status_code == 200 and "VidSwipe Importer" in r.text


def test_import_lifecycle(client, svc, fakes):
    fakes.ytdlp.add_video(yt_video("api1"))
    r = client.post("/api/imports", headers=H, json={"url": "https://www.youtube.com/watch?v=api1", "tags": ["A"]})
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "queued"
    job_id = body["job_id"]
    assert JOB_KEYS <= set(body["job"])

    drain(client, svc)
    job = client.get(f"/api/imports/{job_id}", headers=H).json()
    assert JOB_KEYS <= set(job)
    assert job["status"] == "done" and job["video_id"] == 101 and job["progress"] == 100
    assert job["tags"] == ["a"] and job["title"] == "Video api1"
    assert job["created_at"].endswith("Z") and job["finished_at"].endswith("Z")

    assert [j["id"] for j in client.get("/api/imports?status=done", headers=H).json()] == [job_id]
    assert client.get("/api/imports?status=failed", headers=H).json() == []
    assert client.get("/api/imports?status=bogus", headers=H).status_code == 422
    assert client.get("/api/imports/999", headers=H).status_code == 404
    # Done jobs cannot be cancelled or retried.
    assert client.delete(f"/api/imports/{job_id}", headers=H).status_code == 409
    assert client.post(f"/api/imports/{job_id}/retry", headers=H).status_code == 409


def test_batch_and_cancel(client, svc, fakes):
    r = client.post("/api/imports/batch", headers=H,
                    json={"urls": ["https://www.youtube.com/watch?v=b1", "https://www.youtube.com/watch?v=b2"]})
    assert r.status_code == 202
    ids = r.json()["job_ids"]
    assert len(ids) == 2
    r = client.delete(f"/api/imports/{ids[0]}", headers=H)
    assert r.status_code == 200 and r.json()["status"] == "failed" and r.json()["error"] == "Cancelled"
    r = client.post(f"/api/imports/{ids[0]}/retry", headers=H)
    assert r.status_code == 202 and r.json()["status"] == "queued"


def test_bad_url_rejected(client):
    assert client.post("/api/imports", headers=H, json={"url": "ftp://nope"}).status_code == 422


def test_watch_api(client, svc, fakes):
    url = "https://www.youtube.com/@SomeCreator/shorts"
    fakes.ytdlp.set_playlist(url, [{"id": "w1", "url": "https://www.youtube.com/shorts/w1", "ie_key": "Youtube"}],
                             uploader_id="@SomeCreator", channel_id="UCabc", id="UCabc", title="Some Creator - Shorts")
    r = client.post("/api/watches", headers=H, json={"url": url, "tags": ["Shorts"], "filters": {"max_duration_s": 180}})
    assert r.status_code == 201
    w = r.json()
    assert WATCH_KEYS <= set(w)
    assert w["kind"] == "channel" and w["initial_mode"] == "new_only" and w["enabled"] is True
    assert w["creator_source_domain"] == "youtube.com" and w["creator_handle"] == "@SomeCreator"
    assert w["filters"] == {"max_duration_s": 180.0} and w["tags"] == ["shorts"]
    wid = w["id"]

    dup = client.post("/api/watches", headers=H, json={"url": "https://youtube.com/@somecreator/shorts/"})
    assert dup.status_code == 409 and dup.json()["watch_id"] == wid

    r = client.post(f"/api/watches/{wid}/run?wait=true", headers=H)
    assert r.status_code == 200 and r.json()["listed_count"] == 1 and r.json()["queued_count"] == 0
    assert client.get(f"/api/watches/{wid}", headers=H).json()["name"] == "Some Creator - Shorts"
    assert len(client.get(f"/api/watches/{wid}/runs", headers=H).json()) == 1
    its = client.get(f"/api/watches/{wid}/items", headers=H).json()
    assert [(i["source_video_id"], i["status"]) for i in its] == [("w1", "baseline")]

    # Creator filter, tolerant of the @ prefix and case.
    for h in ("@SomeCreator", "somecreator"):
        lst = client.get("/api/watches", headers=H, params={"creator_source_domain": "youtube.com", "creator_handle": h})
        assert [x["id"] for x in lst.json()] == [wid]
    assert client.get("/api/watches?creator_source_domain=youtube.com&creator_handle=@other", headers=H).json() == []

    # Pause, then resume the same watch.
    r = client.patch(f"/api/watches/{wid}", headers=H, json={"enabled": False})
    assert r.json()["enabled"] is False and r.json()["paused_at"]
    r = client.patch(f"/api/watches/{wid}", headers=H, json={"enabled": True, "resume_mode": "only_new"})
    assert r.json()["enabled"] is True and r.json()["paused_at"] is None and r.json()["id"] == wid
    assert len(client.get("/api/watches", headers=H).json()) == 1

    assert client.delete(f"/api/watches/{wid}", headers=H).status_code == 204
    assert client.get(f"/api/watches/{wid}", headers=H).status_code == 404


def test_workers_process_jobs_in_background(svc, fakes):
    fakes.ytdlp.add_video(yt_video("bg1"))
    with TestClient(create_app(svc, background=True)) as c:
        job_id = c.post("/api/imports", headers=H, json={"url": "https://www.youtube.com/watch?v=bg1"}).json()["job_id"]
        deadline = time.time() + 10
        status = None
        while time.time() < deadline:
            status = c.get(f"/api/imports/{job_id}", headers=H).json()["status"]
            if status in ("done", "failed", "skipped"):
                break
            time.sleep(0.05)
        assert status == "done"


def test_delete_import_records(client, svc, fakes):
    for v in ("d1", "d2"):
        fakes.ytdlp.add_video(yt_video(v))
    ids = client.post("/api/imports/batch", headers=H, json={"urls": [
        "https://www.youtube.com/watch?v=d1", "https://www.youtube.com/watch?v=d2",
        "https://www.youtube.com/watch?v=missing"]}).json()["job_ids"]
    drain(client, svc)
    statuses = {j["id"]: j["status"] for j in client.get("/api/imports", headers=H).json()}
    assert statuses[ids[0]] == "done" and statuses[ids[2]] == "failed"

    # single delete of a finished job
    r = client.delete(f"/api/imports/{ids[0]}/record", headers=H)
    assert r.status_code == 200 and r.json()["deleted"] == 1
    assert client.get(f"/api/imports/{ids[0]}", headers=H).status_code == 404
    assert client.delete(f"/api/imports/{ids[0]}/record", headers=H).status_code == 404

    # unfinished jobs are protected
    q = client.post("/api/imports", headers=H, json={"url": "https://www.youtube.com/watch?v=later"}).json()["job_id"]
    assert client.delete(f"/api/imports/{q}/record", headers=H).status_code == 409
    r = client.post("/api/imports/clear", headers=H, json={}).json()
    assert q not in r["job_ids"] and set(r["job_ids"]) == {ids[1], ids[2]}
    assert [j["id"] for j in client.get("/api/imports", headers=H).json()] == [q]


def test_clear_import_records_by_status_and_age(client, svc, fakes, clock):
    fakes.ytdlp.add_video(yt_video("c1"))
    ids = client.post("/api/imports/batch", headers=H, json={"urls": [
        "https://www.youtube.com/watch?v=c1", "https://www.youtube.com/watch?v=nope"]}).json()["job_ids"]
    drain(client, svc)
    assert client.post("/api/imports/clear", headers=H, json={"older_than_days": 1}).json()["deleted"] == 0
    r = client.post("/api/imports/clear", headers=H, json={"statuses": ["failed"]}).json()
    assert r["job_ids"] == [ids[1]]
    assert client.post("/api/imports/clear", headers=H, json={"statuses": ["queued"]}).status_code == 422
    assert [j["id"] for j in client.get("/api/imports", headers=H).json()] == [ids[0]]


def test_deleting_watch_job_record_keeps_watch_item(client, svc, fakes):
    from vsimporter.models import Job, WatchItem

    fakes.ytdlp.add_video(yt_video("w1"))
    with svc.Session() as s:
        from vsimporter.models import Watch

        w = Watch(url="https://www.youtube.com/@x", normalized_url="youtube.com/@x", kind="channel", name="x",
                  source_domain="youtube.com",
                  enabled=True, tags=[], filters={}, initial_mode="new_only")
        s.add(w)
        s.flush()
        job = Job(url="https://www.youtube.com/watch?v=w1", status="done", tags=[], watch_id=w.id)
        s.add(job)
        s.flush()
        s.add(WatchItem(watch_id=w.id, source_video_id="w1", status="imported", job_id=job.id))
        s.commit()
        job_id = job.id
    assert client.delete(f"/api/imports/{job_id}/record", headers=H).status_code == 200
    with svc.Session() as s:
        item = s.scalars(__import__("sqlalchemy").select(WatchItem)).one()
        assert item.status == "imported" and item.job_id is None
