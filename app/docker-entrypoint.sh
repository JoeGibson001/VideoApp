#!/bin/sh
# Make sure the bind-mounted volumes are writable by the app user, then drop root.
# Migrations run inside the app on startup (vidswipe.main.startup).
set -e
if [ "$(id -u)" = "0" ]; then
  mkdir -p "$DATA_DIR" "$MEDIA_DIR"
  chown app:app "$DATA_DIR" "$MEDIA_DIR" 2>/dev/null || true
  for d in videos posters avatars metadata icons; do
    mkdir -p "$MEDIA_DIR/$d" && chown app:app "$MEDIA_DIR/$d" 2>/dev/null || true
  done
  exec setpriv --reuid=app --regid=app --init-groups "$@"
fi
exec "$@"
