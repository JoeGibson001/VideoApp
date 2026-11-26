import json

import pytest
from sqlalchemy import select

from vsimporter.models import Job

from .conftest import yt_video

pytestmark = pytest.mark.anyio

PAYLOAD_KEYS = {"source", "source_video_id", "source_url", "creator", "title", "description", "tags", "upload_date",
                "file", "poster_path", "info_json_path"}
FILE_KEYS = {"path", "original_filename", "size_bytes", "width", "height", "duration_s", "fps", "video_codec",
             "audio_codec", "has_audio", "container"}


def add_job(svc, url, **kw) -> int:
    with svc.Session() as s:
        job = svc.jobs.create(s, url, **kw)
        s.commit()
        return job.id


def get_job(svc, job_id) -> Job:
    with svc.Session() as s:
        return s.get(Job, job_id)


async def test_import_pipeline_end_to_end(svc, fakes, settings):
    fakes.ytdlp.add_video(yt_video("abc123", title="Video title"), "https://www.youtube.com/shorts/abc123")
    job_id = add_job(svc, "https://www.youtube.com/shorts/abc123", tags=["Extra Tag", "cars"])
    await svc.jobs.run_until_idle()

    job = get_job(svc, job_id)
    assert job.status == "done", job.error
    assert job.video_id == 101
    assert job.progress == 100
    assert job.source_domain == "youtube.com" and job.source_video_id == "abc123"
    assert job.title == "Video title"

    (payload,) = fakes.app.ingested
    assert set(payload) == PAYLOAD_KEYS
    assert set(payload["file"]) == FILE_KEYS
    assert payload["source"] == {"domain": "youtube.com", "name": "YouTube"}
    assert payload["source_url"] == "https://www.youtube.com/watch?v=abc123"
    assert payload["creator"] == {
        "name": "Some Creator",
        "handle": "@somecreator",
        "profile_url": "https://www.youtube.com/@somecreator",
        "avatar_path": None,  # yt-dlp exposes no avatar for this video
    }
    assert payload["tags"] == ["cars", "drift", "autos & vehicles", "extra tag"]
    assert payload["upload_date"] == "2023-11-14"
    assert payload["file"] == {
        "path": "videos/youtube.com/abc123.mp4",
        "original_filename": "Video title.mp4",
        "size_bytes": 2048,
        "width": 1080,
        "height": 1920,
        "duration_s": 42.3,
        "fps": 30.0,
        "video_codec": "h264",
        "audio_codec": "aac",
        "has_audio": True,
        "container": "mp4",
    }
    assert payload["poster_path"] == "posters/youtube.com/abc123.jpg"
    assert payload["info_json_path"] == "metadata/youtube.com/abc123.json"

    media = settings.media_dir
    assert (media / "videos/youtube.com/abc123.mp4").stat().st_size == 2048
    assert (media / "posters/youtube.com/abc123.jpg").read_bytes().startswith(b"\xff\xd8")
    sidecar = json.loads((media / "metadata/youtube.com/abc123.json").read_text())
    assert "http_headers" not in json.dumps(sidecar)
    assert "googlevideo" not in json.dumps(sidecar)
    imp = sidecar["_import"]
    for key in ("imported_at", "importer_version", "ytdlp_version", "request_tags", "watch_id", "source", "creator",
                "file", "poster_path", "info_json_path"):
        assert key in imp
    assert imp["request_tags"] == ["extra tag", "cars"]
    assert imp["file"] == payload["file"]
    assert imp["creator"] == payload["creator"]
    assert imp["source"] == payload["source"]
    assert imp["ytdlp_version"] == "2099.01.01"
    assert imp["watch_id"] is None
    # Thumbnail fetched from the site and remuxed with faststart, no transcode.
    assert fakes.fetcher.fetched == ["https://i.ytimg.com/vi/abc123/maxresdefault.jpg"]
    assert fakes.media.converts == [{"video_copy": True, "audio_copy": True}]
    # Temp dir cleaned.
    assert list((settings.tmp_dir).iterdir()) == []


