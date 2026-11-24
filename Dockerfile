# The spotdown image (docs/runbooks/docker-image.md). Released as ghcr.io/magpern/spotdown by
# .github/workflows/release.yml for linux/amd64 and linux/arm64. It holds the code and its
# dependencies only: no .env, no audio, no data and no secret (.dockerignore; the service key is
# a Docker secret file mounted at run time).
#
# The base is pinned by version and by the digest of its multi-platform index. To upgrade: pick
# the newest python:3.11.X-slim-trixie, read its index digest with
# `docker buildx imagetools inspect python:<tag>` and replace both here in one PR.
FROM python:3.11.18-slim-trixie@sha256:07d4824036af339fa1b6ef769cc99d9c10a141769538b52b3b597c84f7a7b535

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ffmpeg decodes and transcodes; libsndfile1 is librosa's soundfile backend.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# A fixed, unprivileged uid and gid (10001) so a host can give the service key file and the data
# volume to exactly this user. /data is the one writable place the service needs.
RUN groupadd --system --gid 10001 spotdown \
    && useradd --system --uid 10001 --gid 10001 --home-dir /home/spotdown --create-home \
       --shell /usr/sbin/nologin spotdown \
    && install -d -o 10001 -g 10001 -m 0750 /data

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Root-owned and read-only for the service user; compiled now so nothing is written at run time.
COPY app ./app
RUN python -m compileall -q app

# Service mode by default (docs/contract-v2.md): only /v2 behind the service key, and /healthz.
# Without a readable key file at SERVICE_KEY_FILE the server refuses to start. The standalone web
# UI is the development compose file's job (docker-compose.yml sets SERVICE_MODE=false).
# Caches go to /tmp (a tmpfs in the deploy compose file), never into the read-only image.
ENV SERVICE_MODE=true \
    SERVE_UI=false \
    SERVICE_KEY_FILE=/run/secrets/spotdown_service_key \
    DATA_DIR=/data \
    HOME=/home/spotdown \
    XDG_CACHE_HOME=/tmp/cache \
    NUMBA_CACHE_DIR=/tmp/numba-cache

USER 10001:10001

EXPOSE 8000

# The api's own /healthz on loopback (app/healthcheck.py); the deploy compose file gives the
# worker its Redis check instead.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-m", "app.healthcheck", "api"]

# Overridden by docker-compose for the worker service.
# 0.0.0.0 inside the container is needed for publishing; compose publishes on 127.0.0.1. Forwarded
# headers are ignored (ADR 0003).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
