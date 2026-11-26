# Tastebud Importer

Separate service that downloads videos with yt-dlp, normalises them with ffmpeg, writes them to the
shared `/media` volume and hands the metadata to the App's internal ingest endpoint
(DESIGN.md section 11). It also runs **watches**: daily checks of channels and playlists that import
anything new (section 11.7).

## Stack

Python 3.12, FastAPI + uvicorn, SQLAlchemy 2 + Alembic on SQLite (`/data/jobs.db`), httpx,
pydantic-settings, yt-dlp (with the `deno` JS runtime it needs for YouTube) and ffmpeg/ffprobe.
Same Python/FastAPI/SQLAlchemy stack as the App so both services share conventions. One Docker
image; migrations run automatically on start; logs are JSON lines on stdout.

Layout:

```
vsimporter/
  config.py     env var settings
  models.py     jobs, watches, watch_items, watch_runs
  migrations/   Alembic (run on startup)
  adapters.py   yt-dlp, ffmpeg/ffprobe, App client, HTTP fetcher (swapped for fakes in tests)
  mapping.py    yt-dlp -> App mapping (11.3), tags, sidecar sanitising (11.6), URL normalising, filters
  jobs.py       queue, worker pool, import pipeline (11.2)
  watches.py    watch runs and the daily scheduler (11.7)
  api.py        HTTP API (11.1) and the minimal page at /
tests/          pytest, fully offline
```

## Running

The root `docker-compose.yml` builds this directory as the `importer` service (port 8081,
volumes `./data/importer:/data` and `./data/media:/media`).

The container starts as root only long enough to make `/data` and `/media` writable, then drops to
the unprivileged `importer` user (uid/gid 1000) for the actual process.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `IMPORTER_API_KEY` | (required) | Clients send it as `X-API-Key`. Without it every API call returns 503. |
| `INGEST_API_KEY` | (required) | Sent to the App as `X-Ingest-Key`. |
| `APP_INTERNAL_URL` | `http://app:8080` | Base URL of the App (`/api/internal/...` is appended). |
| `IMPORT_CONCURRENCY` | `2` | Parallel import jobs. |
| `TZ` | `UTC` | Timezone for `WATCH_RUN_TIME`. |
| `WATCH_RUN_TIME` | `03:00` | Daily watch run time (HH:MM, local to `TZ`). |
| `WATCH_SCAN_LIMIT` | `100` | Max items listed per watch per run. |
| `WATCH_MAX_PER_RUN` | `50` | Max imports queued per watch per run; the rest wait for the next run. |
| `WATCH_BACKFILL_MAX` | `500` | Cap for `backfill_all`. |
| `TRANSCODE_INCOMPATIBLE` | `true` | Transcode non H.264/AAC to H.264/AAC. MP4 faststart remux always happens. |
| `MAX_FILE_MB` | `4096` | Larger downloads fail with a clear message (0 = no limit). |
| `MAX_DURATION_MIN` | `240` | Longer videos fail with a clear message (0 = no limit). |
| `YTDLP_AUTO_UPDATE` | `false` | `true` runs `pip install -U "yt-dlp[default]"` on every container start. |
| `YTDLP_COOKIES_FILE` | `/data/cookies.txt` | Netscape cookies file, used only if it exists. |
| `DATA_DIR` | `/data` | `jobs.db` and `tmp/` (download scratch space, wiped on start). |
| `MEDIA_DIR` | `/media` | Shared media volume. |
| `LOG_LEVEL` | `INFO` | |

## Updating yt-dlp

Sites change and yt-dlp breaks. Either:

- rebuild the image to pick up the latest release: `docker compose build --no-cache importer && docker compose up -d importer`, or
- set `YTDLP_AUTO_UPDATE=true` so every start upgrades yt-dlp (it lives in a venv owned by the
  non-root user, so this works without root). `GET /api/health` reports the running version.

## API

Every endpoint except `GET /api/health` and the page at `/` needs `X-API-Key: $IMPORTER_API_KEY`.
Times are UTC ISO 8601 with a `Z` suffix. Errors are `{"detail": ...}` with 401/404/409/422/503.

```sh
KEY=your-importer-key
H="X-API-Key: $KEY"
BASE=http://localhost:8081
```

### Imports

