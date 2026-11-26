#!/bin/sh
# Importer container entrypoint.
# Started as root: make sure the data/media dirs are writable, then drop to the
# unprivileged "importer" user (uid 1000) and re-run this script as that user.
set -e

DATA_DIR="${DATA_DIR:-/data}"
MEDIA_DIR="${MEDIA_DIR:-/media}"

if [ "$(id -u)" = "0" ]; then
  for d in "$DATA_DIR" "$DATA_DIR/tmp" "$MEDIA_DIR" "$MEDIA_DIR/videos" "$MEDIA_DIR/metadata" "$MEDIA_DIR/posters" "$MEDIA_DIR/avatars"; do
    mkdir -p "$d" 2>/dev/null || true
    chown importer:importer "$d" 2>/dev/null || true
  done
  for f in "$DATA_DIR"/jobs.db "$DATA_DIR"/jobs.db-wal "$DATA_DIR"/jobs.db-shm; do
    [ -e "$f" ] && chown importer:importer "$f" 2>/dev/null || true
  done
  exec setpriv --reuid=importer --regid=importer --init-groups "$0" "$@"
fi

if [ "${YTDLP_AUTO_UPDATE:-false}" = "true" ]; then
  echo '{"level":"INFO","logger":"entrypoint","msg":"updating yt-dlp (YTDLP_AUTO_UPDATE=true)"}'
  if pip install --no-cache-dir --disable-pip-version-check -q -U "yt-dlp[default]"; then
    echo "{\"level\":\"INFO\",\"logger\":\"entrypoint\",\"msg\":\"yt-dlp version $(yt-dlp --version)\"}"
  else
    echo '{"level":"WARNING","logger":"entrypoint","msg":"yt-dlp update failed, using the bundled version"}'
  fi
fi

exec "$@"
