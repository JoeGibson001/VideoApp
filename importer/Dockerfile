# Tastebud Importer: FastAPI + yt-dlp + ffmpeg.
#
#   docker build -t tastebud-importer importer                      # runtime image (default target)
#   docker build --target test -t tastebud-importer-test importer   # test image
#   docker run --rm tastebud-importer-test                          # runs pytest

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    DATA_DIR=/data \
    MEDIA_DIR=/media \
    HOME=/home/importer

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates iptables iproute2 \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid 1000 importer \
 && useradd --uid 1000 --gid importer --create-home --home-dir /home/importer --shell /usr/sbin/nologin importer \
 && python -m venv /opt/venv \
 && mkdir -p /data/tmp /media/videos /media/metadata /media/posters /media/avatars \
 && chown -R importer:importer /opt/venv /data /media

WORKDIR /srv
COPY requirements.txt ./
# yt-dlp lives in a venv owned by the non-root user so YTDLP_AUTO_UPDATE can upgrade it.
# The "deno" package provides the JavaScript runtime yt-dlp needs for YouTube.
RUN pip install -r requirements.txt && chown -R importer:importer /opt/venv

COPY entrypoint.sh /entrypoint.sh
COPY alembic.ini ./
COPY vsimporter ./vsimporter
RUN sed -i 's/\r$//' /entrypoint.sh && chmod 0755 /entrypoint.sh

# ---------------------------------------------------------------- tests
FROM base AS test
COPY requirements-dev.txt pytest.ini ./
RUN pip install -r requirements-dev.txt
COPY tests ./tests
RUN chown -R importer:importer /srv
USER importer
ENTRYPOINT []
CMD ["python", "-m", "pytest"]

# ---------------------------------------------------------------- runtime
FROM base AS runtime
EXPOSE 8081
VOLUME ["/data", "/media"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8081/api/health', timeout=4).status == 200 else 1)"
# The entrypoint starts as root only to fix volume ownership, then drops to uid 1000 (importer).
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "vsimporter"]
