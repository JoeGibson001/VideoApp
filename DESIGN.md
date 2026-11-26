# Tastebud: Design Document (formerly the working name VidSwipe)

Companion file: `mockup-player.html` is an interactive mockup of the video player. Open it in a browser and use it as the visual reference for the feed screen (layout, overlays, swipe behaviour, details sheet, save sheet).

A mobile-first, installable web app that combines full screen vertical video scrolling with Tinder-style swipe rating. Ratings feed per-user scores for tags, creators and sources, which drive a personalised feed. Videos are brought in by a separate importer service built on yt-dlp.

This document is the build brief for Claude Code. It covers two applications that must both be built:

1. **App**: the web app (API + PWA frontend).
2. **Importer**: a separate Docker service that downloads videos with yt-dlp, extracts metadata and hands them to the App.

---

## 1. Goals and scope

### In scope (v1)

- Full screen vertical video feed, fitted to the screen without stretching.
- Swipe right = like, swipe left = dislike, both auto-advance to the next video.
- Vertical scroll up/down to go back or skip without rating.
- Per-user scores for tags, creators and sources, derived from likes and dislikes.
- Sortable list views for tags, creators and sources.
- User-defined categories (groups of tags, creators and sources).
- User-defined collections (playlists), plus two system collections: "Favorites" (manual) and "Liked" (automatic, every liked video).
- Filtered feeds for a tag, creator, source, category or collection.
- Expandable details panel for each video.
- Single seeded user `admin`; anonymous use allowed (ratings not saved).
- Admin pages to manage creators, tags, sources and videos.
- PWA installable via the browser's "Install" / "Add to home screen".
- Importer service with a REST API that accepts URLs and imports via yt-dlp.
- Both services run in Docker via one `docker-compose.yml`.

### Out of scope (later phases)

- Public sign-up and multiple users (the data model must already support this).
- User uploads.
- Comments, follows, sharing between users.

---

## 2. Architecture

```
            +-------------------------+
 Browser -> | Reverse proxy (TLS)     |   e.g. existing SWAG/nginx
 (PWA)      +-----------+-------------+
                        |
                        v
            +-------------------------+        +---------------------------+
            | app (web server)        | <----- | importer (API + worker)    |
            |  - REST API /api/*      | ingest |  - REST API /api/imports  |
            |  - serves PWA build     |  API   |  - yt-dlp, ffmpeg/ffprobe |
            |  - streams /media/*     |        |  - job queue (SQLite)     |
            |  - SQLite (app.db)      |        +-------------+-------------+
            +-----------+-------------+                      |
                        |                                    |
                        +---------- shared volume -----------+
                                     /media
```

- The **App owns the main database**. The Importer never writes to it directly; it writes media files to the shared `/media` volume and then calls the App's internal ingest endpoint with the metadata.
- The Importer has its own small SQLite DB for its job queue.
- Service-to-service auth uses a shared secret (`INGEST_API_KEY`).
- HTTPS is required for PWA install and service workers. Assume the app sits behind a TLS reverse proxy.

### Tech stack

Claude Code chooses the stack. Before writing code, state the chosen stack and the reasoning in the README, and keep it consistent across both services where practical. Constraints the choice must meet:

- Each service ships as a single Docker image; the whole system starts with `docker compose up`.
- Embedded database (e.g. SQLite) with schema migrations; no separate DB server required for v1.
- The Importer must run `yt-dlp`, `ffmpeg` and `ffprobe` (a Python base image is the simplest fit for yt-dlp).
- Frontend must be a PWA with smooth touch gestures and swipe animations on mobile.
- Media endpoint must support HTTP Range requests.
- Passwords hashed with a modern algorithm (argon2 or bcrypt); sessions in httpOnly cookies.
- Automated tests for backend logic and mobile-emulated E2E tests (e.g. Playwright) for the swipe interactions.

For reference, a stack that meets all of these: Python FastAPI + SQLAlchemy/Alembic + SQLite for both services, React + TypeScript + Vite with `vite-plugin-pwa` for the frontend.

---

## 3. Data model (App)

All user-specific data is keyed by `user_id` so multi-user sign-up can be added later without schema changes.

### Core content

**users**: `id`, `username` (unique), `password_hash`, `is_admin`, `created_at`

**sources**: `id`, `name` (display, e.g. "YouTube"), `domain` (unique, e.g. `youtube.com`), `icon_path`, `created_at`

**creators**: `id`, `source_id`, `name` (display name), `handle` (site username/ID), `profile_url`, `avatar_path` (nullable), `created_at`
- Unique on (`source_id`, `handle`).
- Avatar fallback: generated initials avatar if `avatar_path` is null.

