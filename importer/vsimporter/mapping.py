"""Pure functions: yt-dlp info dict -> App fields (DESIGN 11.3), tag normalisation,
sidecar sanitising (11.6), URL normalisation and error classification."""

from __future__ import annotations

import copy
import re
from datetime import date, datetime, timezone
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# ---------------------------------------------------------------- tags


def normalize_tag(tag: Any) -> str | None:
    if tag is None:
        return None
    t = re.sub(r"\s+", " ", str(tag)).strip().lower()
    t = t.lstrip("#").strip()
    if not t or len(t) > 100:
        return None
    return t


def normalize_tags(*groups: Iterable[Any] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for tag in group or []:
            t = normalize_tag(tag)
            if t and t not in seen:
                seen.add(t)
                out.append(t)
    return out


# ---------------------------------------------------------------- source

_SOURCE_NAMES = [
    ("youtube", "YouTube"),
    ("tiktok", "TikTok"),
    ("instagram", "Instagram"),
    ("facebook", "Facebook"),
    ("twitter", "X (Twitter)"),
    ("vimeo", "Vimeo"),
    ("reddit", "Reddit"),
    ("twitch", "Twitch"),
    ("dailymotion", "Dailymotion"),
    ("bilibili", "BiliBili"),
    ("soundcloud", "SoundCloud"),
    ("streamable", "Streamable"),
    ("imgur", "Imgur"),
    ("rumble", "Rumble"),
    ("bluesky", "Bluesky"),
    ("pinterest", "Pinterest"),
    ("snapchat", "Snapchat"),
    ("threads", "Threads"),
    ("kick", "Kick"),
]


def domain_of(url: str | None) -> str | None:
    if not url:
        return None
    host = (urlsplit(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host or None


def source_name(extractor_key: str | None, domain: str | None) -> str:
    key = (extractor_key or "").strip()
    low = key.lower()
    for prefix, name in _SOURCE_NAMES:
        if low.startswith(prefix):
            return name
    if not key or low == "generic":
        return domain or "Unknown"
    # Split CamelCase extractor keys like "NhkVod" into a readable name.
    return key


def safe_name(value: str, max_len: int = 120) -> str:
    """Make a string safe for use as a single path component."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value)).strip("._")
    s = s[:max_len].strip("._")
    return s or "_"


def safe_handle(handle: str) -> str:
    return safe_name(str(handle).lstrip("@"))


def safe_domain(domain: str) -> str:
    return safe_name(domain.lower(), 253)


# ---------------------------------------------------------------- mapping


def _first(info: dict, *keys: str) -> Any:
    for k in keys:
        v = info.get(k)
        if v not in (None, "", [], {}):
            return v
    return None


def upload_date_of(info: dict) -> str | None:
    for key in ("upload_date", "release_date"):
        v = info.get(key)
        if isinstance(v, str) and re.fullmatch(r"\d{8}", v):
            try:
                return date(int(v[:4]), int(v[4:6]), int(v[6:])).isoformat()
            except ValueError:
                pass
    for key in ("timestamp", "release_timestamp"):
        ts = info.get(key)
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
    return None


def map_source(info: dict) -> dict:
    url = info.get("webpage_url") or info.get("original_url") or info.get("url")
    domain = domain_of(url) or (info.get("extractor") or "unknown").lower()
    return {"domain": domain, "name": source_name(info.get("extractor_key") or info.get("ie_key"), domain)}


def map_creator(info: dict, source: dict) -> dict:
    name = _first(info, "uploader", "channel", "creator", "uploader_id")
    handle = _first(info, "uploader_id", "channel_id", "uploader")
    profile = _first(info, "uploader_url", "channel_url")
    if name is None and handle is None:
        # Every video needs a creator; fall back to the source itself.
        name, handle = source["name"], source["domain"]
    return {
        "name": str(name if name is not None else handle),
        "handle": str(handle if handle is not None else name),
        "profile_url": profile,
        "avatar_path": None,
    }


def avatar_url_of(info: dict) -> str | None:
    """Best effort creator avatar URL from an info dict (video or channel listing)."""
    for key in ("channel_thumbnail", "uploader_avatar", "uploader_thumbnail", "avatar", "channel_avatar"):
        v = info.get(key)
        if isinstance(v, str) and v.startswith("http"):
            return v
    # Channel/tab listings expose the avatar in thumbnails with an id like "avatar_uncropped".
    thumbs = info.get("thumbnails") or []
    if info.get("_type") == "playlist":
        for t in reversed(thumbs):
            if isinstance(t, dict) and "avatar" in str(t.get("id", "")) and t.get("url"):
                return t["url"]
    return None


def thumbnail_url_of(info: dict) -> str | None:
    t = info.get("thumbnail")
    if isinstance(t, str) and t.startswith("http"):
        return t
    thumbs = [x for x in (info.get("thumbnails") or []) if isinstance(x, dict) and x.get("url")]
    if thumbs:
        thumbs.sort(key=lambda x: (x.get("preference") or 0, (x.get("width") or 0) * (x.get("height") or 0)))
        return thumbs[-1]["url"]
    return None


def original_filename(title: str | None, ext: str = "mp4") -> str:
    base = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", title or "video")
    base = re.sub(r"\s+", " ", base).strip() or "video"
    return f"{base[:200].strip()}.{ext}"


def media_paths(domain: str, video_id: str, handle: str | None) -> dict[str, str | None]:
    d, v = safe_domain(domain), safe_name(video_id)
    return {
        "video": f"videos/{d}/{v}.mp4",
        "metadata": f"metadata/{d}/{v}.json",
        "poster": f"posters/{d}/{v}.jpg",
        "avatar": f"avatars/{d}/{safe_handle(handle)}.jpg" if handle else None,
    }


def build_payload(
    info: dict,
    *,
    request_tags: list[str] | None,
    file_info: dict,
    paths: dict[str, str | None],
    poster_path: str | None,
    avatar_path: str | None,
) -> dict:
    """Ingest payload, exactly per DESIGN 11.5."""
    source = map_source(info)
    creator = map_creator(info, source)
    creator["avatar_path"] = avatar_path
    tags = normalize_tags(info.get("tags"), info.get("categories"), request_tags)
    if not tags:
        tags = [source["domain"]]
    return {
        "source": source,
        "source_video_id": str(info["id"]),
        "source_url": info.get("webpage_url") or info.get("original_url"),
        "creator": creator,
        "title": info.get("title") or str(info["id"]),
        "description": info.get("description"),
        "tags": tags,
        "upload_date": upload_date_of(info),
        "file": file_info,
        "poster_path": poster_path,
        "info_json_path": paths["metadata"],
    }


# ---------------------------------------------------------------- sidecar

_DROP_KEYS = {
    "http_headers",
    "cookies",
    "fragments",
    "manifest_url",
    "fragment_base_url",
    "downloader_options",
    "automatic_captions",
    "requested_subtitles",
    "requested_downloads",
    "filepath",
    "_filename",
    "filename",
    "infojson_filename",
    "__real_download",
    "__files_to_move",
    "__postprocessors",
    "_format_sort_fields",
    "heatmap",
}

_SIGNED_QUERY_KEYS = {
    "expire",
    "expires",
    "signature",
    "sig",
    "lsig",
    "sparams",
    "token",
    "policy",
    "key-pair-id",
    "x-amz-signature",
    "x-amz-credential",
    "x-amz-security-token",
    "x-goog-signature",
    "hdnts",
    "hdnea",
    "oh",
    "oe",
    "rs",
    "ip",
    "ei",
}


def is_signed_url(value: Any) -> bool:
    if not isinstance(value, str) or not value.startswith(("http://", "https://")):
        return False
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    keys = {k.lower() for k, _ in parse_qsl(parts.query, keep_blank_values=True)}
    if keys & _SIGNED_QUERY_KEYS:
        return True
    # googlevideo style path parameters: /videoplayback/expire/123/...
    return "/expire/" in parts.path or "googlevideo.com" in (parts.hostname or "")


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in _DROP_KEYS or str(k).startswith("__"):
                continue
            if is_signed_url(v):
                continue
            out[k] = _scrub(v)
        return out
    if isinstance(value, list):
        return [_scrub(v) for v in value if not is_signed_url(v)]
    return value


def sanitize_info(info: dict, used_thumbnail_url: str | None = None) -> dict:
    """Return a sanitised copy of a yt-dlp info dict for the metadata sidecar."""
    data = copy.deepcopy(info)
    used_ids = set(str(data.get("format_id") or "").split("+")) - {""}
    for key in ("formats", "requested_formats"):
        fmts = data.get(key)
        if isinstance(fmts, list):
            kept = [f for f in fmts if isinstance(f, dict) and str(f.get("format_id")) in used_ids]
            data[key] = kept
            if not kept:
                data.pop(key)
    thumbs = data.get("thumbnails")
    if isinstance(thumbs, list):
        target = used_thumbnail_url or data.get("thumbnail")
        kept = [t for t in thumbs if isinstance(t, dict) and t.get("url") == target]
        data["thumbnails"] = kept[:1]
    subs = data.get("subtitles")
    if isinstance(subs, dict):
        data["subtitles"] = {
            lang: [{k: v for k, v in s.items() if k in ("ext", "name")} for s in items if isinstance(s, dict)]
            for lang, items in subs.items()
            if isinstance(items, list)
        }
    # Per-format URLs are always short lived.
    for key in ("formats", "requested_formats"):
        for f in data.get(key) or []:
            f.pop("url", None)
    data.pop("url", None)
    return _scrub(data)


# ---------------------------------------------------------------- urls

_TRACKING = {"si", "feature", "pp", "fbclid", "gclid", "igshid", "ref", "ref_src"}
_YT_HOSTS = {"youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}


def normalize_url(url: str) -> str:
    """Normalise a watch URL so duplicates can be detected."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    for prefix in ("www.", "m."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    path = re.sub(r"/+", "/", parts.path).rstrip("/")
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if k.lower() not in _TRACKING and not k.lower().startswith("utm_")
    ]
    if host in _YT_HOSTS:
        host = "youtube.com"
        query = [(k, v) for k, v in query if k in ("list", "v")]
        if dict(query).get("list") and path in ("/watch", "/playlist"):
            path, query = "/playlist", [("list", dict(query)["list"])]
        # A channel root and its default tabs are the same feed.
        path = re.sub(r"/(videos|featured)$", "", path)
        if path.startswith("/@"):
            path = path.lower()
    query.sort()
    return urlunsplit(("https", host, path, urlencode(query), ""))


def guess_kind(url: str, info: dict | None = None) -> str:
    """Classify a watch target as channel, playlist or page."""
    info = info or {}
    low = url.lower()
    ek = str(info.get("extractor_key") or "").lower()
    if "list=" in low or "/playlist" in low or "playlist" in ek or "album" in ek:
        return "playlist"
    if re.search(r"/(@[^/]+|channel/|c/|user/)", low) or ek.endswith("user") or ek.endswith("channel"):
        return "channel"
    if info.get("channel_id") and info.get("id") == info.get("channel_id"):
        return "channel"
    if info.get("uploader_id") and info.get("id") == info.get("uploader_id"):
        return "channel"
    return "page"


# ---------------------------------------------------------------- errors

_UNAVAILABLE = [
    "private video",
    "video is private",
    "video unavailable",
    "this video is unavailable",
    "has been removed",
    "no longer available",
    "not available in your country",
    "not made this video available in your country",
    "geo restriction",
    "geo-restricted",
    "account associated with this video has been terminated",
    "members-only",
    "join this channel",
    "copyright claim",
    "does not exist",
    "http error 404",
    "video has been deleted",
]


def classify_error(text: str | None) -> str:
    """Return "unavailable" for permanent per-video errors, else "error"."""
    low = (text or "").lower()
    if any(p in low for p in _UNAVAILABLE):
        return "unavailable"
    return "error"


def last_error_line(text: str | None) -> str | None:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    for ln in reversed(lines):
        if ln.startswith("ERROR"):
            return ln
    return lines[-1] if lines else None


def is_live(info: dict) -> bool:
    return bool(info.get("is_live")) or info.get("live_status") in ("is_live", "is_upcoming", "post_live")


def entry_url(entry: dict) -> str | None:
    for key in ("webpage_url", "url", "original_url"):
        u = entry.get(key)
        if isinstance(u, str) and u.startswith(("http://", "https://")):
            return u
    ie = str(entry.get("ie_key") or "").lower()
    vid = entry.get("id")
    if vid and ie.startswith("youtube"):
        return f"https://www.youtube.com/watch?v={vid}"
    return None


def entry_is_playlist(entry: dict) -> bool:
    if entry.get("_type") == "playlist":
        return True
    ie = str(entry.get("ie_key") or "")
    return ie.endswith("Tab") or "Playlist" in ie


# ---------------------------------------------------------------- watch filters


def _keywords(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        value = value.split(",")
    return [str(v).strip().lower() for v in value if str(v).strip()]


def filter_reason(filters: dict | None, info: dict) -> str | None:
    """Return why `info` is excluded by a watch's filters, or None if it passes.

    Unknown values (for example no duration in a flat listing) pass; the job
    re-checks the filters once the full metadata is known.
    """
    if not filters:
        return None
    duration = info.get("duration")
    if isinstance(duration, (int, float)):
        mx, mn = filters.get("max_duration_s"), filters.get("min_duration_s")
        if mx is not None and duration > float(mx):
            return f"duration {duration:.0f}s is over max_duration_s {mx}"
        if mn is not None and duration < float(mn):
            return f"duration {duration:.0f}s is under min_duration_s {mn}"
    title = info.get("title")
    if isinstance(title, str):
        low = title.lower()
        inc, exc = _keywords(filters.get("title_include")), _keywords(filters.get("title_exclude"))
        if inc and not any(k in low for k in inc):
            return "title does not match title_include"
        hit = next((k for k in exc if k in low), None)
        if hit:
            return f"title matches title_exclude '{hit}'"
    after = filters.get("uploaded_after")
    if after:
        uploaded = upload_date_of(info)
        if uploaded and uploaded < str(after)[:10]:
            return f"uploaded {uploaded}, before uploaded_after {str(after)[:10]}"
    if filters.get("vertical_only"):
        w, h = info.get("width"), info.get("height")
        if isinstance(w, int) and isinstance(h, int) and w and h and w >= h:
            return f"not vertical ({w}x{h})"
    return None
