# spotdown-ui

The UI image of spotdown ([ADR 0005](../docs/adr/0005-two-images-backend-api-and-ui.md)): a small
web service, `ghcr.io/magpern/spotdown-ui`, built from [`Dockerfile.ui`](../Dockerfile.ui). It
imports nothing from `app/` and holds no yt-dlp, librosa or ffmpeg. It analyses only through the
backend.

The page lets you:

- paste a Spotify track, album or playlist link, see the tracks and pick one;
- see its YouTube match, analyse it, and make and download the mp3;
- pick a local audio file (mp3, m4a or wav) and analyse it.

The analysis view shows the explicit tag, the timeline and the waveform.

## How it calls the backend

Only server-side, and only at `SPOTDOWN_BACKEND_URL`. The browser never sees the backend, its
address or its key. Each browser route maps to one fixed backend route:

| Browser | Backend |
|---|---|
| `POST /api/spotify/resolve` | `POST /v2/spotify/resolve` |
| `POST /api/match` | `POST /v2/match` |
| `PUT /api/uploads` | `PUT /v2/uploads`, streamed |
| `POST /api/analyses` | `POST /v2/analyses`, with a fresh `Idempotency-Key` |
| `GET /api/jobs/{id}` | `GET /v2/jobs/{id}` in the scope `spotdown_ui` |
| `GET /api/audio-jobs/{id}` | `GET /v2/jobs/{id}` without a scope (an mp3 job has none) |
| `GET /api/analyses/{id}` | `GET /v2/analyses/{id}` |
| `POST /api/audio` | `POST /v2/audio`, with a fresh `Idempotency-Key` |
| `GET /api/audio/{id}` | `GET /v2/audio/{id}`, streamed as `audio/mpeg` |

- Every analysis is in the fixed `cache_scope` `spotdown_ui`; `DELETE /v2/scopes/spotdown_ui`
  removes them.
- Ids are checked by pattern (`[A-Za-z0-9_-]{1,64}`) before they go into a backend path.
- Bodies are rebuilt from their known fields only.
- Only a status and a JSON body come back, never the backend's headers. A backend error is shown
  as its `code`.
- The client ignores proxy settings from the environment and never follows a redirect.
- The UI keeps no audio and no analysis of its own.

## Browser-facing protection

[ADR 0003](../docs/adr/0003-standalone-exposure.md), carried over unchanged:

- a token session (sign in at `/login`);
- the `Host` allowlist and the expected origin;
- same-origin checks and one exact content type on every unsafe request;
- `X-Spotdown-UI: 1` on `/api/`;
- no CORS and no API docs.

Every response has the headers of ADR 0004 Decision 6 (a strict CSP, no framing, no referrer, no
caching). `/healthz` is open and never calls the backend.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `SPOTDOWN_BACKEND_URL` | (required) | `http://` or `https://` plus a host and an optional port, nothing else. The only host the UI ever calls. |
| `SPOTDOWN_BACKEND_KEY_FILE` | `/run/secrets/spotdown_ui_backend_key` in the image | A secret file with the UI's caller key: the backend's `UI_SERVICE_KEY_FILE`. At least 32 characters of printable ASCII without spaces. |
| `UI_ACCESS_TOKEN_FILE` | unset | An owner-supplied access token (32 to 512 characters). Unset: a token is generated once in `UI_DATA_DIR/ui_token`. |
| `UI_DATA_DIR` | `/data` | Where the generated token lives. |
| `UI_ALLOWED_HOSTS` | `localhost,127.0.0.1,[::1]` | The `Host` allowlist (names or IP literals, no port). |
| `UI_PUBLIC_ORIGIN` | unset | The exact `http(s)://host[:port]` the browser uses, behind a reverse proxy. |
| `MAX_UPLOAD_BYTES` | 62914560 (60 MiB) | The largest local file. A larger one stops at the cap with `413`. |
| `UI_UPLOAD_READ_S` | 150 | The read deadline for a whole upload (`408` past it). |
| `UI_MAX_AUDIO_BYTES` | 209715200 (200 MiB) | The largest mp3 relayed. |
| `UI_BACKEND_TIMEOUT_S` | 30 | The timeout of one backend request. |

The app fails to start if any of these is wrong:

- the key file is missing, unreadable or too short;
- `SPOTDOWN_BACKEND_URL` is missing or not exact;
- a host entry or the public origin is malformed.

An error names the setting, never the key.

Run locally: `uvicorn --factory spotdown_ui.app:create_app` (needs `requirements-ui.txt`). The
image runs one process, because the one upload slot is held in memory.
