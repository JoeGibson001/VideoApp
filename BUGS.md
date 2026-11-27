# Known bugs

Logged during testing.

## Open

None.

## Fixed

### 1. Import progress bar does not stay in its job row when scrolling

- **Where:** Admin > Import, a running job's progress bar.
- **Logged:** 2026-11-27. **Fixed:** 2026-11-27.
- **Cause:** the import bar and the video feed's progress bar shared the CSS class `.progress`. The
  feed rule (`position: absolute`, pinned just above the nav bar) also applied to the import bar.
- **Fix:** the import bar now has its own class, `.import-progress`, and sits inside its job row.

### 2. Imports fail with "ffmpeg failed (code 187)"

- **Logged and fixed:** 2026-11-27.
- **Cause:** exit code 187 is ffmpeg's "unexpected end of file". The downloaded video was incomplete,
  most likely because the connection (for example through a proxy) was cut before yt-dlp noticed.
- **Fix:**
  - The Importer now downloads the video once more when ffmpeg cannot read it, and only fails if the
    second download is unreadable too.
  - The error now names the problem: "unexpected end of file, the download is probably incomplete".
  - Admin > Import shows ffmpeg's or yt-dlp's last output lines under a failed job ("Tool output").
- **If it keeps happening behind Privoxy:** raise Privoxy's `socket-timeout` and check its logs for
  dropped connections.
