# Tastebud

A mobile-first, installable web app that combines a full screen vertical video feed with Tinder-style swipe rating. Likes and dislikes build per-user scores for tags, creators and sources, which drive a personalised feed. Videos come in through a separate Importer service built on yt-dlp.

The full build brief is [DESIGN.md](DESIGN.md). The player mockup is [mockup-player.html](mockup-player.html).

## Tech stack (and why)

| Part | Choice | Reasoning |
|---|---|---|
| App API | Python 3.12, FastAPI, SQLAlchemy 2, Alembic, SQLite (WAL) | One language for both services; the Importer has to be Python for yt-dlp. FastAPI gives typed request validation and an OpenAPI page (`/api/docs`). SQLite with Alembic migrations meets "embedded DB, no DB server" and is fast enough for tens of thousands of videos. |
| Search | In-memory index + RapidFuzz (Damerau-Levenshtein) | The searchable set is names only, so ranking in memory is simpler than FTS and easily hits the 50 ms target. |
| Auth | argon2 (argon2-cffi), server-side sessions in httpOnly SameSite=Lax cookies, double-submit CSRF token | Matches DESIGN.md 8 and 14. |
| Frontend | React 18, TypeScript, Vite, `vite-plugin-pwa` (Workbox), react-router | Small bundle, fast dev loop, first-class PWA tooling. Gestures are hand-written with Pointer Events and direct DOM transforms for 60 fps swipes. |
| Importer | Python 3.12, FastAPI, SQLAlchemy/Alembic + SQLite job queue, asyncio workers, yt-dlp, ffmpeg/ffprobe | See [importer/README.md](importer/README.md). |
| Tests | pytest (both services), Playwright with iPhone 13 and Pixel 5 emulation | DESIGN.md 2 and 15. |

Both images run their migrations on start and run the server process as uid 1000 (the entrypoint fixes ownership of the bind mounts, then drops root with `setpriv`).

## Repository layout

```
app/
  backend/        FastAPI app (vidswipe/), Alembic migrations, pytest suite
  frontend/       React PWA, Playwright e2e tests (e2e/)
  Dockerfile      multi-stage: build frontend, copy into the backend image
importer/         Importer service (see importer/README.md)
docker-compose.yml
docker-compose.e2e.yml
.env.example
```

## Unraid

Ready-made images are published to GitHub Container Registry by `.github/workflows/docker-publish.yml`. See [unraid/README.md](unraid/README.md) for setting up with the Docker Compose Manager plugin.

## Running

```sh
cp .env.example .env      # set ADMIN_PASSWORD, SESSION_SECRET, INGEST_API_KEY, IMPORTER_API_KEY
docker compose build
docker compose up -d
```

Open `http://<host>:8080` and log in as `admin` with `ADMIN_PASSWORD`. The app expects to sit behind a TLS reverse proxy (SWAG / nginx): PWA install and service workers need HTTPS, and session cookies are `Secure` by default. For plain-http testing on a LAN set `COOKIE_SECURE=false`.

Example nginx location:

```nginx
location / {
    proxy_pass http://tastebud-app:8080;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;        # streamed video range responses
}
```

### Importing videos

From the app: Account > Open admin > Import, paste URLs. From a script:

```sh
curl -X POST http://<host>:8081/api/imports \
  -H "X-API-Key: $IMPORTER_API_KEY" -H "Content-Type: application/json" \
  -d '{"url": "https://www.youtube.com/shorts/rYvbUYtXvME", "tags": ["animals"]}'
```

`test-import-urls.json` has a batch of test URLs.

### Updating yt-dlp

Sites change and yt-dlp breaks. Either set `YTDLP_AUTO_UPDATE=true` (updates on container start) or rebuild: `docker compose build --no-cache importer && docker compose up -d importer`.

## Backups

Back up `./data/app/app.db` (ratings, collections, moods, users) and `./data/media` (videos, posters, avatars and the metadata sidecar JSON). If the database is lost, Admin > Maintenance > "Rebuild from media" recreates videos, creators, sources and tags from the sidecars; ratings, collections and moods need the database backup. Use `sqlite3 app.db ".backup app-backup.db"` for a consistent copy while running.

## Tests

```sh
# App backend (pytest)
docker build -t tastebud-app-test -f- app/backend <<'EOF'
FROM python:3.12-slim
WORKDIR /src
COPY requirements*.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY . .
CMD ["python", "-m", "pytest", "-q"]
EOF
docker run --rm tastebud-app-test

# Importer (pytest)
docker build --target test -t tastebud-importer-test importer && docker run --rm tastebud-importer-test

# Mobile E2E (Playwright, iPhone + Pixel emulation)
docker compose -f docker-compose.e2e.yml up --build --abort-on-container-exit --exit-code-from e2e
docker compose -f docker-compose.e2e.yml down -v
```

## Using the app

- **Feed**: swipe right = like, left = dislike (both advance), up/down = next/previous without rating. Tap to pause. Heart = Favorites (does not rate or advance). Keyboard: Left/Right rate, Up/Down navigate, Space pause, M mute, I details, F favorite.
- **Library**: Tags / Creators / Sources / Moods with scores, sorting and fuzzy search; tap a row for its feed; the row menu adds it to a mood (a group of tags, creators and sources with its own feed).
- **Collections**: Favorites (manual), Liked (automatic), and your own playlists with drag to reorder.
- **Admin**: videos, creators (merge, avatar, watch toggle), tags (rename, merge with preview, aliases, duplicates, undo, watch a tag page on any source), sources, import, watches, maintenance. Tags, creators and sources sort like the library.

## Configuration

All variables are documented in [.env.example](.env.example).