```sh
# One URL (extra tags are added on top of the site's tags)
curl -s -X POST $BASE/api/imports -H "$H" -H 'Content-Type: application/json' \
  -d '{"url":"https://www.youtube.com/shorts/rYvbUYtXvME","tags":["animals"]}'
# -> 202 {"job_id": 1, "status": "queued", "job": {...}}

# A playlist or profile: one job per entry
curl -s -X POST $BASE/api/imports -H "$H" -H 'Content-Type: application/json' \
  -d '{"url":"https://www.youtube.com/@BBCEarthKids/shorts","expand_playlists":true,"max_items":5}'

# Batch
curl -s -X POST $BASE/api/imports/batch -H "$H" -H 'Content-Type: application/json' \
  -d '{"urls":["https://www.youtube.com/shorts/q0gReLz2efU","https://www.youtube.com/watch?v=0KVI7gBfeO8"],"tags":["test-import"]}'
# -> 202 {"job_ids": [2, 3], "status": "queued"}

curl -s "$BASE/api/imports?status=failed&limit=20" -H "$H"   # list, newest first (also ?watch_id=)
curl -s $BASE/api/imports/1 -H "$H"                           # one job
curl -s -X POST $BASE/api/imports/1/retry -H "$H"             # failed/skipped -> queued again
curl -s -X DELETE $BASE/api/imports/1 -H "$H"                 # cancel a queued/running job
curl -s $BASE/api/health
```

Job object:

```json
{
  "id": 1, "url": "https://www.youtube.com/shorts/rYvbUYtXvME",
  "status": "done", "progress": 100, "error": null,
  "detail": "Imported as video 1", "outcome": "imported", "stderr_tail": null,
  "video_id": 1, "tags": ["animals"], "expand_playlists": false, "max_items": 50,
  "parent_id": null, "watch_id": null, "watch_name": null,
  "source_domain": "youtube.com", "source_video_id": "rYvbUYtXvME", "title": "Seal vs Penguin | BBC Earth",
  "attempts": 1,
  "created_at": "2026-11-26T01:26:41Z", "started_at": "2026-11-26T01:26:41Z", "finished_at": "2026-11-26T01:26:52Z"
}
```

- `status`: `queued | running | done | failed | skipped`.
- `outcome` says why: `imported`, `expanded` (playlist parent, children have `parent_id`), `exists`
  (skipped, `video_id` is the existing video), `filtered` (skipped by a watch filter), `unavailable`,
  `live`, `limit`, `cancelled`, `ingest_error`, `app_error`, `error`.
- Failed yt-dlp runs keep the last stderr lines in `stderr_tail`.
- If ingest fails after the files were written, the files are kept; retrying the job only re-sends the ingest.

### Watches

```sh
# Watch a channel's Shorts tab, import only what appears from now on
curl -s -X POST $BASE/api/watches -H "$H" -H 'Content-Type: application/json' \
  -d '{"url":"https://www.youtube.com/@BBCEarthKids/shorts","tags":["kids"],"initial_mode":"new_only",
       "filters":{"max_duration_s":180,"vertical_only":true}}'
# -> 201 watch object (the first run starts immediately in the background), 409 {"detail","watch_id"} if already watched

# Or import the newest 3 straight away
curl -s -X POST $BASE/api/watches -H "$H" -H 'Content-Type: application/json' \
  -d '{"url":"https://www.youtube.com/playlist?list=PL2g6Hr5ZLeMYzO5-yOi6yZnR8KonfNZTc","initial_mode":"backfill_latest","backfill_count":3}'

curl -s $BASE/api/watches -H "$H"
curl -s "$BASE/api/watches?creator_source_domain=youtube.com&creator_handle=@BBCEarthKids" -H "$H"
curl -s $BASE/api/watches/1 -H "$H"
curl -s -X PATCH $BASE/api/watches/1 -H "$H" -H 'Content-Type: application/json' -d '{"enabled":false}'   # pause
curl -s -X PATCH $BASE/api/watches/1 -H "$H" -H 'Content-Type: application/json' \
  -d '{"enabled":true,"resume_mode":"import_missed"}'     # or "only_new"
curl -s -X POST $BASE/api/watches/1/run -H "$H"            # check now (202, background)
curl -s -X POST "$BASE/api/watches/1/run?wait=true" -H "$H" # check now and return the run
curl -s $BASE/api/watches/1/runs -H "$H"
curl -s "$BASE/api/watches/1/items?status=imported" -H "$H"
curl -s -X DELETE $BASE/api/watches/1 -H "$H"              # 204; imported videos are kept
```

