from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from vsimporter import mapping as m
from vsimporter.adapters import parse_ffprobe
from vsimporter.util import next_run_after

from .conftest import yt_video


def test_normalize_tags_trims_lowercases_collapses_and_dedupes():
    assert m.normalize_tags(["  Cars ", "cars", "Drift   King", "#Drift king", "", None], ["CARS", "new"]) == [
        "cars",
        "drift king",
        "new",
    ]


def test_source_name_prettified():
    assert m.source_name("Youtube", "youtube.com") == "YouTube"
    assert m.source_name("YoutubeTab", "youtube.com") == "YouTube"
    assert m.source_name("YoutubeShortsAudioPivot", "youtube.com") == "YouTube"
    assert m.source_name("TikTok", "tiktok.com") == "TikTok"
    assert m.source_name("Vimeo", "vimeo.com") == "Vimeo"
    assert m.source_name("Generic", "example.org") == "example.org"
    assert m.source_name("SomeSite", "somesite.tv") == "SomeSite"


def test_domain_strips_www():
    assert m.domain_of("https://www.YouTube.com/watch?v=x") == "youtube.com"
    assert m.domain_of("https://vimeo.com/1") == "vimeo.com"


def test_mapping_payload_shape():
    info = yt_video("abc123", tags=["Cars", "drift"], categories=[])
    file_info = {"path": "videos/youtube.com/abc123.mp4"}
    paths = m.media_paths("youtube.com", "abc123", "@somecreator")
    payload = m.build_payload(info, request_tags=["Extra"], file_info=file_info, paths=paths,
                              poster_path=paths["poster"], avatar_path=paths["avatar"])
    assert payload == {
        "source": {"domain": "youtube.com", "name": "YouTube"},
        "source_video_id": "abc123",
        "source_url": "https://www.youtube.com/watch?v=abc123",
        "creator": {
            "name": "Some Creator",
            "handle": "@somecreator",
            "profile_url": "https://www.youtube.com/@somecreator",
            "avatar_path": "avatars/youtube.com/somecreator.jpg",
        },
        "title": "Video abc123",
        "description": "Description of abc123",
        "tags": ["cars", "drift", "extra"],
        "upload_date": "2023-11-14",
        "file": file_info,
        "poster_path": "posters/youtube.com/abc123.jpg",
        "info_json_path": "metadata/youtube.com/abc123.json",
    }


def test_mapping_fallbacks_and_domain_tag():
    info = {"id": "x/1", "webpage_url": "https://www.example.org/v/1", "extractor_key": "Generic",
            "channel": "Chan", "channel_id": "C1", "timestamp": 0}
    payload = m.build_payload(info, request_tags=None, file_info={}, paths=m.media_paths("example.org", "x/1", "C1"),
                              poster_path=None, avatar_path=None)
    assert payload["tags"] == ["example.org"]
    assert payload["creator"]["name"] == "Chan"
    assert payload["creator"]["handle"] == "C1"
    assert payload["upload_date"] == "1970-01-01"
    assert payload["source"]["name"] == "example.org"
    assert m.media_paths("example.org", "x/1", "@a b/../c")["video"] == "videos/example.org/x_1.mp4"


def test_safe_handle_prevents_traversal():
    assert m.safe_handle("@some creator") == "some_creator"
    assert m.safe_handle("../../etc/passwd") == "etc_passwd"
    assert "/" not in m.safe_handle("a/b\\c")


