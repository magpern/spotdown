# Runbook: install and upgrade spotdown from its Docker image

spotdown ships as a Docker image (owner, 2026-11-24): `ghcr.io/magpern/spotdown`, built for
`linux/amd64` and `linux/arm64` by `.github/workflows/release.yml` from a version tag on `main`.
A server needs the image, the release's `compose.yaml`, its own service key file and, optionally,
a `spotdown.env`. It never needs a clone. Deploying stays the owner's decision (AGENTS.md, safety
envelope).

## What a release publishes

| Item | Value |
|---|---|
| Image | `ghcr.io/magpern/spotdown`, **private** package. Never change its visibility. |
| Tags | `X.Y.Z`, `X.Y` (stable releases only, moves to the newest patch) and `sha-<short commit>`. There is **no `latest`**. |
| Attestations | SLSA provenance (`mode=max`) and an SBOM in the registry; a GitHub artifact attestation of the image digest and of every release asset. |
| Release assets | `compose.yaml` (from `deploy/compose.yaml`), `spotdown.env.example` (from `deploy/spotdown.env.example`) and `SHA256SUMS`. |
| Release notes | Generated from the merged PRs, plus the attested image digest. A tag with a hyphen (`v1.2.3-rc.1`) is a pre-release. |

A tag that is not `vMAJOR.MINOR.PATCH[-prerelease]`, or whose commit is not on `main`, publishes
nothing. Agents never push a tag on their own; the Architect cuts it after a merge
(`git tag -a vX.Y.Z -m vX.Y.Z origin/main && git push origin vX.Y.Z`), and a pushed tag is never
moved or deleted: fix forward with a new version.

## What the image is

- Python 3.11 on Debian slim, base pinned by version and digest; ffmpeg and libsndfile from Debian.
- Runs as the unprivileged user **uid 10001, gid 10001**. The code is root-owned and read-only.
- **Service mode by default** (`SERVICE_MODE=true`, `SERVE_UI=false`): only `/v2` behind the service
  key, and `/healthz` ([contract](../contract-v2.md)). Without a readable key file the server does
  not start.
- Holds no `.env`, no audio, no data and no secret: `.dockerignore` lets only `requirements.txt` and
  `app/` into the build, and the build takes no build arguments or secrets.
- Writes only to `/data` (the database `service.db`, uploads and work files) and `/tmp` (numba and
  yt-dlp caches).
- Healthcheck: `python -m app.healthcheck api` asks `/healthz` on `127.0.0.1:8000`; the deploy file
  gives the worker `python -m app.healthcheck worker` (a Redis ping).

## Install

On the server, as the deploying user:

```bash
mkdir -p /opt/spotdown && cd /opt/spotdown
V=1.2.3                                   # the release to install
gh release download "v$V" -R magpern/spotdown -p compose.yaml -p spotdown.env.example -p SHA256SUMS
sha256sum -c SHA256SUMS
gh attestation verify compose.yaml -R magpern/spotdown

# The private image: log in once with a token that has read:packages only.
docker login ghcr.io -u <github user>      # paste the token at the prompt, never on the command line

# The callers' network. classbeat joins it and calls http://spotdown-api:8000.
docker network create --internal spotdown

# The service key: at least 32 characters, one line, readable only by the image's user.
mkdir -p secrets
umask 077
openssl rand -hex 32 > secrets/spotdown_service_key.new
sudo install -m 0400 -o 10001 -g 10001 secrets/spotdown_service_key.new secrets/spotdown_service_key
rm secrets/spotdown_service_key.new
# Give the same key to the caller (classbeat) through its own secret file; never paste it into chat,
# a URL, an environment variable or a log.

cp spotdown.env.example spotdown.env      # optional: uncomment only what you change
SPOTDOWN_VERSION=$V docker compose up -d --wait
```

Check the pulled image is the attested one:

```bash
docker image inspect --format '{{index .RepoDigests 0}}' ghcr.io/magpern/spotdown:$V
gh attestation verify oci://ghcr.io/magpern/spotdown:$V -R magpern/spotdown
```

The first digest must be the one in the release notes.

## Settings

The deploy file fixes `SERVICE_MODE=true`, `SERVE_UI=false`,
`SERVICE_KEY_FILE=/run/secrets/spotdown_service_key`, `DATA_DIR=/data` and
`REDIS_URL=redis://redis:6379/0`. Everything else is optional in `spotdown.env`, one `NAME=value`
per line. `spotdown.env.example` lists each setting with its default:

- `URL_ALLOWED_HOSTS`: the hosts the `url` source may fetch from. Empty turns that source off.
- `ALLOW_YTDLP_SOURCING`: YouTube. Off by default. Turning it on accepts a terms-of-service risk and
  is the operator's decision (README, "Service mode").
- `MAX_UPLOAD_BYTES`, `UPLOAD_TTL_S`, `URL_FETCH_TIMEOUT_S`, `ANALYSIS_MAX_*`, `GLOBAL_*_TTL_S`:
  limits and retention.

Compose variables, read from the shell or from a `.env` file next to `compose.yaml`:

| Variable | Meaning |
|---|---|
| `SPOTDOWN_VERSION` | **Required.** `X.Y.Z` (pinned, recommended) or `X.Y` (follows patch releases on each pull). |
| `SPOTDOWN_SERVICE_KEY_FILE` | The key file on the host. Default `./secrets/spotdown_service_key`. |
| `SPOTDOWN_SERVICE_NETWORK` | The callers' external network. Default `spotdown`. |

Never put the service key in `spotdown.env` or in any environment variable. It is a Docker secret
file mounted into the api only; the worker and Redis never see it.

## Networks, ports and volumes

- **No port is published.** The api listens on port 8000 inside its networks only. A caller
  reaches it as `http://spotdown-api:8000` on the `spotdown` network. Redis is on the `backend`
  network (internal), which only the api and the worker share.
- `egress` gives the api and the worker a way out, for the `url` source and, if enabled, YouTube.
- `spotdown-data` (mounted at `/data`) holds the service database and the temporary audio of
  running jobs. Back it up while the stack is stopped. A takedown does not reach an earlier backup
  (contract, "Retention of global analyses").
- `redis-data` holds the job queue. Losing it loses the jobs still waiting in the queue.

Operator commands run in the api container, for example a takedown:

```bash
SPOTDOWN_VERSION=$V docker compose exec api python -m app.service.takedown youtube:<video id>
```

## Upgrade, pin and roll back

1. Read the release notes of every version between the running one and the new one, and back up
   `spotdown-data` (stack stopped).
2. Download that release's `compose.yaml` and `SHA256SUMS`, check them as above, and compare
   the new `compose.yaml` with the running one (`diff`).
3. `SPOTDOWN_VERSION=<new> docker compose pull && SPOTDOWN_VERSION=<new> docker compose up -d --wait`.
4. Check `docker compose ps` shows every service `healthy`.

To roll back, run step 3 with the previous version and its `compose.yaml`. A newer version may
have migrated the service database on start, so roll back only when the newer release notes say an
older version can read it; otherwise restore the backup of `spotdown-data` taken before the upgrade.

Pin `X.Y.Z` in production; `X.Y` moves on every patch release and changes what a plain
`docker compose pull` installs.

## Rotating the service key

Write a new key file as in "Install", update the caller's copy, then
`SPOTDOWN_VERSION=$V docker compose up -d --force-recreate api`. The old key stops working when the
api restarts.

## Local development

`docker-compose.yml` in the repository builds the same Dockerfile for development. It runs the
standalone web UI (`SERVICE_MODE=false` unless set) on the host's `./data` bind mount, as root
(ADR 0003). It is never a production install.