async def test_avatar_downloaded_when_exposed(svc, fakes, settings):
    fakes.ytdlp.add_video(yt_video("av1", channel_thumbnail="https://yt3.ggpht.com/avatar.jpg"))
    add_job(svc, "https://www.youtube.com/watch?v=av1")
    await svc.jobs.run_until_idle()
    assert fakes.app.ingested[0]["creator"]["avatar_path"] == "avatars/youtube.com/somecreator.jpg"
    assert (settings.media_dir / "avatars/youtube.com/somecreator.jpg").exists()


async def test_transcode_when_incompatible(svc, fakes):
    fakes.ytdlp.add_video(yt_video("vp9"))
    fakes.media.source_probe = dict(fakes.media.probe_result, video_codec="vp9", audio_codec="opus")
    add_job(svc, "https://www.youtube.com/watch?v=vp9")
    await svc.jobs.run_until_idle()
    assert fakes.media.converts == [{"video_copy": False, "audio_copy": False}]


async def test_poster_falls_back_to_frame(svc, fakes, settings):
    fakes.ytdlp.add_video(yt_video("nothumb"))
    fakes.fetcher.fail = True
    add_job(svc, "https://www.youtube.com/watch?v=nothumb")
    await svc.jobs.run_until_idle()
    assert (settings.media_dir / "posters/youtube.com/nothumb.jpg").read_bytes() == b"\xff\xd8frame"


async def test_dedup_existing_video_is_skipped(svc, fakes):
    fakes.ytdlp.add_video(yt_video("dup1"))
    fakes.app.library[("youtube.com", "dup1")] = 7
    job_id = add_job(svc, "https://www.youtube.com/watch?v=dup1")
    await svc.jobs.run_until_idle()
    job = get_job(svc, job_id)
    assert job.status == "skipped"
    assert job.video_id == 7
    assert fakes.ytdlp.downloads == []
    assert fakes.app.ingested == []


async def test_same_url_twice_imports_once(svc, fakes):
    fakes.ytdlp.add_video(yt_video("twice"), "https://www.youtube.com/shorts/twice")
    a = add_job(svc, "https://www.youtube.com/shorts/twice")
    b = add_job(svc, "https://www.youtube.com/shorts/twice")
    await svc.jobs.run_until_idle()
    assert get_job(svc, a).status == "done"
    assert get_job(svc, b).status == "skipped"
    assert len(fakes.ytdlp.downloads) == 1


async def test_playlist_expansion_creates_one_job_per_entry(svc, fakes):
    url = "https://www.youtube.com/playlist?list=PL1"
    entries = []
    for i in range(5):
        fakes.ytdlp.add_video(yt_video(f"p{i}"))
        entries.append({"_type": "url", "ie_key": "Youtube", "id": f"p{i}", "url": f"https://www.youtube.com/watch?v=p{i}"})
    fakes.ytdlp.set_playlist(url, entries)
    parent = add_job(svc, url, expand_playlists=True, max_items=3, tags=["T"])
    await svc.jobs.run_until_idle()
    pj = get_job(svc, parent)
    assert pj.status == "done" and "3" in pj.detail
    with svc.Session() as s:
        children = list(s.scalars(select(Job).where(Job.parent_id == parent).order_by(Job.id)))
    assert [c.url for c in children] == [f"https://www.youtube.com/watch?v=p{i}" for i in range(3)]
    assert all(c.status == "done" and c.tags == ["t"] for c in children)
    assert len(fakes.app.ingested) == 3


async def test_playlist_without_expand_fails_clearly(svc, fakes):
    url = "https://www.youtube.com/playlist?list=PL2"
    fakes.ytdlp.set_playlist(url, [{"id": "x", "url": "https://www.youtube.com/watch?v=x"}])
    job_id = add_job(svc, url)
    await svc.jobs.run_until_idle()
    job = get_job(svc, job_id)
    assert job.status == "failed" and "expand_playlists" in job.error


async def test_ytdlp_failure_keeps_stderr_tail(svc, fakes):
    url = "https://www.youtube.com/watch?v=gone"
    fakes.ytdlp.errors[url] = "WARNING: something\nERROR: [youtube] gone: Video unavailable. This video has been removed"
    job_id = add_job(svc, url)
    await svc.jobs.run_until_idle()
    job = get_job(svc, job_id)
    assert job.status == "failed"
    assert job.outcome == "unavailable"
    assert "Video unavailable" in job.error
    assert "WARNING: something" in job.stderr_tail


