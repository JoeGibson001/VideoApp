"""Phase 6 acceptance: watches (DESIGN 11.7 / 15)."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from vsimporter.models import Job, Watch, WatchItem
from vsimporter.watches import DuplicateWatch

from .conftest import yt_video

pytestmark = pytest.mark.anyio

PL = "https://www.youtube.com/playlist?list=PLtest"
CH = "https://www.youtube.com/@SomeCreator/shorts"


def entry(vid: str, ts: int, **kw) -> dict:
    e = {"_type": "url", "ie_key": "Youtube", "id": vid, "url": f"https://www.youtube.com/shorts/{vid}",
         "title": f"Video {vid}", "timestamp": ts, "duration": 30}
    e.update(kw)
    return e


def setup_playlist(fakes, n: int, url: str = PL, **extra) -> list[dict]:
    # Playlist order: oldest first (new videos are appended at the end).
    entries = []
    for i in range(n):
        ts = 1_700_000_000 + i * 86400
        fakes.ytdlp.add_video(yt_video(f"v{i}", ts=ts), f"https://www.youtube.com/shorts/v{i}")
        entries.append(entry(f"v{i}", ts))
    fakes.ytdlp.set_playlist(url, entries, **extra)
    return entries


def add_to_listing(fakes, url: str, vid: str, ts: int, front: bool = False) -> None:
    fakes.ytdlp.add_video(yt_video(vid, ts=ts), f"https://www.youtube.com/shorts/{vid}")
    entries = fakes.ytdlp.playlists[url]["entries"]
    e = entry(vid, ts)
    entries.insert(0, e) if front else entries.append(e)


def create(svc, url=PL, **kw) -> int:
    with svc.Session() as s:
        w = svc.watches.create(s, url=url, **kw)
        s.commit()
        return w.id


def items(svc, wid) -> dict[str, str]:
    with svc.Session() as s:
        return {i.source_video_id: i.status for i in s.scalars(select(WatchItem).where(WatchItem.watch_id == wid))}


def jobs(svc) -> list[Job]:
    with svc.Session() as s:
        return list(s.scalars(select(Job).order_by(Job.id)))


async def check_now(svc, wid):
    run = await svc.watches.run_watch(wid)
    await svc.jobs.run_until_idle()
    return run


async def test_new_only_imports_nothing_then_only_new_videos(svc, fakes):
    setup_playlist(fakes, 5)
    wid = create(svc)
    run = await check_now(svc, wid)
    assert run.error is None and run.queued_count == 0 and run.listed_count == 5
    assert jobs(svc) == []
    assert set(items(svc, wid).values()) == {"baseline"}

    add_to_listing(fakes, PL, "fresh", 1_800_000_000)
    run = await check_now(svc, wid)
    assert run.queued_count == 1 and run.new_count == 1
    js = jobs(svc)
    assert [j.url for j in js] == ["https://www.youtube.com/shorts/fresh"]
    assert js[0].status == "done" and js[0].watch_id == wid
    assert items(svc, wid)["fresh"] == "imported"
    assert [p["source_video_id"] for p in fakes.app.ingested] == ["fresh"]

    run = await check_now(svc, wid)
    assert run.queued_count == 0 and run.new_count == 0
    assert len(jobs(svc)) == 1

    with svc.Session() as s:
        w = svc.watches.serialize(s, [s.get(Watch, wid)])[0]
    assert w["imported_count"] == 1 and w["last_new_count"] == 0 and w["kind"] == "playlist"
    assert w["name"] == "A playlist" and w["creator_handle"] is None


async def test_backfill_latest_imports_exactly_the_newest(svc, fakes):
    setup_playlist(fakes, 8)
    wid = create(svc, initial_mode="backfill_latest", backfill_count=3)
    run = await check_now(svc, wid)
    assert run.queued_count == 3
    imported = sorted(p["source_video_id"] for p in fakes.app.ingested)
    assert imported == ["v5", "v6", "v7"]
    st = items(svc, wid)
    assert sum(1 for v in st.values() if v == "baseline") == 5
    # Nothing more on the next run.
    run = await check_now(svc, wid)
    assert run.queued_count == 0


async def test_backfill_latest_without_dates_on_channel_uses_listing_order(svc, fakes):
    entries = []
    for i in range(5):
        fakes.ytdlp.add_video(yt_video(f"c{i}"), f"https://www.youtube.com/shorts/c{i}")
        entries.append({"_type": "url", "ie_key": "Youtube", "id": f"c{i}", "url": f"https://www.youtube.com/shorts/c{i}"})
    fakes.ytdlp.set_playlist(CH, entries, uploader_id="@SomeCreator", channel_id="UCabc", id="UCabc")
    wid = create(svc, url=CH, initial_mode="backfill_latest", backfill_count=2)
    await check_now(svc, wid)
    assert sorted(p["source_video_id"] for p in fakes.app.ingested) == ["c0", "c1"]


async def test_existing_video_recorded_skipped_exists(svc, fakes):
    setup_playlist(fakes, 2)
    wid = create(svc)
    await check_now(svc, wid)
    add_to_listing(fakes, PL, "known", 1_800_000_000)
    fakes.app.library[("youtube.com", "known")] = 55
    run = await check_now(svc, wid)
    assert run.queued_count == 0
    assert items(svc, wid)["known"] == "skipped_exists"
    assert fakes.ytdlp.downloads == []


async def test_filters_record_filtered(svc, fakes):
    setup_playlist(fakes, 1)
    wid = create(svc, filters={"max_duration_s": 60, "title_exclude": ["skipme"]})
    await check_now(svc, wid)
    add_to_listing(fakes, PL, "longone", 1_800_000_000)
    fakes.ytdlp.playlists[PL]["entries"][-1]["duration"] = 600
    add_to_listing(fakes, PL, "skipme1", 1_800_000_100)
    fakes.ytdlp.playlists[PL]["entries"][-1]["title"] = "please SKIPME"
    add_to_listing(fakes, PL, "good", 1_800_000_200)
    run = await check_now(svc, wid)
    st = items(svc, wid)
    assert st["longone"] == "filtered" and st["skipme1"] == "filtered" and st["good"] == "imported"
    assert run.queued_count == 1


async def test_vertical_only_checked_in_job(svc, fakes):
    setup_playlist(fakes, 1)
    wid = create(svc, filters={"vertical_only": True})
    await check_now(svc, wid)
    fakes.ytdlp.add_video(yt_video("wide", width=1920, height=1080), "https://www.youtube.com/shorts/wide")
    fakes.ytdlp.playlists[PL]["entries"].append(entry("wide", 1_800_000_000))
    await check_now(svc, wid)
    assert items(svc, wid)["wide"] == "filtered"
    assert fakes.ytdlp.downloads == []


async def test_max_per_run_defers_remainder(svc, fakes, settings):
    settings.watch_max_per_run = 2
    setup_playlist(fakes, 5)
    wid = create(svc, initial_mode="backfill_all")
    r1 = await check_now(svc, wid)
    assert r1.queued_count == 2
    assert sorted(items(svc, wid).values()).count("pending") == 3
    r2 = await check_now(svc, wid)
    r3 = await check_now(svc, wid)
    assert (r2.queued_count, r3.queued_count) == (2, 1)
    assert len(fakes.app.ingested) == 5
    assert set(items(svc, wid).values()) == {"imported"}


async def test_failed_retried_on_next_two_runs_and_unavailable_not_retried(svc, fakes):
    setup_playlist(fakes, 1)
    wid = create(svc)
    await check_now(svc, wid)
    add_to_listing(fakes, PL, "flaky", 1_800_000_000)
    add_to_listing(fakes, PL, "private", 1_800_000_100)
    fakes.ytdlp.errors["https://www.youtube.com/shorts/flaky"] = "ERROR: unable to download webpage: timed out"
    fakes.ytdlp.errors["https://www.youtube.com/shorts/private"] = "ERROR: [youtube] private: Private video"
    await check_now(svc, wid)
    st = items(svc, wid)
    assert st["flaky"] == "failed" and st["private"] == "unavailable"
    r = await check_now(svc, wid)
    assert r.queued_count == 1
    r = await check_now(svc, wid)
    assert r.queued_count == 1
    r = await check_now(svc, wid)
    assert r.queued_count == 0  # two automatic retries used up
    assert items(svc, wid)["flaky"] == "failed"
    private_jobs = [j for j in jobs(svc) if j.url.endswith("/private")]
    assert len(private_jobs) == 1


async def test_live_entries_not_recorded(svc, fakes):
    setup_playlist(fakes, 1)
    wid = create(svc)
    await check_now(svc, wid)
    fakes.ytdlp.playlists[PL]["entries"].append(entry("upcoming", 1_900_000_000, live_status="is_upcoming"))
    run = await check_now(svc, wid)
    assert "upcoming" not in items(svc, wid)
    assert run.queued_count == 0


async def test_channel_stops_after_20_consecutive_seen(svc, fakes):
    entries = [entry(f"s{i}", 1_800_000_000 - i) for i in range(30)]
    fakes.ytdlp.set_playlist(CH, entries, uploader_id="@SomeCreator", channel_id="UCabc", id="UCabc")
    wid = create(svc, url=CH)
    await check_now(svc, wid)
    # An old video that was never seen sits after 20+ seen ones: the scan stops before it.
    fakes.ytdlp.playlists[CH]["entries"].append(entry("old-unseen", 1_000_000_000))
    run = await check_now(svc, wid)
    assert run.listed_count == 20
    assert "old-unseen" not in items(svc, wid)
    with svc.Session() as s:
        w = s.get(Watch, wid)
        assert w.kind == "channel"
        assert w.creator_source_domain == "youtube.com" and w.creator_handle == "@SomeCreator"


async def test_listing_failure_counts_consecutive_failures(svc, fakes):
    fakes.ytdlp.errors[PL] = "ERROR: [youtube:tab] PLtest: This playlist does not exist"
    wid = create(svc)
    for _ in range(3):
        run = await svc.watches.run_watch(wid)
        assert run.error
    with svc.Session() as s:
        w = svc.watches.serialize(s, [s.get(Watch, wid)])[0]
    assert w["consecutive_failures"] == 3 and w["erroring"] is True and "does not exist" in w["last_error"]
    del fakes.ytdlp.errors[PL]
    setup_playlist(fakes, 1)
    await svc.watches.run_watch(wid)
    with svc.Session() as s:
        w = svc.watches.serialize(s, [s.get(Watch, wid)])[0]
    assert w["consecutive_failures"] == 0 and w["erroring"] is False and w["last_error"] is None


async def test_duplicate_watch_rejected_after_normalising(svc, fakes):
    create(svc, url="https://www.youtube.com/@SomeCreator")
    with pytest.raises(DuplicateWatch):
        create(svc, url="https://youtube.com/@somecreator/videos?si=123")
    create(svc, url="https://youtube.com/@somecreator/shorts")  # a different tab is fine


async def test_pause_resume_keeps_same_watch_and_history(svc, fakes):
    setup_playlist(fakes, 2)
    wid = create(svc)
    await check_now(svc, wid)
    with svc.Session() as s:
        svc.watches.update(s, s.get(Watch, wid), {"enabled": False})
        s.commit()
    add_to_listing(fakes, PL, "while-paused", 1_800_000_000)
    with svc.Session() as s:
        w = s.get(Watch, wid)
        assert not w.enabled and w.paused_at is not None
        svc.watches.update(s, w, {"enabled": True, "resume_mode": "import_missed"})
        s.commit()
    with svc.Session() as s:
        assert len(list(s.scalars(select(Watch)))) == 1
        assert s.get(Watch, wid).enabled
    await check_now(svc, wid)
    assert items(svc, wid)["while-paused"] == "imported"
    assert len(items(svc, wid)) == 3  # history kept


async def test_resume_only_new_rebaselines(svc, fakes):
    setup_playlist(fakes, 2)
    wid = create(svc)
    await check_now(svc, wid)
    with svc.Session() as s:
        svc.watches.update(s, s.get(Watch, wid), {"enabled": False})
        s.commit()
    add_to_listing(fakes, PL, "missed", 1_800_000_000)
    with svc.Session() as s:
        svc.watches.update(s, s.get(Watch, wid), {"enabled": True, "resume_mode": "only_new"})
        s.commit()
    run = await check_now(svc, wid)
    assert run.queued_count == 0
    assert items(svc, wid)["missed"] == "baseline"
    add_to_listing(fakes, PL, "after", 1_800_100_000)
    await check_now(svc, wid)
    assert items(svc, wid)["after"] == "imported"


async def test_scheduler_runs_each_enabled_watch_once_a_day_and_skips_paused(svc, fakes, clock, gaps):
    urls = [f"https://www.youtube.com/playlist?list=PL{i}" for i in range(3)]
    for u in urls:
        setup_playlist(fakes, 1, url=u)
    a, b, c = (create(svc, url=u) for u in urls)
    with svc.Session() as s:
        svc.watches.update(s, s.get(Watch, c), {"enabled": False})
        s.commit()

    runs: list[int] = []
    orig = svc.watches.run_watch

    async def spy(wid, trigger="manual"):
        runs.append(wid)
        return await orig(wid, trigger)

    svc.watches.run_watch = spy

    # Created at 12:00 on Nov 1, so nothing is due until 03:00 on Nov 2.
    assert await svc.scheduler.tick() == []
    clock.now = datetime(2026, 11, 2, 2, 59, tzinfo=timezone.utc)
    assert await svc.scheduler.tick() == []

    clock.now = datetime(2026, 11, 2, 3, 0, tzinfo=timezone.utc)
    assert await svc.scheduler.tick() == [a, b]
    assert gaps == [45.0]  # one gap between the two watches
    # Same day, later ticks: nothing more.
    for minutes in (1, 30, 600):
        clock.now = datetime(2026, 11, 2, 3, 0, tzinfo=timezone.utc) + timedelta(minutes=minutes)
        assert await svc.scheduler.tick() == []

    # Next days: each enabled watch once per day.
    for day in (3, 4):
        clock.now = datetime(2026, 11, day, 3, 0, 5, tzinfo=timezone.utc)
        assert await svc.scheduler.tick() == [a, b]
        assert await svc.scheduler.tick() == []
    assert sorted(runs) == sorted([a, b] * 3)
    assert c not in runs

    # Container down for two days: overdue watches run once on startup, then resume the daily schedule.
    clock.now = datetime(2026, 11, 7, 9, 0, tzinfo=timezone.utc)
    assert await svc.scheduler.tick() == [a, b]
    with svc.Session() as s:
        assert s.get(Watch, a).next_run_at == datetime(2026, 11, 8, 3, 0)
