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
- [ADR 0004](0004-ui-switch-independent-of-service-mode.md) except its Decision 6: there is no
  service mode, no `SPOTDOWN_UI` switch and no static service page, and the browser never holds the
  service key. Decision 6's response headers and CSP carry over to the UI image (Decision 7).
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
   - Both keys have the same rights on `/v2`; no per-key scopes (plain and simple). The two keys are
     separate so that the owner can replace one without touching classbeat's. Replacing a key is:
     write the file, restart.

4. **`/v2` gains the Spotify link step (#76). Spotify credentials are optional.**
   - The owner's decisions, 2026-11-24:
     - first, "Spotdown should not need any Spotify client ID. It is given a URL, that's it.";
     - then, refining it, "Yes do that": credentials optional, with a public-page fallback.
   - `POST /v2/spotify/resolve` takes `{"url"}`: a track, album or playlist link, or a `spotify:`
     URI.
   - It answers the tracks (`spotify_id`, `title`, `artists`, `album`, `duration_ms`, `isrc`), with
     `listed`, `total` and `truncated`, at most 500.
   - The input is parsed strictly to (kind, id), and the backend builds every upstream URL itself.
     **The caller's URL is never fetched as given**, and no redirect is followed to another host.
     An upstream response over a fixed cap (5 MiB) is refused unread.
   - **With credentials set** on the backend (`SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET_FILE`, a
     secret file mounted into the api only, never logged):
     - the official Web API resolves the link, reusing `app/spotify.py`'s client-credentials code;
     - this gives full playlists and the ISRC;
     - egress: `accounts.spotify.com` and `api.spotify.com`.
   - **Without credentials** (the default):
     - the link is resolved from Spotify's public pages on `open.spotify.com`, the only host called:
       the oEmbed endpoint (today's fallback) and the public embed page
       (`/embed/{track|album|playlist}/{id}`), for artists, durations and the track list;
     - no call goes to `accounts.spotify.com` or `api.spotify.com`.
   - **The setting is the backend's own.** No caller ever sends spotdown credentials, and no request
     can choose the path. classbeat is unchanged: it resolves Spotify itself and sends metadata to
     `POST /v2/match`.
   - **A page that gives less is not a failure.** A missing field is `null` or empty, and the
     matcher works without it. Today `POST /v2/match` requires `duration_s` and at least one
     artist; #76 relaxes that additively (both may be absent or empty, recorded in
     `docs/contract-v2.md`), and the matcher never scores on a missing duration. classbeat's
     requests, which carry every field, are answered as before. A playlist page that lists fewer tracks than the playlist
     has gives what it lists and reports the shortfall (`truncated`, `listed`, `total`). Only a
     page with no usable track at all is an error.
   - **Both paths are faked in tests.** No CI job or test calls Spotify. *(Amended 2026-11-24:
     agents may check the public-page parsing by hand, read-only, as AGENTS.md "Safety envelope"
     allows (owner, 2026-11-24, #85).)*
     - The public-page parsing is an **unverified assumption**: it is written to the pages' observed
       shape and tested with fixtures. Spotify can change these pages without notice, so a live
       read-only check by hand (an agent's, per the Safety envelope, or the owner's first live try)
       is the check. If it fails, the parser and its fixtures are fixed; nothing else depends on
       the page format.
     - The API path reuses code that worked before, but it too is untested live in this programme.
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
   - Spotify: `open.spotify.com`'s public pages, or `accounts.spotify.com` and `api.spotify.com`
     only when the owner has set credentials, at paths it builds itself;
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
   - **Browser-facing protection is ADR 0003, carried over whole:**
     - a token session from `UI_ACCESS_TOKEN_FILE`, or one generated once in the UI's data
       directory, with ADR 0003's login, logout and session cookie;
     - `UI_ALLOWED_HOSTS` and `UI_PUBLIC_ORIGIN`;
     - same-origin checks on unsafe requests, with the `X-Spotdown-UI` header required on every
       `/api` request and the default-deny on unexpected content types (415);
     - no CORS, and no API docs;
     - uvicorn run with `--no-proxy-headers`;
     - the strict CSP and headers of ADR 0004 Decision 6.

     `/healthz` is open and does not call the backend.

8. **Deploy shape (#79).** `deploy/compose.yaml` runs redis, api and worker as now, with no port
   published, and adds the `ui` service.
   - The ui joins the `service` network to reach `spotdown-api`.
   - It publishes port 8000 for the owner's reverse proxy, as the old stack did. The bind address
     is a deploy setting, `SPOTDOWN_PUBLISH_HOST`, which the app never reads.
     - It defaults to the loopback (`127.0.0.1`).
     - For a reverse proxy on another host, the runbook shows the LAN bind: the host's own LAN
       address, which the owner sets in the deploy environment, not in the repository.
     - The backend publishes no port at all.
   - The UI's key is a Docker secret mounted into the ui and the api only.
   - A deploy without the UI needs only classbeat's key file.
   - Choosing the bind address and exposing the UI beyond the loopback is the owner's deploy step.

## Alternatives

- **One app with a UI switch (`SPOTDOWN_UI`, serving the full UI next to `/v2`).** It was drafted
  first, and the owner chose two images instead. One process would have held both guards (the key
  and the token session), both error formats and both sets of egress, so a fault in one could reach
  the other.
- **The UI analyses on its own.** The owner rejected it: the UI analyses only through the backend.
- **Publish the UI on every interface (`0.0.0.0`) by default.** Rejected: the safe default is the
  loopback, and a LAN bind is one setting away.
- **The UI uses classbeat's key.** Rejected: one leaked UI container would hold classbeat's key, and
  replacing it would break classbeat.
- **Require Spotify credentials.** Rejected by the owner: spotdown must work from a link alone.
- **Forbid Spotify credentials.** Rejected by the owner as the refinement: with them, playlists are
  complete and carry the ISRC.
- **Credentials per request from the caller.** Rejected: spotdown never receives anyone's
  credentials, so the setting is the backend's.
- **Per-key scopes or rights.** Rejected for now as more than the owner asked for. Decision 3 is
  where they would go.

## Consequences

- classbeat sees no change. `/v2` answers as before, with its key, its Host and its network.
- **A leaked UI key has classbeat's full rights on `/v2`**, including reading and deleting
  classbeat's scopes (Decision 3 gives both keys the same rights). The UI container holds it only in
  a secret file and never sends it to a browser. If it leaks, the owner replaces that one key
  without touching classbeat's. Narrowing the UI key's rights (for example to the `spotdown_ui`
  scope) is the follow-up if the owner wants it.
- The owner runs one more container for the UI and installs one more secret file, the UI's key.
  Spotify credentials are optional. With them, playlists are complete and carry the ISRC; the
  secret file is then a second, optional secret.
- During #76 to #78 the old standalone app still works. #79 removes it, and the deprecated
  settings warn for one release.
- **The classbeat plan needs a matching amendment from the owner.** Its layer table still says
  "standalone extras off in service mode", and its role line says spotdown "never returns audio"
  (now: never, except the mp3 of `GET /v2/audio`, by owner direction). This repository cannot
  write to classbeat.
