# ADR 0005: two images, a backend that is API only and a separate UI

**Status:** Proposed (Architect, 2026-11-24). It becomes Accepted when the Opus review of its PR
passes (spotdown#75).

**Owner direction (2026-11-24):** "Spotdown shall have the option to paste in a Spotify link or
playlist, just like before, then it downloads the mp3 through matching with YouTube. Then it does its
magic with analysing. When classbeat calls it does something similar, but without the UI. Don't
complicate things with a lot of legal stuff. Plain and simple." And: "We provide a service and it
can have a UI or not. I want the UI option even if it is in service_mode. Why do we even have
something called service_mode." Asked to choose between one app with a UI switch and two images,
the owner chose: "yes, go with two images."

**Supersedes:**
- [ADR 0004](0004-ui-switch-independent-of-service-mode.md) as a whole: there is no service mode,
  no `SPOTDOWN_UI` switch and no static service page. The browser never holds the service key.
- [ADR 0002](0002-local-file-input-in-standalone-ui.md) Decision 1, which put the route in the
  standalone app. The local file now enters through the UI image, which streams it to
  `PUT /v2/uploads`. Its other decisions still apply, in the UI image:
  - the browser cannot reach the route from another site (Decision 2);
  - one slot and a read deadline (Decision 3);
  - nothing is kept (Decision 5);
  - the waveform is drawn in the browser (Decision 6).

  Decision 4's one hostile-input module is now the backend's `/v2` upload path, which already runs it.
- [ADR 0003](0003-standalone-exposure.md) in where it applies. Its token, `Host` allowlist,
  same-origin checks, login and logout, no CORS and loopback publishing protect the **UI image**,
  unchanged in substance. The backend has no browser surface. Decision 7 ("service mode is out of
  scope") is replaced by Decision 2 below.
- The layer table of [`docs/integration-plan.md`](../integration-plan.md).

**Issues, in build order:** #75 (this ADR), #76 and #77 (backend, in parallel), #78 (UI image),
#79 (retire the standalone app and the mode; deploy both images). Each is security-critical and has
an Opus Developer.

## Context

Today one image runs as either of two apps, chosen by `SERVICE_MODE`.
- **Standalone mode** has the full UI: paste a Spotify link, match on YouTube, download the mp3,
  analyse, and upload a local file. It uses its own v1 routes and token session.
- **Service mode** has `/v2` behind the service key for classbeat, and, since ADR 0004, a static
  page that asks the user to type in the service key.

The owner rejects the mode and the reduced page, and wants the UI as before, as an option next to
the service.

## Decision

1. **Two images, one repository, one version.**
   - `ghcr.io/magpern/spotdown` is the backend (api and worker), built from `Dockerfile`, as now.
   - `ghcr.io/magpern/spotdown-ui` is the UI, built from `Dockerfile.ui` out of the package
     `spotdown_ui/` at the repository root.

   The release workflow builds both from the same `vX.Y.Z` tag, with the same pinning, attestation
   and digest note. The UI package imports nothing from `app/`. The UI image holds no `app/`,
   yt-dlp, librosa or ffmpeg. Same repository: one review gate, one CI, one tag. A split into two
   repositories later is a move of one directory.

2. **The backend is API only, with no mode.**
   - It serves `/v2/...` behind a caller key, plus `/healthz`; every other path is 404. No CORS, no
     API docs, no static files, no token session.
   - A service key is always required (fails closed, as service mode is today).
   - The standalone routes, the v1 cache and `/files` leave the backend (#79).
   - `SERVICE_MODE`, `SERVE_UI` and `SPOTDOWN_UI` stay accepted for one release as **ignored,
     deprecated** settings, with one start-up warning that names them and never a value. Existing
     compose files and `.env` files still start, and nothing turns on. The release after removes them.

3. **Two caller keys, from secret files.**
   - `SERVICE_KEY_FILE` is classbeat's and is required. `UI_SERVICE_KEY_FILE` is the UI image's and
     is optional.
   - Same rules for both: at least 32 characters, only the SHA-256 held, never logged.
   - A request is checked against every configured key in constant time. Two equal keys stop
     start-up.
   - Both keys have the same rights on `/v2`; no per-key scopes (plain and simple). The UI's keys are
     separate so that the owner can replace one without touching classbeat's. Replacing a key is:
     write the file, restart.

4. **`/v2` gains the Spotify link step (#76).**
   - `POST /v2/spotify/resolve` takes `{"url"}` (a track, album or playlist link, or a `spotify:`
     URI). It answers the tracks: `spotify_id`, `title`, `artists`, `album`, `duration_ms` and
     `isrc`, at most 500, with `truncated`.
   - The input is parsed strictly to (kind, id). The backend builds the Spotify Web API URLs itself,
     on `accounts.spotify.com` and `api.spotify.com` only; **the caller's URL is never fetched**.
   - Credentials: `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET_FILE` (a secret file, mounted into
     the api only). Without both it answers `422 spotify_disabled`.
   - The YouTube match is the existing `POST /v2/match`, which takes what resolve returns.

5. **`/v2` gains the mp3 (#77).**
   - `POST /v2/audio` takes `{"source_ref": "youtube:<id>", "bitrate", "title", "artists"}` and an
     `Idempotency-Key`. It is a worker job, polled with `GET /v2/jobs/{id}` until `audio_id` comes
     back.
   - `GET /v2/audio/{audio_id}` answers `audio/mpeg` as an attachment.
   - Only `youtube:` refs, gated by `ALLOW_YTDLP_SOURCING` as every YouTube path is (off by default
     in code; switching it on stays the owner's deployment choice).
   - Files are kept `AUDIO_TTL_S` (default a day) and then swept.
   - Returning the mp3 is **the owner's explicit direction** (2026-11-24). Every other `/v2` route
     still never returns audio, and analysis audio is still deleted after every job.

6. **No arbitrary URL fetching.** The old standalone `source_url` (any URL, downloaded by the worker)
   is not carried over. The backend fetches only:
   - Spotify's two fixed API hosts;
   - YouTube, through the existing source, behind `ALLOW_YTDLP_SOURCING`;
   - the hosts in `URL_ALLOWED_HOSTS`, for the existing `url` source, unchanged.

   Anything else is refused.

7. **The UI image is a small web service (#78).**
   - **The page:** upload a local file, or paste a Spotify track, album or playlist link and pick a
     track; see the YouTube match, the analysis (explicit tag, timeline, waveform) and the mp3
     download. It reuses the existing analysis and waveform scripts.
   - **It calls the backend server-side only,** at `SPOTDOWN_BACKEND_URL` (operator setting, checked
     at start, the only host it ever calls), with the key from `SPOTDOWN_BACKEND_KEY_FILE` (fails
     closed without it).
   - **The key never reaches the browser:** not in a page, script, response, header, cookie, log or
     error.
   - Each browser-facing route maps to one fixed backend route.
   - Ids from the browser are checked by pattern before they go into a backend path. Only JSON
     bodies are relayed, never the backend's headers.
   - Analyses use the fixed `cache_scope` `spotdown_ui` (`DELETE /v2/scopes/spotdown_ui` removes
     them).
   - The UI keeps no audio and no analysis of its own.
   - **Browser-facing protection is ADR 0003, carried over:**
     - a token session from `UI_ACCESS_TOKEN_FILE`, or one generated once in the UI's data
       directory;
     - `UI_ALLOWED_HOSTS` and `UI_PUBLIC_ORIGIN`;
     - same-origin checks on unsafe requests;
     - no CORS;
     - the strict CSP and headers of ADR 0004 Decision 6.

     `/healthz` is open and does not call the backend.

8. **Deploy shape (#79).** `deploy/compose.yaml` runs redis, api and worker as now, with no port
   published, and adds the `ui` service.
   - The ui joins the `service` network to reach `spotdown-api`.
   - It publishes port 8000 on the loopback by default (`SPOTDOWN_PUBLISH_HOST`, as the old stack
     did), for the owner's reverse proxy.
   - The UI's key is a Docker secret mounted into the ui and the api only.
   - A deploy without the UI needs only classbeat's key file.
   - Exposing the UI beyond the loopback is the owner's deploy step.

## Alternatives

- **One app with a UI switch (`SPOTDOWN_UI`, serving the full UI next to `/v2`).** It was drafted
  first, and the owner chose two images instead. One process would have held both guards (the key
  and the token session), both error formats and both sets of egress, so a fault in one could reach
  the other.
- **The UI analyses on its own.** The owner rejected it: the UI analyses only through the backend.
- **The UI uses classbeat's key.** Rejected: one leaked UI container would hold classbeat's key, and
  replacing it would break classbeat.
- **Per-key scopes or rights.** Rejected for now as more than the owner asked for. Decision 3 is
  where they would go.

## Consequences

- classbeat sees no change. `/v2` answers as before, with its key, its Host and its network.
- The owner runs one more container for the UI and installs two more secret files: the UI's key and
  the Spotify client secret.
- During #76 to #78 the old standalone app still works. #79 removes it, and the deprecated
  settings warn for one release.
- **The classbeat plan needs a matching amendment from the owner.** Its layer table still says
  "standalone extras off in service mode", and its role line says spotdown "never returns audio"
  (now: never, except the mp3 of `GET /v2/audio`, by owner direction). This repository cannot
  write to classbeat.
