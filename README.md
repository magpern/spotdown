# spotdown

spotdown is a music-analysis service: **audio in, structured analysis out** (tempo and beats,
energy, drops and build-ups, sections, phrases, meter, an explicit tag). It is also classbeat's
analysis service; see the frozen [integration plan](docs/integration-plan.md) (authorized
2026-11-20 except the owner decisions it lists; no deploy and no real provider call by agents).

It ships as two images from one repository and one tag
([ADR 0005](docs/adr/0005-two-images-backend-api-and-ui.md)):

| Image | What it is |
|---|---|
| `ghcr.io/magpern/spotdown` (`Dockerfile`, `app/`) | **The backend**, API only: `/v2/...` behind a caller key, and `/healthz`. An api process and an RQ worker. |
| `ghcr.io/magpern/spotdown-ui` (`Dockerfile.ui`, `spotdown_ui/`) | **The UI**, optional: paste a Spotify link or pick a local file, see the YouTube match, the analysis and the mp3. It calls the backend server-side with its own key ([spotdown_ui/README.md](spotdown_ui/README.md)). |

```
caller (classbeat) ─┐                          ┌─> Redis ──> RQ worker
                    ├─ /v2 + key ─> backend api ┤               1. source: upload | url | youtube
browser ─> UI ──────┘   (server-side)           │               2. sandboxed checks + analysis
                                                └─ Spotify link resolution, YouTube match, mp3
```

## ⚠️ Legal — read this

**The audio never comes from Spotify.** Spotify is used only for metadata (title, artists,
duration, ISRC), from its public pages or, with credentials, its Web API. To get audio, the backend
matches that metadata on YouTube and downloads the matching audio with `yt-dlp`.

**Downloading copyrighted audio you do not have the rights to infringes copyright and violates
YouTube's and Spotify's Terms of Service.** Operating such a service publicly carries real legal
exposure.

The **YouTube path is ON by default** (`ALLOW_YTDLP_SOURCING=true`, owner decision, 2026-11-24).
Set `ALLOW_YTDLP_SOURCING=false` to turn every YouTube path off (`422 source_disabled`). Turning it
on is the operator's terms-of-service decision. **Upgrading:** an unset flag used to mean off and
now means on, so set it explicitly (`true` or `false`) before you upgrade; `false` keeps the old
behaviour. Uploads and the `url` source (content you own, Creative Commons, public domain or
licensed) never touch YouTube. You are responsible for how you use this.

## The backend

The backend has no mode ([contract](docs/contract-v2.md)): it serves only `/v2/...` and
`/healthz`; every other path is 404, with no CORS, no API docs and no static files. Every `/v2`
call needs `Authorization: Bearer <key>`: classbeat's key from the file in `SERVICE_KEY_FILE`
(required: the api refuses to start without it), or the UI image's own key from the optional
`UI_SERVICE_KEY_FILE` (at least 32 characters, different from the first, the same rights). The
worker needs no key.

`SERVICE_MODE`, `SERVE_UI` and `SPOTDOWN_UI` are **deprecated and ignored** for one release: an
old compose or `.env` file still starts, nothing turns on, and the api logs one warning naming
them (never a value). Remove them; the next release drops them.

- `POST /v2/spotify/resolve`: a Spotify track, album or playlist link to its tracks. It needs no
  Spotify credentials and then reads Spotify's public pages on `open.spotify.com`. With
  `SPOTIFY_CLIENT_ID` and the secret in the file `SPOTIFY_CLIENT_SECRET_FILE` it uses the official
  Web API instead (full playlists, ISRC). One of the two alone stops the start-up.
- `POST /v2/audio` and `GET /v2/audio/{id}`: the mp3 of a matched YouTube video, by the owner's
  direction (ADR 0005); kept `AUDIO_TTL_S`. Every other route never returns audio.
- `PUT /v2/uploads?cache_scope=…`: stream mp3, mp4 or wav bytes (`application/octet-stream`, capped
  by `MAX_UPLOAD_BYTES`, 60 MiB by default); the whole structure is checked and a malformed,
  truncated or mixed file is refused (`415`) with nothing kept; kept for one job or `UPLOAD_TTL_S`.