def test_sanitize_info_removes_headers_signed_urls_and_bulky_arrays():
    info = yt_video("abc")
    info["requested_formats"] = [dict(f) for f in info["formats"][:2]]
    info["automatic_captions"] = {"en": [{"url": "https://x/y?expire=1"}]}
    info["subtitles"] = {"en": [{"ext": "vtt", "url": "https://www.youtube.com/api/timedtext?v=abc&expire=1&sig=1"}]}
    info["__files_to_move"] = {}
    out = m.sanitize_info(info, "https://i.ytimg.com/vi/abc/maxresdefault.jpg")
    text = repr(out)
    assert "http_headers" not in text
    assert "googlevideo" not in text and "expire=" not in text
    assert "automatic_captions" not in out
    assert [f["format_id"] for f in out["formats"]] == ["140", "137"]
    assert out["thumbnails"] == [{"id": "1", "url": "https://i.ytimg.com/vi/abc/maxresdefault.jpg"}]
    assert out["subtitles"] == {"en": [{"ext": "vtt"}]}
    assert "__files_to_move" not in out
    assert out["title"] == "Video abc"
    assert info["http_headers"]  # original untouched


def test_normalize_url_for_duplicates():
    a = m.normalize_url("https://www.youtube.com/@BBCEarth/videos?si=abc")
    b = m.normalize_url("https://youtube.com/@bbcearth/")
    assert a == b
    assert m.normalize_url("https://m.youtube.com/@BBCEarth/shorts") != a
    assert m.normalize_url("https://www.youtube.com/playlist?list=PL1&si=x") == m.normalize_url(
        "https://youtube.com/watch?v=abc&list=PL1"
    )


def test_guess_kind():
    assert m.guess_kind("https://www.youtube.com/playlist?list=PL1") == "playlist"
    assert m.guess_kind("https://www.youtube.com/@BBCEarthKids/shorts") == "channel"
    assert m.guess_kind("https://example.org/videos") == "page"


def test_classify_error():
    assert m.classify_error("ERROR: [youtube] x: Private video. Sign in if you've been granted access") == "unavailable"
    assert m.classify_error("ERROR: [youtube] x: Video unavailable. This video has been removed") == "unavailable"
    assert m.classify_error("ERROR: unable to download webpage: timed out") == "error"


def test_filter_reason():
    f = {"max_duration_s": 180, "title_exclude": ["live"], "uploaded_after": "2024-01-01", "vertical_only": True}
    assert m.filter_reason(f, {"duration": 100, "title": "ok"}) is None
    assert "max_duration" in m.filter_reason(f, {"duration": 200})
    assert "title_exclude" in m.filter_reason(f, {"title": "LIVE now"})
    assert "uploaded_after" in m.filter_reason(f, {"upload_date": "20230101"})
    assert "vertical" in m.filter_reason(f, {"width": 1920, "height": 1080})
    assert m.filter_reason({"title_include": "cat, dog"}, {"title": "My Dog"}) is None
    assert m.filter_reason({"title_include": ["cat"]}, {"title": "My Dog"}) is not None


def test_next_run_after_respects_timezone():
    syd = ZoneInfo("Australia/Sydney")
    now = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)  # 23:00 in Sydney (UTC+11)
    nxt = next_run_after(now, time(3, 0), syd)
    assert nxt == datetime(2026, 11, 1, 16, 0)  # 03:00 Sydney next day, as naive UTC
    assert next_run_after(datetime(2026, 11, 1, 3, 0, tzinfo=timezone.utc), time(3, 0), ZoneInfo("UTC")) == datetime(
        2026, 11, 2, 3, 0
    )


def test_parse_ffprobe_rotation_and_audio():
    raw = {
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080, "avg_frame_rate": "30000/1001",
             "side_data_list": [{"rotation": -90}]},
            {"codec_type": "audio", "codec_name": "aac"},
        ],
        "format": {"duration": "12.5", "format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
    }
    r = parse_ffprobe(raw, 1000)
    assert (r["width"], r["height"]) == (1080, 1920)
    assert r["fps"] == 29.97
    assert r["has_audio"] is True and r["audio_codec"] == "aac"
    assert r["container"] == "mp4" and r["duration_s"] == 12.5 and r["size_bytes"] == 1000
    r2 = parse_ffprobe({"streams": [{"codec_type": "video", "codec_name": "vp9"}], "format": {}})
    assert r2["has_audio"] is False and r2["audio_codec"] is None