**videos**:
- `id`, `title`, `description`, `info_json_path` (path to the yt-dlp metadata sidecar, see 11.6)
- `creator_id`, `source_id`
- `source_url` (original page URL), `source_video_id` (site's ID), unique on (`source_id`, `source_video_id`)
- `file_path` (relative to `/media`), `filename` (original/stored filename), `poster_path`
- `size_bytes`, `width`, `height`, `duration_s`, `fps`, `video_codec`, `audio_codec`, `has_audio` (bool), `container`
- `upload_date` (from the site), `imported_at`
- `is_hidden` (admin soft-hide)

**tags**: `id`, `name` (unique, normalised: trimmed, lowercased, internal whitespace collapsed), `created_at`

**video_tags**: (`video_id`, `tag_id`) primary key, `origin_name` (the tag name exactly as it arrived from the site, import request or admin edit, before alias resolution), `origin` (`site` | `import_request` | `admin`)
- `origin_name` is what lets merges be undone and aliases be removed cleanly later (see 9.1).

**tag_aliases**: `alias` (unique, normalised), `tag_id`, `created_at`
- Created automatically when a tag is merged (the old name becomes an alias of the target). Can also be added by hand in admin.
- Ingest and tag editing resolve names through this table first, so a merged tag never comes back on a later import.

**creator_aliases**: (`source_id`, `handle`) unique, `creator_id`
- Same idea for creators, so a merged creator's handle maps to the surviving creator on future imports.

**merge_log**: `id`, `entity_type` (`tag` | `creator`), `from_ids`, `from_names`, `into_id`, `snapshot` (JSON of the rows changed, for undo), `merged_by`, `merged_at`, `undone_at`

### Ratings and scores

**ratings**: (`user_id`, `video_id`) primary key, `value` (+1 or -1), `rated_at`, `updated_at`

Score aggregates (one row per user per item), updated in the same DB transaction as the rating change:

- **user_tag_stats**: (`user_id`, `tag_id`), `likes`, `dislikes`, `score` (= likes - dislikes), `last_rated_at`
- **user_creator_stats**: same shape, keyed by `creator_id`
- **user_source_stats**: same shape, keyed by `source_id`

Sources were not explicitly asked to be scored, but tracking them costs nothing and makes the Sources list and the feed algorithm consistent with tags and creators.

### Organisation

**categories**: `id`, `user_id`, `name`, `description`, `created_at`, `updated_at`

**category_items**: `id`, `category_id`, `item_type` (`tag` | `creator` | `source`), `item_id`; unique on (`category_id`, `item_type`, `item_id`)
- A tag/creator/source can be in many categories.

**collections**: `id`, `user_id`, `name`, `system_type` (null | `favorites` | `liked`), `created_at`, `updated_at`
- Every user gets two system collections on creation: "Favorites" and "Liked". Neither can be renamed or deleted.
- **Favorites** is managed manually by the user (heart button).
- **Liked** is managed automatically by the rating logic (section 4). Users cannot manually add videos to it or reorder it; it is always ordered by most recently liked first. Removing a video from Liked is done by clearing or flipping its rating (the UI "Remove" action on a Liked item clears the rating, with a confirm prompt).

**collection_videos**: (`collection_id`, `video_id`) primary key, `position`, `added_at`

---

## 4. Rating rules

| Action | Effect |
|---|---|
| Swipe right on unrated video | Insert rating +1. For each tag on the video, plus its creator and source: `likes += 1`, `score += 1`. |
| Swipe left on unrated video | Insert rating -1. `dislikes += 1`, `score -= 1` on each. |
| Swipe opposite way on a rated video | Reverse the old rating's effect, then apply the new one (net change of 2 to score). |
| Swipe the same way on a rated video | No-op (show a small "already liked/disliked" toast). |
| "Clear rating" in details panel | Reverse the old rating's effect and delete the rating row. |

- Ratings are per user. Scores are per user.
- If a tag is added to or removed from a video later (admin edit or re-import), recompute stats for affected tags from the `ratings` table. Provide an admin "Recompute all stats" action that rebuilds every stats table from `ratings` (also used as a consistency check in tests).
- Liked collection sync, in the same transaction as the rating change:
  - Rating becomes +1 -> add the video to the user's Liked collection (`added_at` = rated time).
  - Rating changes from +1 to -1, or is cleared -> remove the video from Liked.
- Liking does **not** add to Favorites. Favorites is a separate action (heart button).
- Favoriting is independent of rating: it does not like or dislike the video, does not change any scores, and does **not** advance the feed. A video can be favorited and liked, favorited and disliked, or favorited and unrated.
- "Recompute all stats" also rebuilds the Liked collection from the `ratings` table.

---

## 5. Feed behaviour

### 5.1 Layout

- One video per screen. Container height `100dvh`, width `100vw`.
- Video uses `object-fit: contain` on a black background (letterbox/pillarbox, never stretched or cropped).
- Respect safe-area insets (`env(safe-area-inset-*)`) so overlays clear notches and home indicators.
- Overlay at the bottom (above the nav bar), on a subtle dark gradient for legibility:
  - Creator avatar (circular) + username. Tap -> that creator's feed.
  - Up to 3 tag chips plus a "+N" chip. Tap a tag -> that tag's feed.
  - Expand button (chevron or info icon) -> opens the details sheet.
- Right-side action rail (small, vertical): Favorite (heart), Add to collection, Mute toggle.
  - Tapping the heart toggles Favorites in place: the heart fills/unfills with a short pop animation, the video keeps playing from the same position, and the feed does **not** scroll. The user can then still swipe right to like (or left to dislike) as normal.
  - "Add to collection" and the details sheet behave the same way: the video keeps playing and the feed stays put.
  - Only a committed left/right swipe (or the like/dislike keys/buttons) advances the feed.
- Rating indicator: if the current user has rated the video, show a small badge (green thumbs up / red thumbs down) near the top.

### 5.2 Gestures

Axis lock: after the first ~10px of movement, lock the gesture to horizontal or vertical for the rest of that touch.

**Horizontal (rating)**
- The video card follows the finger horizontally with a slight rotation (Tinder style).
- A "LIKE" (green, right) or "NOPE" (red, left) stamp fades in proportional to drag distance.
- Commit when drag passes 30% of screen width OR flick velocity exceeds a threshold.
- On commit: card animates off-screen in the swipe direction, rating is saved (optimistic UI, retried on failure), then the feed animates to the next video.
- Below threshold: card springs back, nothing is saved.
- Light haptic tap on commit where supported (`navigator.vibrate`).

**Vertical (navigation)**
- Swipe up = next video, swipe down = previous video. Snap one video at a time.
- No rating is recorded. Scrolling back shows previously seen videos (including disliked ones) with their rating badge, and they can be re-rated.

**Taps**
- Single tap: play/pause.
- Tap on overlay elements: their own action (do not toggle play).

**Desktop / keyboard**
- Right arrow = like, Left arrow = dislike, Up/Down arrows and mouse wheel = navigate, Space = play/pause, M = mute, I = toggle details.
- Visible like/dislike buttons on wide screens (hidden on touch devices).

### 5.3 Playback

- Autoplay the current video muted (browser policy). The first user tap on the mute toggle unmutes, and the choice persists for the session.
- Videos loop.
- Only the current video plays; all others are paused.
- Virtualise the list: keep roughly 5 video elements in the DOM (previous 2, current, next 2). Next video uses `preload="auto"`, others `preload="metadata"`. Use `poster` images for instant display.
- Show a buffering spinner over the poster while loading.
- Videos with `has_audio = false` show a "no sound" icon instead of the mute toggle.

### 5.4 Feed types and ordering

| Feed | Route | Contents | Default order |
|---|---|---|---|
| Main | `/` | All visible videos the user has **not** rated | Personalised (below) |
| Tag | `/feed/tag/:id` | Videos with that tag | Personalised, rated videos included |
| Creator | `/feed/creator/:id` | Videos by that creator | Newest first |
| Source | `/feed/source/:id` | Videos from that source | Personalised |
| Category | `/feed/category/:id` | Union of videos matching any tag, creator or source in the category | Personalised |
| Collection | `/feed/collection/:id` | Videos in the collection | Collection order (Liked: most recently liked first) |

- Filtered feeds show a pill at the top ("Tag: drift" etc.) with a back/close button.
- Filtered feeds have a toggle "Unrated only".
- Main feed excludes already-rated videos (assumption, see Open Questions). When the main feed runs out, show an end card with links to Tags / Creators and an option to play the Liked collection.

### 5.5 Personalised ordering (v1 algorithm)

Keep this simple and tunable via config.

For each candidate video `v` for user `u`:

```
tag_component     = mean over v.tags of tanh(tag_score / K)        (0 if no rated tags)
creator_component = tanh(creator_score / K)
source_component  = tanh(source_score / K)

score(v) = W_TAG * tag_component
         + W_CREATOR * creator_component
         + W_SOURCE * source_component
         + W_FRESH * freshness(v.imported_at)
         + W_RANDOM * random()
```

Defaults: `K = 5`, `W_TAG = 1.0`, `W_CREATOR = 0.8`, `W_SOURCE = 0.2`, `W_FRESH = 0.2`, `W_RANDOM = 0.5`.

- Exploration: every 5th slot is a random unrated video regardless of score, so new tags and creators still surface.
- Pagination: `GET /api/feed?limit=10&exclude=<ids already served this session>`. The client passes IDs it has already received so pages don't repeat.
- Anonymous users get a random/newest mix (no personalisation).
- This only needs to perform well up to tens of thousands of videos on SQLite. Compute in SQL where practical, otherwise score a bounded candidate set (e.g. 500 random unrated) in Python.

### 5.6 Details sheet

Bottom sheet that slides up over the video (video keeps playing). Shows:

- Title and description
- Creator (avatar, name, handle, link to original profile) -> creator feed
- Source (name, icon) and link to the original video URL
- All tags (each tappable; also shows your score for that tag)
- Filename, file size (human readable), dimensions (W x H), aspect ratio, duration, FPS, video codec, audio codec, has sound (yes/no)
- Upload date (original), imported date
- Your rating, with "Clear rating" action
- Collections this video is in, with "Add to collection"
- Admin only: "Edit metadata", "Hide video"

---

## 6. Library views

### 6.1 Tags, Creators, Sources lists

Same component for all three, under a "Library" tab with segmented control: Tags | Creators | Sources | Categories.

Each row shows: name (and avatar for creators, icon for sources), net score (green positive, red negative, grey zero), likes / dislikes counts, number of videos.

Row actions: tap -> filtered feed. Overflow menu -> "Add to category".

Search box at the top filters the list live as you type, using the fuzzy search rules in section 6.4 (results ordered by match quality while a search term is present, then by the chosen sort when the box is cleared).

Sort options:
- Score, high to low (**default**)
- Score, low to high
- Name A to Z
- Name Z to A
- Newest (most recently created/first imported)
- Oldest
- Most videos
- Most recently rated

Logged-out users see the lists with video counts only (no scores), sorted by name by default.

### 6.2 Categories

- List of the user's categories with item count and combined score (sum of member scores).
- Create / rename / delete.
- Category detail page: three sections (Tags, Creators, Sources) with chips; "Add items" opens a searchable picker across all three types; remove item; "Play feed" button.

### 6.3 Collections

Separate "Collections" tab.

- Favorites and Liked always shown first (Favorites, then Liked), cannot be deleted or renamed. Liked shows a "Liked automatically" label and has no reorder or manual add.
- Create / rename / delete collections.
- Collection detail: grid of video posters, reorder (drag), remove video, "Play" -> collection feed.
- From the feed: heart toggles Favorites; "Add to collection" opens a sheet with checkboxes for each collection plus "New collection".

---

### 6.4 Search

Search is available from a search icon at the top right of the feed and at the top of the Library and Collections tabs.

**Behaviour**
- Results populate as you type (debounced around 150ms, cancel in-flight requests when a new character is typed). Starts from the first character.
- Searches tags, creators, sources and collections (and categories). Results are grouped by type with a filter chip row: All, Tags, Creators, Sources, Collections, Categories.
- In "All" mode, show the top 5 per group with a "Show all" link per group; in a single-type mode, show a full scrolling list.
- Each result shows the same row as the Library lists (name, avatar/icon, score, video count) with the matched characters highlighted.
- Tapping a result opens its feed (or the collection/category page).
- Recent searches are shown when the box is empty (stored per user, last 10).
- Logged-out users can search tags, creators and sources (not collections or categories).

**Fuzzy matching and ranking**

Matching is case-insensitive and ignores accents, punctuation and spaces/hyphens/underscores (so "hip hop", "hip-hop" and "hiphop" match each other). Each candidate gets a match score; results are sorted by match score descending, then by the user's item score, then by video count, then name.

| Rank | Match type | Example (query "car") |
|---|---|---|
| 1 | Exact match (after normalisation) | `car` |
| 2 | Exact match on an alias | `cars` merged into `car`, query `cars` |
| 3 | Name starts with query | `cars`, `carbon` |
| 4 | A word in the name starts with query | `rc car`, `@fast_cars` |
| 5 | Query appears anywhere in the name | `racecar` |
| 6 | Fuzzy match (typos, transpositions, missing letters) | query `drfit` -> `drift`, `cokking` -> `cooking` |
| 7 | Subsequence match (letters in order) | query `jdmc` -> `jdm classic` |

- Fuzzy matching uses a similarity score (e.g. normalised Damerau-Levenshtein or trigram similarity) with a threshold so junk doesn't appear. Allowed typos scale with query length: 0 for 1 to 3 characters, 1 for 4 to 6, 2 for 7 or more.
- Creators match on display name **and** handle. Tags match on name **and** aliases. Sources match on name and domain.
- Within a rank, shorter names that cover more of the query rank higher.

**Implementation guidance**
- One endpoint: `GET /api/search?q=&types=&limit=` returning grouped, ranked results.
- The searchable set is small (names only, likely tens of thousands at most), so a simple approach is fine: keep an in-memory index of normalised names per type, refreshed on change, and score with a fuzzy library (e.g. RapidFuzz in Python, or an equivalent). SQLite FTS5 with the trigram tokenizer is an acceptable alternative.
- Target under 50ms server time per keystroke query.
- The Library list search boxes (6.1) and the admin pickers (category item picker, tag editor, merge dialog) all use this same endpoint/ranking.

## 7. Navigation and screens

Bottom tab bar (translucent over the feed, solid elsewhere): **Feed**, **Library**, **Collections**, **Account**.

| Screen | Notes |
|---|---|
| Feed | Default on launch. Starts on the first video of the main feed. See `mockup-player.html` for the player layout and gestures. |
| Search | Opened from the search icon (feed top right, Library, Collections). See 6.4. |
| Library | Tags / Creators / Sources / Categories lists |
| Collections | Collections list and detail |
| Account | Login/logout, mute default, autoplay setting, (admin) link to Admin |
| Admin | Admin-only. See section 9. |

Dark theme by default (suits a video app). Touch targets at least 44px.

---

## 8. Auth and anonymous mode

- On first startup, seed user `admin` with password from `ADMIN_PASSWORD` env var (fail to start if unset and no admin exists). `is_admin = true`. Create its Favorites and Liked collections.
- Login page at `/login`. Session cookie: httpOnly, Secure, SameSite=Lax, configurable lifetime (default 30 days).
- No sign-up UI in v1, but keep user creation in a service function so a sign-up endpoint can be added later.
- **Anonymous users**:
  - Can browse the feed, library lists and filtered feeds.
  - Swipe gestures still work and advance the feed; ratings are held in client memory for the session only (so the rating badge shows when scrolling back) and are not sent to the server.
  - A small dismissible banner: "Log in to save your likes".
  - Collections (including Liked and Favorites), categories, favorites and "Add to collection" prompt to log in.
- Rate limit the login endpoint.

---

## 9. Admin (App)

Admin-only pages and APIs. Creators are managed here, not by users.

- **Videos**: searchable table; edit title, description, creator, source, tags; hide/unhide; delete (removes file and DB rows, recomputes stats).
- **Creators**: list; edit display name, handle, profile URL; upload/replace avatar; merge creators (see 9.1).
- **Tags**: list; rename; merge (see 9.1); delete; view and edit aliases; "Possible duplicates" suggestions.
- **Sources**: list; edit display name and icon.
- **Import**: paste one or more URLs and send them to the Importer API (the App proxies the request using `IMPORTER_API_KEY`). Shows recent import jobs and their status.
- **Watches**: list, add, edit, pause, delete and "Check now" for channel/playlist watches (proxied to the Importer, see 11.7). Shows last checked time, new videos found, and any watch that is failing. Watch status is also shown and toggled directly on creators (see 11.8).
- **Maintenance**: "Recompute all stats", "Rescan media" (finds files on disk with no DB row and vice versa), "Rebuild from media" and "Re-read metadata" (both use the sidecar JSON, 11.6).

### 9.1 Tag merging

Merging folds one or more tags (the "from" tags) into a single surviving tag (the "into" tag), e.g. `dogs`, `doggo` and `Dog` into `dog`.

**Flow (admin UI)**
1. On the Tags admin page, select one or more tags and choose "Merge into...", or open a tag and choose "Merge another tag into this".
2. Pick the target with the fuzzy search picker.
3. A preview shows: videos affected, how many videos already have both tags, categories affected, and the target's score before and after for each user.
4. Confirm. The merge runs in a single DB transaction.

**What the merge does**
1. `video_tags`: re-point every row from the "from" tags to the "into" tag. Where a video already has the "into" tag, drop the duplicate row (a video never has the same tag twice).
2. `category_items`: re-point tag items to the "into" tag; drop duplicates where a category already contains it.
3. `tag_aliases`: add each "from" tag's name (and any aliases it already had) as aliases of the "into" tag.
4. Stats: delete `user_tag_stats` rows for the "from" tags and **recompute** the "into" tag's stats from the `ratings` table for every user. Do not simply add the numbers together, because a video that had both tags would be counted twice.
5. Delete the "from" tags.
6. Write a `merge_log` entry with a snapshot of everything changed.
7. Refresh the search index.

**Afterwards: when a merged tag name turns up again**

Every place a tag name enters the system (importer site tags, tags sent with an import request, admin tag edits, rename) goes through one resolver function:

1. Normalise the name.
2. If a tag with that exact name exists, use it.
3. Else if the name is in `tag_aliases`, use the tag it points to.
4. Else create a new tag.

So after merging `dogs` into `dog`, a new import tagged `dogs` gets `dog` attached and no `dogs` tag is recreated. The `video_tags` row stores `origin_name = "dogs"` so the original label is never lost. If an import brings both `dogs` and `dog`, the video gets `dog` once.

Related rules:
- **Chained merges**: if `dog` is later merged into `canine`, all of `dog`'s aliases (`dogs`, `doggo`) are re-pointed to `canine`, so there is never an alias pointing at a deleted tag.
- **Creating a tag that is an alias**: if the admin types `dogs` when editing a video's tags or creating a tag, the UI shows "`dogs` is merged into `dog`" and uses `dog`. To make `dogs` a separate tag again, remove the alias first.
- **Removing an alias** (admin, tag detail page): stops future mapping. The admin is offered "Also split existing videos": every `video_tags` row for the target with `origin_name = dogs` is moved to a recreated `dogs` tag, then stats for both tags are recomputed from ratings.
- **Undo**: the most recent merges can be undone from the merge log. Undo restores the deleted tags, their category links and aliases, and moves back every `video_tags` row whose `origin_name` matches a "from" tag name or alias, **including videos imported after the merge**. Stats are then recomputed. Undo is blocked if the "into" tag has since been merged or deleted.
- Searching `dogs` finds `dog` (alias match, rank 2 in 6.4).
- Duplicate suggestions ignore pairs that are already linked by an alias.

Creator handles work the same way through `creator_aliases`: a new import from a merged handle attaches to the surviving creator.

**Duplicate suggestions**
The Tags admin page has a "Possible duplicates" panel that lists likely pairs using the same normalisation and fuzzy rules as search: singular/plural (`dog` / `dogs`), punctuation and spacing (`hip-hop` / `hip hop`), and close spellings (`colour` / `color`). Each suggestion has "Merge" and "Not a duplicate" (dismissed pairs are remembered).

**Rename vs merge**: renaming a tag to a name that already exists is treated as a merge and goes through the same preview.

**Creators** use the same merge process: videos move to the surviving creator, the old handles become `creator_aliases`, `user_creator_stats` are recomputed from ratings, category items are re-pointed, and the merge is logged and undoable. Creators can only be merged within the same source.

---

## 10. App API (summary)

All under `/api`. JSON. Authenticated endpoints use the session cookie.

```
POST   /auth/login                 {username, password}
POST   /auth/logout
GET    /auth/me

GET    /feed?type=main|tag|creator|source|category|collection&id=&unrated_only=&limit=&exclude=
GET    /videos/:id                 full details incl. user's rating
PUT    /videos/:id/rating          {value: 1 | -1}
DELETE /videos/:id/rating

GET    /search?q=&types=tag,creator,source,collection,category&limit=
GET    /search/recent              DELETE /search/recent

GET    /tags?sort=&q=&page=
GET    /creators?sort=&q=&page=
GET    /sources?sort=&q=&page=
GET    /tags/:id, /creators/:id, /sources/:id

GET    /categories                 POST /categories
GET    /categories/:id             PATCH /categories/:id    DELETE /categories/:id
POST   /categories/:id/items       {item_type, item_id}
DELETE /categories/:id/items/:itemId

GET    /collections                POST /collections
GET    /collections/:id            PATCH /collections/:id   DELETE /collections/:id
POST   /collections/:id/videos     {video_id}
DELETE /collections/:id/videos/:videoId
PUT    /collections/:id/order      {video_ids: [...]}

# Admin
/admin/videos, /admin/creators, /admin/tags, /admin/sources   (CRUD)
GET    /admin/tags/merge-preview?from=1,2&into=3
POST   /admin/tags/merge           {from_ids: [...], into_id}
POST   /admin/creators/merge       {from_ids: [...], into_id}
GET    /admin/merges               merge log
POST   /admin/merges/:id/undo
GET    /admin/tags/duplicates
POST   /admin/tags/duplicates/dismiss  {tag_a, tag_b}
POST   /admin/import               {urls: [...], tags?: [...]}  -> proxies to Importer
POST   /admin/recompute-stats
POST   /admin/rescan-media

# Internal (Importer -> App), header X-Ingest-Key
POST   /internal/ingest            (payload in section 11.5)
GET    /internal/exists?source_domain=&source_video_id=
```

Media:

```
GET /media/videos/...   must support HTTP Range requests (206 Partial Content). Required for seeking and iOS Safari.
GET /media/posters/...
GET /media/avatars/...
```

Add an automated test that confirms a Range request returns 206 with the correct `Content-Range`. If the framework's file response doesn't handle ranges correctly, serve `/media` via a lightweight static server or the reverse proxy instead.

---

## 11. Importer service

A separate application in its own Docker container. Its main job: accept URLs over an API, download with yt-dlp, extract metadata, and hand the result to the App.

### 11.1 API

Auth: `X-API-Key: <IMPORTER_API_KEY>` header on every request.

```
POST   /api/imports            {url, tags?: [...], expand_playlists?: bool, max_items?: int}
                               -> 202 {job_id, status}
POST   /api/imports/batch      {urls: [...], tags?: [...]}  -> 202 {job_ids: [...]}
GET    /api/imports?status=queued|running|done|failed|skipped&limit=
GET    /api/imports/:id        status, progress %, error, resulting video_id
POST   /api/imports/:id/retry
DELETE /api/imports/:id        cancel if queued/running
GET    /api/health

# Watches (see 11.7)
GET    /api/watches?creator_source_domain=&creator_handle=   (filter: watches for one creator)
POST   /api/watches                {url, name?, tags?, initial_mode, backfill_count?, filters?, enabled?}
GET    /api/watches/:id            details, stats, last run result
PATCH  /api/watches/:id            change name, tags, filters, enabled
DELETE /api/watches/:id            stop watching (already imported videos are kept)
POST   /api/watches/:id/run        check now
GET    /api/watches/:id/runs       run history
GET    /api/watches/:id/items      every video ID the watch has seen and what happened to it
```

- Designed to be called from scripts, iOS Shortcuts, Android share apps, or the App's admin Import page.
- `tags` in the request are added on top of the site's tags.
- If the URL is a playlist or profile and `expand_playlists` is true, expand into one job per video (capped by `max_items`, default 50).

### 11.2 Job pipeline

1. **Probe**: `yt-dlp --dump-single-json --no-download <url>` to get metadata and the site's video ID.
2. **Dedup**: call App `GET /internal/exists`. If it already exists, mark job `skipped`.
3. **Download**: prefer browser-safe formats:
   `-f "bv*[vcodec^=avc1][ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b" --merge-output-format mp4`
   Download to a temp dir inside the importer volume.
4. **Transcode (optional)**: if `TRANSCODE_INCOMPATIBLE=true` and the codec is not H.264/AAC in MP4, transcode with ffmpeg (`libx264`, `aac`, `+faststart`). Always remux with `-movflags +faststart` so playback starts before the full file loads.
5. **Inspect**: `ffprobe` for width, height, duration, fps, codecs, size, and whether an audio stream exists (`has_audio`).
6. **Poster**: use the site thumbnail if available, otherwise grab a frame at 10% of duration with ffmpeg. Save as JPEG.
7. **Creator avatar**: try to fetch the uploader/channel avatar if yt-dlp exposes one for that extractor; otherwise leave null (App shows initials).
8. **Write metadata sidecar**: save yt-dlp's metadata JSON (see 11.6).
9. **Move** files into the shared volume:
   `/media/videos/<source_domain>/<source_video_id>.mp4`, `/media/metadata/<source_domain>/<source_video_id>.json`, `/media/posters/<source_domain>/<source_video_id>.jpg`, `/media/avatars/<source_domain>/<creator_handle>.jpg`
10. **Ingest**: `POST` metadata to the App's `/internal/ingest`. On failure, keep files and retry with backoff.
11. Mark job `done` with the returned `video_id`, or `failed` with the error (stderr tail from yt-dlp kept on the job).

### 11.3 Metadata mapping (yt-dlp -> App)

| App field | yt-dlp field(s), first non-empty |
|---|---|
| source.domain | domain of `webpage_url` (strip `www.`) |
| source.name | `extractor_key` (prettified) |
| source_video_id | `id` |
| source_url | `webpage_url` |
| creator.name | `uploader`, `channel`, `creator`, `uploader_id` |
| creator.handle | `uploader_id`, `channel_id`, `uploader` |
| creator.profile_url | `uploader_url`, `channel_url` |
| title | `title` |
| description | `description` |
| tags | `tags` + `categories` + request `tags`, normalised and deduped |
| upload_date | `upload_date` (YYYYMMDD -> date) or `timestamp` |
| filename | stored filename; keep original `title` + `ext` in a separate field for display |
| technical fields | from ffprobe (not yt-dlp) |

If a video has no tags at all, add a tag named after the source domain so every video has at least one tag.

### 11.4 Operational details

- Worker concurrency configurable (`IMPORT_CONCURRENCY`, default 2).
- Optional `cookies.txt` mount for sites that need a logged-in session (`YTDLP_COOKIES_FILE`).
- yt-dlp breaks when sites change. Allow `YTDLP_AUTO_UPDATE=true` to run `yt-dlp -U` (or `pip install -U yt-dlp`) on container start, and document how to rebuild with the latest version.
- Max file size / max duration limits via env vars; jobs over the limit fail with a clear message.
- Clean up temp files on failure and on startup.
- Structured JSON logs.
- Optional minimal web page at `/` listing recent jobs with a URL box (nice to have, API is the priority).

### 11.5 Ingest payload (Importer -> App)

```json
{
  "source": {"domain": "youtube.com", "name": "YouTube"},
  "source_video_id": "abc123",
  "source_url": "https://www.youtube.com/watch?v=abc123",
  "creator": {
    "name": "Some Creator",
    "handle": "@somecreator",
    "profile_url": "https://www.youtube.com/@somecreator",
    "avatar_path": "avatars/youtube.com/somecreator.jpg"
  },
  "title": "Video title",
  "description": "...",
  "tags": ["cars", "drift"],
  "upload_date": "2026-11-04",
  "file": {
    "path": "videos/youtube.com/abc123.mp4",
    "original_filename": "Video title.mp4",
    "size_bytes": 12345678,
    "width": 1080,
    "height": 1920,
    "duration_s": 42.3,
    "fps": 30,
    "video_codec": "h264",
    "audio_codec": "aac",
    "has_audio": true,
    "container": "mp4"
  },
  "poster_path": "posters/youtube.com/abc123.jpg",
  "info_json_path": "metadata/youtube.com/abc123.json"
}
```

The App upserts source and creator (resolving `creator_aliases`), resolves each tag name through `tag_aliases` before creating new tags, creates the video, and returns `{video_id}`. Ingest is idempotent on (`source.domain`, `source_video_id`).

### 11.6 Metadata sidecar JSON

For every imported video, keep the full yt-dlp metadata in a separate `metadata` folder that mirrors the `videos` folder layout:

```
/media/videos/<source_domain>/<source_video_id>.mp4
/media/metadata/<source_domain>/<source_video_id>.json
```

- Content: yt-dlp's info dict (as from `--write-info-json` / `--dump-single-json`), plus an `_import` block added by the importer: `imported_at`, importer version, yt-dlp version, the request's extra tags, and the ffprobe results.
- Sanitise before writing: remove `http_headers`, cookies and any signed/expiring URLs. Drop the bulky `formats` / `requested_formats` / `thumbnails` arrays except the entries actually used, to keep files small.
- Written atomically (temp file then rename).
- The JSON is the source of truth for imported metadata; the database is the working copy. Admin edits to a video do not rewrite the JSON.
- Uses:
  - Admin "Rebuild from media" (Maintenance): recreates videos, creators, sources and tags from the sidecars if the DB is lost or reset (ratings, collections and categories come from backups, not sidecars).
  - Admin "Re-read metadata" on a video or for all videos: re-applies the mapping in 11.3, so new fields can be added later without re-downloading.
- Deleting a video deletes its sidecar too.

---

### 11.7 Watches (automatic imports of new content)

A watch monitors a creator/channel, a channel tab (e.g. a channel's Shorts page) or a playlist, and once a day imports anything new.

**Importer tables**

**watches**: `id`, `url`, `kind` (`channel` | `playlist` | `page`, detected from yt-dlp), `name` (defaults to the channel/playlist title), `source_domain`, `enabled`, `tags` (extra tags added to every import), `filters` (JSON, below), `initial_mode`, `created_at`, `last_checked_at`, `last_success_at`, `last_new_count`, `last_error`, `consecutive_failures`, `next_run_at`, `creator_source_domain` + `creator_handle` (set for channel-type watches from the yt-dlp listing's channel/uploader ID, so the App can match the watch to a creator; null for playlists that mix creators), `paused_at`

**watch_items**: (`watch_id`, `source_video_id`) primary key, `first_seen_at`, `status` (`baseline` | `queued` | `imported` | `skipped_exists` | `filtered` | `failed` | `unavailable`), `job_id`, `detail`

**watch_runs**: `id`, `watch_id`, `started_at`, `finished_at`, `listed_count`, `new_count`, `queued_count`, `error`

Import jobs created by a watch store `watch_id`, so the job list can show "via watch: BBC Earth Shorts".

**Creating a watch: what happens to existing content**

`initial_mode` decides what happens on the first run:
- `new_only` (**default**): list everything currently on the channel/playlist and record it in `watch_items` as `baseline` without downloading. Only videos that appear after this are imported.
- `backfill_latest`: import the newest `backfill_count` videos (default 20), baseline the rest.
- `backfill_all`: import everything (capped by `WATCH_BACKFILL_MAX`, default 500, queued gradually rather than all at once).

**Daily run**

1. A scheduler inside the Importer runs all enabled watches once a day at `WATCH_RUN_TIME` (default 03:00, in the `TZ` timezone). Watches run one at a time with a random 30 to 120 second gap between them so the sites are not hammered. "Check now" runs a single watch immediately. If the container was down at the scheduled time, overdue watches run shortly after startup.
2. **List cheaply**: `yt-dlp --flat-playlist --dump-single-json` (with `--playlist-end WATCH_SCAN_LIMIT`, default 100) to get video IDs, titles and basic info without downloading anything.
3. **Find what's new**: a video is new if its ID is not in `watch_items` for this watch. For channels (newest first), stop scanning after 20 consecutive already-seen IDs. Playlists are not reliably in date order, so scan up to the limit.
4. **Skip what's already in the library**: check each new ID against the App (`/internal/exists`). Videos already imported some other way are recorded as `skipped_exists`.
5. **Filter**: apply the watch's filters. Anything excluded is recorded as `filtered` so it isn't re-checked every day.
6. **Queue**: create normal import jobs (section 11.2) for the rest, with the watch's tags, up to `WATCH_MAX_PER_RUN` (default 50) per run; any remainder is picked up next run.
7. Update `watch_items` as jobs finish (`imported` / `failed`), and write a `watch_runs` row.

**Filters (optional, per watch)**
- `max_duration_s` / `min_duration_s` (e.g. Shorts only: max 180 seconds)
- `title_include` / `title_exclude` keywords
- `vertical_only` (checked at the probe stage once dimensions are known)
- `uploaded_after` date

**Edge cases**
- Live streams and upcoming premieres are left un-recorded and checked again on the next run, once they become normal videos.
- Private, deleted or region-blocked videos are recorded as `unavailable` and not retried.
- Failed imports are retried on the next two runs, then left as `failed` (can be retried manually).
- Videos removed from a channel or playlist are **not** deleted from the library.
- Deleting a watch keeps everything it imported.
- Pausing a watch keeps its history. When resuming, the user chooses: "Import what was posted while paused" (treated as new, up to the per-run limit) or "Only new from now" (re-baselines without downloading).
- If a watch fails 3 days in a row (e.g. channel renamed, URL broken, yt-dlp out of date), it is flagged as erroring in the App's admin Watches page with the last error message.
- The same channel can't be watched twice (duplicate URLs rejected after normalising).

### 11.8 Watch status on creators (App)

Watches are admin-only. Non-admin users (once sign-up exists) never see watch status or controls.

**Where it shows**
- **Creator feed** (`/feed/creator/:id`): the header pill shows the creator plus, for admins, a watch indicator: an eye icon labelled "Watching" (active), "Paused" (greyed), "Error" (amber, with the last error on tap) or "Not watched".
- **Creator details**: tapping the creator in the feed header (or the creator row in the video details sheet) opens a creator sheet with avatar, name, handle, source, profile link, score, video count, and for admins a **Watch** section.
- **Creators list (Library)**: admins see a small eye icon on watched creators, and a "Watched only" filter.
- **Admin creator page**: same Watch section.

**Watch section (admin)**
- A single toggle: **Watch for new videos**.
  - Off -> On, no existing watch: creates a watch on the creator's profile URL. A small dialog asks for the first-run choice (`new_only` default, or backfill latest N) and offers optional tags and filters, with "Shorts only" as a quick option.
  - On -> Off: **pauses** the watch (does not delete it), so history is kept and nothing is downloaded twice if it is turned back on.
  - Off -> On, paused watch exists: resumes it, asking "Import what was posted while paused" or "Only new from now".
- Below the toggle: last checked, next check, videos imported by this watch, "Check now", and an "Edit" link to the full watch settings. "Delete watch" lives on the full settings page, not on the toggle.
- If a creator has more than one watch (e.g. the channel and its Shorts tab), list each with its own toggle; the indicator shows "Watching" if any are active.
- The toggle reflects the Importer's state: the App reads it via `GET /api/watches?creator_source_domain=&creator_handle=` (cached for 60 seconds) and changes it with the normal watch endpoints. If the Importer is unreachable, the section shows "Importer offline" and the toggle is disabled.

**Matching**: a watch is linked to a creator by (`source_domain`, `handle`), using `creator_aliases` too, so a watch still shows on the right creator after creators are merged. Playlist watches that mix creators are not shown on creator pages, only on the admin Watches page.

---

## 12. PWA requirements

- `manifest.webmanifest`: `name`, `short_name`, `start_url: "/"`, `display: "standalone"`, `background_color` and `theme_color` (dark), icons 192 and 512 plus a maskable icon, `orientation: "portrait"` (allow rotation inside the app if easy).
- Service worker (via `vite-plugin-pwa`): precache the app shell; network-first for API; **do not cache video files** (large, and range requests don't cache well). Posters can use a small runtime cache.
- iOS: `apple-touch-icon`, `apple-mobile-web-app-capable`, status bar style meta tags.
- Must pass Chrome's installability checks (Lighthouse PWA audit).
- Nice to have (later): Web Share Target on Android so a URL shared from another app goes straight to the Importer (admin only).

---

## 13. Docker and configuration

One repo, two services, one compose file.

```
/app            backend + frontend (multi-stage Dockerfile: build frontend, copy into the backend image)
/importer       importer service (Python image with ffmpeg + yt-dlp)
docker-compose.yml
.env.example
```

`docker-compose.yml` (outline):

```yaml
services:
  app:
    build: ./app
    ports: ["8080:8080"]
    env_file: .env
    volumes:
      - ./data/app:/data        # app.db, sessions
      - ./data/media:/media
    depends_on: [importer]
    healthcheck: ...

  importer:
    build: ./importer
    ports: ["8081:8081"]        # optional: only expose if calling the API from outside
    env_file: .env
    volumes:
      - ./data/importer:/data   # jobs.db, temp downloads, cookies.txt
      - ./data/media:/media
    healthcheck: ...
```

Environment variables (put all in `.env.example` with comments):

| Variable | Service | Purpose |
|---|---|---|
| `ADMIN_PASSWORD` | app | Initial admin password (required on first run) |
| `SESSION_SECRET` | app | Cookie signing secret |
| `SESSION_DAYS` | app | Session lifetime, default 30 |
| `INGEST_API_KEY` | both | Importer -> App ingest auth |
| `IMPORTER_URL` | app | e.g. `http://importer:8081` |
| `IMPORTER_API_KEY` | both | Auth for the Importer API |
| `APP_INTERNAL_URL` | importer | e.g. `http://app:8080` |
| `IMPORT_CONCURRENCY` | importer | Default 2 |
| `TZ` | both | Timezone, e.g. `Australia/Sydney` |
| `WATCH_RUN_TIME` | importer | Daily watch time, default `03:00` |
| `WATCH_SCAN_LIMIT` | importer | Max items listed per watch per run, default 100 |
| `WATCH_MAX_PER_RUN` | importer | Max imports queued per watch per run, default 50 |
| `WATCH_BACKFILL_MAX` | importer | Cap for `backfill_all`, default 500 |
| `TRANSCODE_INCOMPATIBLE` | importer | Default true |
| `MAX_FILE_MB`, `MAX_DURATION_MIN` | importer | Import limits |
| `YTDLP_AUTO_UPDATE` | importer | Update yt-dlp on start |
| `YTDLP_COOKIES_FILE` | importer | Optional cookies path |
| `FEED_*` weights | app | Overrides for section 5.5 |

- Both containers run as a non-root user and run DB migrations on start.
- Backups: `./data/app/app.db` + `./data/media` (document in README).

---

## 14. Non-functional requirements

- Mobile first: smooth 60fps swipes on a mid-range phone; first video visible within 2 seconds on a good connection.
- Works in current Chrome (Android/desktop), Safari (iOS/macOS), Edge, Firefox.
- Accessible: buttons for every gesture action, aria labels, reduced-motion support (`prefers-reduced-motion` disables card rotation/fling).
- Security: all admin and internal endpoints authorised; API keys compared in constant time; no directory traversal in media serving; CSRF protection on cookie-authenticated state-changing requests.
- Ratings are saved optimistically and queued for retry if offline.

---

## 15. Build phases and acceptance criteria

**Phase 1: Foundations**
- Repo structure, both Dockerfiles, compose, `.env.example`, README.
- App DB models + migrations, admin seeding, login/logout.
- Ingest endpoint and media serving with Range support.
- Importer: API, job queue, yt-dlp pipeline, ingest call.
- Accept: `curl` a URL to the Importer API -> video file, poster and DB rows exist; `GET /api/videos/:id` returns correct technical metadata.

**Phase 2: Feed**
- Full screen feed, virtualised, autoplay, contain-fit, overlays, details sheet.
- Swipe left/right rating with animations and auto-advance; vertical navigation; keyboard controls.
- Accept: in Playwright mobile emulation (iPhone and Pixel profiles), swiping right records +1 and shows the next video; swiping up skips with no rating; swiping down returns to the previous video showing its rating badge; portrait and landscape videos are never stretched.

**Phase 3: Scores and Library**
- Stats tables, re-rating logic, recompute action.
- Tags / Creators / Sources lists with all sort options and search; filtered feeds.
- Accept: unit tests prove like/dislike/flip/clear produce correct stats, and "recompute" produces identical results to incremental updates.

**Phase 4: Categories and Collections**
- Category CRUD + picker + category feed.
- Collections incl. Favorites and the auto-managed Liked collection, add/remove/reorder, collection feed.
- Accept: tapping the heart adds the video to Favorites without pausing it or scrolling the feed, and a following right swipe likes it and adds it to Liked.
- Accept: liking a video adds it to Liked; flipping to dislike or clearing the rating removes it; recompute leaves Liked unchanged.
- Accept: a category containing one tag and one creator plays the union of their videos with no duplicates.
- Tag and creator merging (9.1) with preview, aliases, undo and duplicate suggestions.
- Global search (6.4).
- Accept: merging `dogs` into `dog` where some videos have both leaves no duplicate tag links, `dog`'s stats equal a full recompute, and a fresh import tagged `dogs` gets `dog` with no `dogs` tag created. Undo restores the original state exactly and also moves the post-merge import back to `dogs`. Chained merge (`dog` into `canine`) leaves no alias pointing at a deleted tag.
- Accept: deleting `app.db` and running "Rebuild from media" restores all videos, creators, sources and tags from the sidecar JSON files.
- Accept: typing `drfit` returns `drift`; typing `car` lists `car` before `cars` before `racecar`; results update on each keystroke.

**Phase 5: Personalisation, PWA, Admin, polish**
- Feed algorithm (5.5), anonymous mode, admin pages, App -> Importer proxy page.
- PWA manifest, icons, service worker; Lighthouse installable.
- Accept: app installs via Chrome "Install" on Android and desktop and via Safari "Add to Home Screen"; liking several videos with a tag visibly raises that tag's videos in the main feed.

**Phase 6: Watches**
- Watch tables, scheduler, listing/diffing, filters, API (11.7) and the App's admin Watches page.
- Accept: creating a `new_only` watch on a playlist imports nothing; adding a video to that playlist and running "Check now" imports only that video; a second "Check now" imports nothing. `backfill_latest` with 3 imports exactly the 3 newest. A video already in the library is recorded as `skipped_exists`, not downloaded again. On a creator page, turning the watch toggle on creates a watch, turning it off pauses it (history kept), and turning it on again resumes the same watch rather than creating a new one; non-admin users see no watch controls. With the clock mocked, the scheduler runs each enabled watch once per day and skips paused ones.

---

## 16. Decisions and assumptions

**Confirmed by Joe:**

| # | Question | Decision |
|---|---|---|
| 1 | Feed ordering | Personalised by tag/creator/source scores with randomness (5.5) |
| 2 | Do rated videos reappear in the main feed? | No. They remain in filtered feeds and can be re-rated by scrolling back. |
| 3 | Does liking auto-add to a collection? | Yes, to a system "Liked" collection (auto-managed). Favorites stays a separate heart button. |
| 4 | Tech stack | Claude Code chooses, within the constraints in section 2 |
| 5 | Importer | Separate Docker app, yt-dlp, URLs submitted via API |
| 6 | Watches | Importer monitors channels/playlists once a day and imports only new content (11.7) |

**Assumptions (build with these, keep them easy to change):**

| # | Question | Assumption for v1 |
|---|---|---|
| 6 | Are scores per user or global? | Per user |
| 7 | Anonymous ratings | Kept in browser memory for the session only |
| 8 | Tag normalisation | Lowercase, trimmed; admin can merge duplicates |
| 9 | Video formats | Prefer H.264/AAC MP4, transcode others by default for iOS compatibility |

---

## 17. Working conventions for Claude Code

- Keep the App and Importer independently buildable and testable.
- Write migrations for every schema change; never edit the DB by hand.
- Include tests for rating math, feed exclusion rules, ingest idempotency and Range requests.

### Source control (required)

- Repository: https://github.com/JoeGibson001/VideoApp.git (branch `main`). Both the App and the Importer live in this one repo, along with `DESIGN.md` and `mockup-player.html`.
- Use the GitHub CLI (`gh`) for GitHub access. Before starting, check `gh auth status`; if not authenticated, stop and ask Joe to run `gh auth login`. If the local folder is not yet a clone, set it up with `gh repo clone JoeGibson001/VideoApp` (or `git init` + `git remote add origin` if the repo is empty), and run `gh auth setup-git` so `git push` uses the `gh` credentials.
- **Every change results in a commit pushed to the repo.** That includes code, tests, Docker files, config, docs and changes to `DESIGN.md` itself. Do not finish a task with uncommitted or unpushed work.
- Commit after each logical unit of work (a feature, a fix, a phase step), not one giant commit at the end. Each commit should build and pass tests.
- Commit messages: short imperative summary line (e.g. "Add tag merge preview endpoint"), blank line, then a few lines on what changed and why. Reference the DESIGN.md section where relevant (e.g. "Implements 9.1").
- Push straight after committing (`git push origin main`). If the push is rejected because the remote has moved on, `git pull --rebase` and retry; never force-push.
- Never commit secrets: `.env`, cookies files, API keys, `data/` (database and media). Add these to `.gitignore` in the first commit; only `.env.example` is committed.
- Tag each completed build phase (`git tag phase-1` etc.) and push tags. When a phase is complete, open a GitHub release for the tag with `gh release create` listing what the phase delivered.
- At the end of each task, report the commit hash(es) and the GitHub URL, then give the `docker compose build` / `docker compose up -d` command to deploy the change.