`filters`: `max_duration_s`, `min_duration_s`, `title_include` / `title_exclude` (list or comma
separated string), `vertical_only` (checked when the job probes the video), `uploaded_after` (date).
`PATCH` accepts `name`, `tags`, `filters`, `enabled`, `resume_mode`, and before the first run also
`initial_mode` / `backfill_count`.

Watch object:

```json
{
  "id": 2, "url": "https://www.youtube.com/@BBCEarthKids/shorts", "kind": "channel",
  "name": "BBC Earth Kids - Shorts", "source_domain": "youtube.com",
  "enabled": true, "paused_at": null, "tags": [], "filters": {},
  "initial_mode": "new_only", "backfill_count": 20,
  "created_at": "2026-11-26T01:27:13Z", "last_checked_at": "2026-11-26T01:27:14Z",
  "last_success_at": "2026-11-26T01:27:14Z", "last_new_count": 0, "last_error": null,
  "consecutive_failures": 0, "next_run_at": "2026-11-26T16:00:00Z",
  "creator_source_domain": "youtube.com", "creator_handle": "@BBCEarthKids",
  "imported_count": 0, "erroring": false, "running": false,
  "item_counts": {"baseline": 12},
  "last_run": {"id": 2, "watch_id": 2, "trigger": "create", "started_at": "...", "finished_at": "...",
               "listed_count": 12, "new_count": 0, "queued_count": 0, "error": null}
}
```

Item statuses: `baseline`, `pending` (deferred by `WATCH_MAX_PER_RUN`), `queued`, `imported`,
`skipped_exists`, `filtered`, `failed`, `unavailable`. Failed items are retried automatically on the
next two runs; unavailable (private, removed, geo-blocked) ones are not. Live and upcoming videos are
not recorded, so they are picked up once they become normal videos. `erroring` is true after 3
failed runs in a row.

Behaviour notes:

- Kind (`channel | playlist | page`) is guessed from the URL and refined from the yt-dlp listing.
  Channel listings are newest first and scanning stops after 20 consecutive known IDs; playlists are
  scanned up to `WATCH_SCAN_LIMIT`.
- `backfill_latest` picks the newest by upload timestamp when the listing has dates; otherwise the
  first N of a channel listing or the last N of a playlist (playlists append new videos at the end).
- Duplicate URLs are rejected after normalising (host, `www.`/`m.`, trailing slash, tracking
  parameters, `/videos` and `/featured` channel tabs are the channel itself; a Shorts tab is separate).
- For channel watches `creator_source_domain` / `creator_handle` come from the URL and the listing,
  then from the first imported video's creator handle (exactly what the App stores).

## What the Importer sends to the App

- `GET {APP_INTERNAL_URL}/api/internal/exists?source_domain=&source_video_id=` with `X-Ingest-Key`,
  expects `{"exists": bool, "video_id": int|null}`.
- `POST {APP_INTERNAL_URL}/api/internal/ingest` with `X-Ingest-Key` and the payload from DESIGN 11.5,
  expects `{"video_id": int}`. Network errors, 5xx and 429 are retried with backoff (2, 5, 15, 30,
  60, 120 s); other 4xx fail the job immediately with the response body in `error`.
- Files (paths in the payload are relative to `/media`): `videos/<domain>/<id>.mp4`,
  `posters/<domain>/<id>.jpg`, `metadata/<domain>/<id>.json`, `avatars/<domain>/<handle>.jpg`
  (handle without `@`, sanitised to `[A-Za-z0-9._-]`).
- The sidecar JSON is the sanitised yt-dlp info dict plus an `_import` block: `imported_at`,
  `importer_version`, `ytdlp_version`, `request_tags`, `watch_id`, `job_id`, `source`, `creator`,
  `file` (same object as the payload), `poster_path`, `info_json_path`, `transcoded`, `ffprobe`.

## Tests

```sh
docker build --target test -t tastebud-importer-test importer
docker run --rm tastebud-importer-test
```

yt-dlp, ffmpeg, the App and HTTP downloads are replaced by fakes (`tests/conftest.py`), and the
scheduler clock and inter-watch gap are injected, so the suite runs offline in a few seconds.
