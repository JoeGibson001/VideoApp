#!/bin/sh
# Importer container entrypoint.
# Started as root: make sure the data/media dirs are writable, set the proxy firewall if a proxy is
# configured, then drop to the unprivileged "importer" user (uid 1000) and re-run this script as that user.
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

  # Validate PROXY_URL first: a bad value stops the container instead of silently going direct.
  PROXY_URL="$(python -m vsimporter.proxy normalize)"
  export PROXY_URL
  unset TASTEBUD_FIREWALL
  case "$(echo "${PROXY_KILL_SWITCH:-true}" | tr A-Z a-z)" in false|0|no|off) KILL_SWITCH=off ;; *) KILL_SWITCH=on ;; esac
  if [ -n "$PROXY_URL" ] && [ "$KILL_SWITCH" = "on" ]; then
    # Exits non-zero (and the container stops) if the rules cannot be applied.
    python -m vsimporter.proxy firewall
    export TASTEBUD_FIREWALL=active
  fi
  # Dropping to uid 1000 also drops NET_ADMIN, so nothing in the importer can change the rules.
  exec setpriv --reuid=importer --regid=importer --init-groups --bounding-set=-all "$0" "$@"
fi

if [ "${YTDLP_AUTO_UPDATE:-false}" = "true" ]; then
  echo '{"level":"INFO","logger":"entrypoint","msg":"updating yt-dlp (YTDLP_AUTO_UPDATE=true)"}'
  if [ -n "${PROXY_URL:-}" ]; then
    pip_ok() { pip install --no-cache-dir --disable-pip-version-check -q -U --proxy "$PROXY_URL" "yt-dlp[default]"; }
  else
    pip_ok() { pip install --no-cache-dir --disable-pip-version-check -q -U "yt-dlp[default]"; }
  fi
  if pip_ok; then
    echo "{\"level\":\"INFO\",\"logger\":\"entrypoint\",\"msg\":\"yt-dlp version $(yt-dlp --version)\"}"
  else
    echo '{"level":"WARNING","logger":"entrypoint","msg":"yt-dlp update failed, using the bundled version"}'
  fi
fi

exec "$@"