- `POST /v2/analyses` with `upload_id` or `url` (https, hosts in `URL_ALLOWED_HOSTS` only, every
  resolved address public, no redirect to another host, size cap and deadline); `GET /v2/jobs/{id}`.
- The analysis runs in a subprocess with rlimits (`ANALYSIS_MAX_*`, the decode itself within
  `ANALYSIS_MAX_DECODE_S`); the file is checked again and decoded by a decoder pinned to its
  container; a file over a limit ends as
  `processing_failed` and the worker carries on. Temporary audio is deleted after every job; a job
  whose worker was killed is failed (`timeout`) once past its lease and its audio swept.
- Global rows holding a catalogue-derived reference (a chosen video's match record, a YouTube
  alignment) expire after `GLOBAL_REFERENCE_TTL_S` (default 3 days). Global analyses and their links
  are purged after `GLOBAL_ANALYSIS_TTL_S` (default 90 days) without a read or when the analyzer
  version changes; a takedown by source is `python -m app.service.takedown youtube:<video id>`
  (operator command, counts only; docs/contract-v2.md, "Retention of global analyses"). Scoped
  analyses (with their links and alignments) are purged by the same rules after
  `SCOPED_RETENTION_TTL_S` (default 90 days), with scoped job records unchanged that long, so a
  scope that is never deleted keeps nothing for ever (docs/contract-v2.md, "Retention of scoped
  entries").
- **YouTube (on by default).** With `ALLOW_YTDLP_SOURCING=true` (the default), `POST /v2/match` turns
  `{title, artists, duration_s, isrc?}` into ranked YouTube candidates (recording-match confidence,
  band, duration check, up to 2 alternates; no download), and `POST /v2/analyses` accepts
  `source_ref: "youtube:<11-character video id>"`. With the setting off both are
  `422 source_disabled`. A YouTube analysis is global: one per video, shared by every scope, and not
  removed by a scope deletion. Every YouTube match is **timing-approximate** for the caller;
  spotdown reports the evidence only and never claims identical audio.

  **Terms-of-service risk, the operator's to accept:** downloading from YouTube breaks YouTube's
  terms, and using it for Spotify-catalogue tracks touches Spotify's Developer Policy. Returning
  only metadata, or running noncommercially, does **not** remove that risk. Enabling it is a
  deliberate per-deployment decision (for classbeat: the owner's, classbeat#1296). Tests and CI never
  call YouTube: yt-dlp is faked in every test.

## The UI

The UI image is a small web service of its own: the page, a token session (ADR 0003), and the
`Host`, origin and content-type checks; it holds no `app/`, yt-dlp, librosa or ffmpeg, keeps no
audio or analysis, and its key never reaches the browser. Analyses go to the fixed scope
`spotdown_ui`. Its settings and routes are in [spotdown_ui/README.md](spotdown_ui/README.md).

## Install from the Docker images (production)

Both images are private on GHCR (`linux/amd64`, `linux/arm64`; tags `X.Y.Z`, `X.Y` and
`sha-<short>`, never `latest`). Each GitHub Release carries `compose.yaml` (both images, pinned
through `SPOTDOWN_VERSION`), the overlays `compose.no-ui.yaml` (a deploy without the UI, which
needs only classbeat's key) and `compose.spotify.yaml` (optional Spotify credentials), the two env
examples and `SHA256SUMS`. The keys are Docker secret files; the backend publishes no port, and the
UI publishes 8000 on the loopback by default.
[docs/runbooks/docker-image.md](docs/runbooks/docker-image.md) covers install, settings, the UI's
bind address, upgrading (also from a `SERVICE_MODE` compose file) and pinning.

## Local development

With Docker, both images from the checkout (the UI on `127.0.0.1:8000`):

```bash
mkdir -p secrets && umask 077
# Two different keys, owned by the images' user (uid 10001): the ui runs as that user and reads
# its key from the bind-mounted file, which keeps the host's owner and mode.
for name in spotdown_service_key spotdown_ui_backend_key; do   # classbeat's key, the UI's key
  openssl rand -hex 32 > "secrets/$name.new"
  sudo install -m 0400 -o 10001 -g 10001 "secrets/$name.new" "secrets/$name"
  rm "secrets/$name.new"
done
cp .env.example .env                                      # optional
docker compose up --build
docker compose exec ui cat /data/ui_token                 # sign in at http://127.0.0.1:8000/login
```

Without Docker (Python 3.11, `ffmpeg` on PATH and a running Redis), the processes run as you, so
create the key files as yourself (`openssl rand -hex 32 > secrets/spotdown_service_key`) instead
of with `install -o 10001`:

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
redis-server &                                   # or: docker run -p127.0.0.1:6379:6379 redis
export SERVICE_KEY_FILE=secrets/spotdown_service_key
uvicorn app.main:app --host 127.0.0.1 --port 8001 --no-proxy-headers &   # the backend
rq worker -u redis://localhost:6379/0 spotdown   # the worker (separate terminal)
```

Then call it, for example `curl -H "Authorization: Bearer $(cat secrets/spotdown_service_key)"
http://127.0.0.1:8001/v2/jobs/job_x` (404 `not_found`). The UI runs with
`SPOTDOWN_BACKEND_URL=http://127.0.0.1:8001 SPOTDOWN_BACKEND_KEY_FILE=secrets/spotdown_ui_backend_key
UI_DATA_DIR=./data-ui uvicorn --factory spotdown_ui.app:create_app --host 127.0.0.1` with
`UI_SERVICE_KEY_FILE=secrets/spotdown_ui_backend_key` set for the backend.

> **macOS note:** RQ's default worker `fork()`s a work-horse, which macOS aborts
> (SIGABRT/signal 6) when the child touches Objective-C frameworks. Locally, run the
> worker without forking:
> ```bash
> OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES \
>   rq worker --worker-class rq.SimpleWorker -u redis://localhost:6379/0 spotdown
> ```
> The Docker/Linux worker in `docker-compose.yml` is unaffected and uses the default worker.

Checks: `ruff check . && ruff format --check . && pyright`, and `pytest` on the files you changed
(AGENTS.md, "Developer commands"). Tests never touch the network.

## Things to harden before anything real

- **Matching accuracy**: the scored matcher (`app/youtube/match_calc.py`) uses version-marker,
  channel, ISRC and duration signals; a wrong match still gives the wrong audio.
- **yt-dlp upkeep** (`app/youtube/ytdlp_client.py`): the real operational
  burden. YouTube actively fights downloaders, so expect to update `yt-dlp` constantly. Two
  failures you *will* hit:
  - **"Requested format is not available"** → yt-dlp too old to decode YouTube's current
    signatures. Fix: `pip install -U yt-dlp` (and bump the pin in `requirements.txt`).
  - **"The page needs to be reloaded" / "Sign in to confirm you're not a bot"** → YouTube's
    bot challenge on the default `web` client. We pass
    `extractor_args={"youtube": {"player_client": ["android", "tv", "web"]}}` to skip it.
    From datacenter IPs you may *still* be blocked and need cookies (`cookiefile`) or a
    PO token. This is inherent to sourcing from YouTube, not a one-time fix.
- **Python**: yt-dlp has deprecated Python 3.9. The Docker image uses 3.11; if you run the
  local venv on 3.9 it still works for now but will stop — use 3.11+.
- **Abuse and rate limiting** of the UI beyond its login limit.

## Layout

```
app/                the backend image
  main.py           create_app: /v2 behind the key, /healthz; nothing else
  config.py         env-driven settings (the deprecated mode settings are ignored)
  queue.py          Redis + RQ (queue "spotdown")
  healthcheck.py    the container healthcheck (api /healthz, worker Redis ping)
  core/             the core analyser (pure, on the file's own clock)
  alignment_calc.py the alignment layer (pure)
  youtube/          the matcher (pure scoring) and the yt-dlp driver
  service/          /v2: routes, worker, caches, sandbox, sources, Spotify, mp3
  quality/          the quality harness
spotdown_ui/        the UI image
deploy/             the production compose file, its overlays and the env examples (release assets)
scripts/release-assets.sh  the release's asset list and SHA256SUMS
```