async def test_duration_limit(svc, fakes, settings):
    settings.max_duration_min = 1
    fakes.ytdlp.add_video(yt_video("long", duration=3600))
    job_id = add_job(svc, "https://www.youtube.com/watch?v=long")
    await svc.jobs.run_until_idle()
    job = get_job(svc, job_id)
    assert job.status == "failed" and "MAX_DURATION_MIN" in job.error
    assert fakes.ytdlp.downloads == []


async def test_ingest_retries_then_succeeds(svc, fakes):
    fakes.ytdlp.add_video(yt_video("retry"))
    fakes.app.fail_ingest = 2
    job_id = add_job(svc, "https://www.youtube.com/watch?v=retry")
    await svc.jobs.run_until_idle()
    assert get_job(svc, job_id).status == "done"


async def test_ingest_failure_keeps_files_and_retry_reingests(svc, fakes, settings):
    fakes.ytdlp.add_video(yt_video("later"))
    fakes.app.fail_ingest = 10
    job_id = add_job(svc, "https://www.youtube.com/watch?v=later")
    await svc.jobs.run_until_idle()
    job = get_job(svc, job_id)
    assert job.status == "failed" and "Ingest failed" in job.error
    assert (settings.media_dir / "videos/youtube.com/later.mp4").exists()
    fakes.app.fail_ingest = 0
    svc.jobs.retry(job_id)
    await svc.jobs.run_until_idle()
    assert get_job(svc, job_id).status == "done"
    assert len(fakes.ytdlp.downloads) == 1  # not downloaded again


async def test_vertical_only_filter_at_probe(svc, fakes):
    fakes.ytdlp.add_video(yt_video("wide", width=1920, height=1080))
    job_id = add_job(svc, "https://www.youtube.com/watch?v=wide", options={"filters": {"vertical_only": True}})
    await svc.jobs.run_until_idle()
    job = get_job(svc, job_id)
    assert job.status == "skipped" and job.outcome == "filtered"
    assert fakes.ytdlp.downloads == []


async def test_cancel_queued_and_retry(svc, fakes):
    fakes.ytdlp.add_video(yt_video("c1"))
    job_id = add_job(svc, "https://www.youtube.com/watch?v=c1")
    job = await svc.jobs.cancel(job_id)
    assert job.status == "failed" and job.error == "Cancelled"
    svc.jobs.retry(job_id)
    await svc.jobs.run_until_idle()
    assert get_job(svc, job_id).status == "done"


async def test_live_stream_fails_without_download(svc, fakes):
    fakes.ytdlp.add_video(yt_video("live1", live_status="is_live", is_live=True))
    job_id = add_job(svc, "https://www.youtube.com/watch?v=live1")
    await svc.jobs.run_until_idle()
    job = get_job(svc, job_id)
    assert job.status == "failed" and job.outcome == "live"


async def test_avatar_from_creator_profile_listing(svc, fakes, settings):
    fakes.ytdlp.add_video(yt_video("prof1"))
    fakes.ytdlp.set_playlist("https://www.youtube.com/@somecreator", [], thumbnails=[
        {"id": "banner_uncropped", "url": "https://yt3.example/banner"},
        {"id": "avatar_uncropped", "url": "https://yt3.example/avatar"},
    ])
    add_job(svc, "https://www.youtube.com/watch?v=prof1")
    await svc.jobs.run_until_idle()
    assert fakes.app.ingested[0]["creator"]["avatar_path"] == "avatars/youtube.com/somecreator.jpg"
    assert "https://yt3.example/avatar" in fakes.fetcher.fetched
    # Second video by the same creator reuses the stored avatar without another lookup.
    fakes.ytdlp.add_video(yt_video("prof2"))
    n = len(fakes.ytdlp.probes)
    add_job(svc, "https://www.youtube.com/watch?v=prof2")
    await svc.jobs.run_until_idle()
    assert fakes.app.ingested[1]["creator"]["avatar_path"] == "avatars/youtube.com/somecreator.jpg"
    assert len(fakes.ytdlp.probes) == n + 1
