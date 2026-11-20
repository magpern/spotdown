# spotdown

A stand-alone reference backend with the same architecture the **SpotMate** Android app
talks to: an async **"resolve metadata → source audio → transcode → serve file"** pipeline.

> spotdown is also classbeat's music-analysis service. See the frozen
> [integration plan](docs/integration-plan.md) (authorized 2026-11-20 except the owner decisions
> classbeat#1297 to #1299 and spotdown#1 (classbeat#1296 was accepted 2026-11-20); no deploy or real provider call).

It exposes the same API shape the app expects:

| Endpoint | Purpose |
|---|---|
| `GET /api/Gettrack?spotify_url=…` | Resolve a Spotify track/album/playlist to metadata (no audio) |
| `POST /api/v1/convert` | Create a conversion job → returns `task_id` (or `download_url` if cached) |
| `GET /api/v1/tasks/{id}` | Poll job status → `download_url` when done |
| `GET /files/{name}` | Download the finished file |
| `GET /api/v1/analyze/{track_id}` | MIR analysis JSON (tempo, beats, energy, drops, segments) of a converted track |

```
client → FastAPI ──enqueue──> Redis ──> RQ worker
                                          1. Spotify Web API  (metadata only)
                                          2. yt-dlp           (find + download source)   [GATED]
                                          3. ffmpeg           (transcode mp3/m4a)
                                          4. mutagen          (tags + cover art)
                                          5. publish file + cache by track_id
```

## ⚠️ Legal — read this

**The audio never comes from Spotify.** Spotify streams are DRM-protected, so this server
(like SpotMate) uses Spotify **only for metadata** — title, artist, album, duration, ISRC.
To get an actual file, it takes that metadata, **searches YouTube**, and downloads the
matching audio with `yt-dlp`, then transcodes it. So a "Spotify download" is really:
*use Spotify to identify the song → fetch the audio from YouTube.*

**Downloading copyrighted audio you do not have the rights to infringes copyright and
violates YouTube's and Spotify's Terms of Service.** Operating such a service publicly
carries real legal exposure.

Because of that, the **YouTube-sourcing path is OFF by default** (`ALLOW_YTDLP_SOURCING=false`):
with it off, a Spotify link resolves metadata only and the convert step refuses. The pipeline
is identical for lawful inputs, which you drive through the `source_url` field instead — a
direct URL to content you own / Creative-Commons / public-domain / licensed / your own uploads
/ podcasts with permission. `source_url` bypasses Spotify and the YouTube search entirely and
just downloads the URL you give it. You are responsible for how you use this.

## Service mode (v2 API)

`SERVICE_MODE=true` turns spotdown into an internal analysis service ([contract](docs/contract-v2.md)):
only `/v2` and `/healthz` are served. Every `/v2` call needs `Authorization: Bearer <service key>`,
read from the file in `SERVICE_KEY_FILE` (the service refuses to start without it). The UI,
`/files`, `/api/v1`, Spotify URL resolution, CORS and the API docs are off.

- `PUT /v2/uploads?cache_scope=…`: stream mp3, mp4 or wav bytes (`application/octet-stream`, capped
  by `MAX_UPLOAD_BYTES`); kept for one job or `UPLOAD_TTL_S`.
- `POST /v2/analyses` with `upload_id` or `url` (https, hosts in `URL_ALLOWED_HOSTS` only, every
  resolved address public, no redirect to another host, size cap and deadline); `GET /v2/jobs/{id}`.
- The analysis runs in a subprocess with rlimits (`ANALYSIS_MAX_*`); a file over a limit ends as
  `processing_failed` and the worker carries on. Temporary audio is deleted after every job; a job
  whose worker was killed is failed (`timeout`) once past its lease and its audio swept.
- Global rows holding a catalogue-derived reference (a chosen video's match record, a YouTube
  alignment) expire after `GLOBAL_REFERENCE_TTL_S` (default 3 days). Global analyses and their links
  are purged after `GLOBAL_ANALYSIS_TTL_S` (default 90 days) without a read or when the analyzer
  version changes; a takedown by source is `python -m app.service.takedown youtube:<video id>`
  (operator command, counts only; docs/contract-v2.md, "Retention of global analyses").
- **YouTube (off by default).** With `ALLOW_YTDLP_SOURCING=true`, `POST /v2/match` turns
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

## Backend-only (no web UI)

Set `SERVE_UI=false` in `.env` to run API-only — the JSON endpoints, `/files`,
`/healthz` and `/docs` all stay; only the HTML page at `GET /` is withheld
(`GET /` then returns `{"service":"spotdown","ui":"disabled"}`). Useful when
another frontend or service consumes the API.

## Run it

```bash
cp .env.example .env         # fill in SPOTIFY_CLIENT_ID/SECRET for full metadata (optional)
docker compose up --build
```

Or locally without Docker (needs `ffmpeg` on PATH + a running Redis):

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
redis-server &                                   # or: docker run -p6379:6379 redis
uvicorn app.main:app --reload &                  # API on :8000
rq worker -u redis://localhost:6379/0 spotdown   # worker (separate terminal)
```

> **macOS note:** RQ's default worker `fork()`s a work-horse, which macOS aborts
> (SIGABRT/signal 6) when the child touches Objective-C frameworks. Locally, run the
> worker without forking:
> ```bash
> OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES \
>   rq worker --worker-class rq.SimpleWorker -u redis://localhost:6379/0 spotdown
> ```
> The Docker/Linux worker in `docker-compose.yml` is unaffected and uses the default worker.

## Try it (lawful path — a direct source URL)

```bash
# 1) kick off a conversion of content you have the right to download
curl -s -X POST localhost:8000/api/v1/convert \
  -H 'content-type: application/json' \
  -d '{"source_url":"https://example.com/my-own-audio.wav","format":"mp3","bitrate":320}'
# -> {"status":"queued","task_id":"src_...-mp3-320", ...}
# (source_url is an API-only path; the web UI exposes only the Spotify-link flow.)

# 2) poll until done
curl -s localhost:8000/api/v1/tasks/<task_id>
# -> {"status":"done","download_url":"http://localhost:8000/files/src_....mp3"}

# 3) download
curl -O -J localhost:8000/files/<name>
```

Resolving metadata from a Spotify URL always works (needs API creds for album/playlist):

```bash
curl -s 'localhost:8000/api/Gettrack?spotify_url=https://open.spotify.com/track/XXXX'
```

## How it maps to what SpotMate does

- The app POSTs to `/api/v1/convert`; the **server** returns `download_url` **or** a
  `task_id` (the app's string `Convert response missing download_url/task_id`).
- If a `task_id`, the app polls `/api/v1/tasks/{id}` until the server finishes.
- The finished file is fetched over HTTP by the app's `background_downloader` and stored in
  a local SQLite `downloads` table keyed by `track_id` — this server dedupes by `track_id`
  the same way (`idx_downloads_track_id`).

## Music analysis (`GET /api/v1/analyze/{track_id}`)

Runs [librosa](https://librosa.org) MIR on the highest-bitrate converted file for the track
and returns JSON you can build a cycling class (or anything beat-timed) from. Cached per
`analyzer_version`; pass `?refresh=true` to recompute. First call per process is slow
(~10s) while numba JIT-compiles; cached calls are instant.

Fields:
- `bpm` + `bpm_candidates` — tempo, with ½/2× alternates (beat trackers octave-error).
- `beat_grid`, `bars`, `downbeat_phase`, `meter_assumed` — the grid to snap intervals to.
  `meter_assumed` is the estimated meter (`"3/4"` or `"4/4"`; `"4/4"` also when no meter could be
  claimed, then `bars` is the old 4/4 grid on the strongest beat). With a claim, `bars` has one
  entry per bar with a `confidence` (at most 0.4) and the bars restart at the start of the music
  and at each drop, so `downbeat_phase` (the first bar's beat index modulo the meter) no longer
  describes one grid for the whole track; use `bars[].start`.
- `energy_curve` — per-second `full` / `percussive` / `low`-band energy (0–1). Percussive and
  low-band expose kick/bass changes that full-band RMS misses on loud masters.
- `drops` — energy rising-edges (breakdown → full), snapped to the beat; your sprint/accel cues.
- `build_ups` — the climb before each drop; where to ramp effort up.
- `segments` — beat-synchronous sections with `energy_full`, `energy_struct`, a repetition
  `group` (repeats share an id), and a **heuristic** `label` (intro/verse/chorus-drop/
  breakdown-quiet/outro). `label_method: "heuristic"` — reason from the numbers, not the label.

**Timeline caveat (important):** the audio is a YouTube match, so its timeline may not equal
Spotify's. Every timestamp is on the *analyzed file*. The result reports
`analyzed_duration_s`, `spotify_duration_s`, `duration_delta_s`, `leading_silence_s` and
`timeline_matches_spotify` (true when |delta| ≤ 1s). If you drive the class from Spotify
playback and `timeline_matches_spotify` is false, the cues will be offset — prefer a
`- Topic` source (the matcher already favors these; they align to Spotify within ~1s) or
play the downloaded file itself.

## Things to harden before anything real

- **Matching accuracy** (`app/sources.py`, standalone): match on duration (and ideally ISRC), not
  just title+artist, or you get wrong/live/sped-up versions. Service mode has a scored matcher
  (`app/youtube/match_calc.py`) with version-marker, channel, ISRC and duration signals.
- **Caching**: already keyed by `track_id`; convert once, serve many.
- **yt-dlp upkeep** (`app/sources.py`, `app/youtube/ytdlp_client.py`): the real operational
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
- **Abuse/rate limiting**, auth, and **file cleanup/TTL** (expire `data/files`).
- **Storage/CDN**: swap `app/storage.py` for S3 + pre-signed, expiring URLs.

## Layout

```
app/
  main.py        FastAPI routes (Gettrack, convert, tasks, files)
  config.py      env-driven settings
  schemas.py     pydantic models
  spotify.py     Spotify Web API metadata (+ oEmbed fallback)
  sources.py     yt-dlp sourcing (direct URL, or gated Spotify-match search)
  transcode.py   ffmpeg transcode + mutagen tagging
  storage.py     publish file -> public URL
  db.py          SQLite cache (downloads table, track_id)
  queue.py       Redis + RQ
  jobs.py        the worker pipeline
  youtube/       service-mode matcher (pure scoring) and the yt-dlp driver
  service/       service mode (/v2): routes, worker, caches, sandbox
```
