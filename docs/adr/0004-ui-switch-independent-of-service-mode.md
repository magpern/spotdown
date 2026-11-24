# ADR 0004: the web UI is its own switch, independent of service mode

**Status:** Proposed (2026-11-24), for spotdown#68 (Opus Developer, Opus Reviewer). It becomes
Accepted when the Opus review of its PR passes.

**Owner direction (2026-11-24):** "Lighten up: service mode cannot run UI. If I want UI I want UI.
Backend should be able to serve even if UI exists."

**Amends:** the layer table of [`docs/integration-plan.md`](../integration-plan.md) ("Standalone
extras (UI, ...) off in service mode": the UI is now a switch of its own) and two statements of
[ADR 0002](0002-local-file-input-in-standalone-ui.md): the constraint "The UI is off in service mode"
and the constraint that the service key "must never reach a browser", and its rejected alternative
"Serve the UI in service mode and let it call `PUT /v2/uploads`", which named this change as the
owner's plan change. The classbeat plan's own layer table says the same as spotdown's summary; this
ADR records the owner's decision for spotdown, and the classbeat plan is to be amended to match
(an Architect task in classbeat).

## Context

Until now `SERVICE_MODE=true` turned the whole standalone UI off: the page, its scripts, the
fixed-name asset route, `/files`, the standalone routes and the session. `SERVE_UI` worked in
standalone mode only. The owner wants a deployment that serves the v2 service API and the UI from
one process, without giving up any service-mode guarantee.

## Decision

1. **One switch, `SPOTDOWN_UI` (on or off), independent of `SERVICE_MODE`.**
   - Unset in service mode: **off**. Only `SPOTDOWN_UI=on` turns the UI on there. `SERVE_UI` is the
     standalone switch and is ignored in service mode, so a stray `SERVE_UI=true` (it is in
     `.env.example`) never exposes anything. classbeat's internal deployment and the container image
     set neither, so they are unchanged.
   - Unset in standalone mode: `SERVE_UI` (default on), as before. `SPOTDOWN_UI` overrides it.
   - `Settings.ui_enabled` is the one place that decides.
2. **In service mode the UI is static files only** (`app/service_ui.py`): `GET /` serves
   `service.html`, and `GET /{name}` serves a fixed list of that page's own files
   (`SERVICE_UI_ASSETS`; the standalone-only `ui.js` and `upload.js` are not in it). No path is built
   from input. These routes need no key: they hold no data. The asset route is included last, after
   `/healthz` and every `/v2` route; the service-key middleware refuses every `/v2` path without the
   key before routing.
3. **Everything else of service mode is unchanged with the UI on.** `/v2` needs the key (401
   without); `/files`, `/api/...`, `/login`, `/docs`, `/redoc` and `/openapi.json` are 404; no CORS;
   audio is deleted after every job and never served; the standalone session and its routes do not
   exist in service mode.
4. **The page analyses only through the authenticated v2 API**: `PUT /v2/uploads`,
   `POST /v2/analyses`, `GET /v2/jobs/{id}` and `GET /v2/analyses/{id}`, all in the fixed
   `cache_scope` `spotdown_ui`, each with a fresh random `Idempotency-Key`. It reuses
   `analysis_model.js` and `analysis_view.js` (explicit tag, timeline, waveform from the picked file:
   the server never sends audio).
5. **The service key reaches the browser only because the user types it in.**
   - It is held in one JavaScript variable: never in localStorage, sessionStorage, IndexedDB, a
     cookie, the URL, the history, the DOM (the input is cleared at once) or the console.
   - It is sent only as `Authorization: Bearer <key>`, the header `/v2` requires, on same-origin
     requests (`mode: "same-origin"`, `credentials: "omit"`, `referrerPolicy: "no-referrer"`).
   - A 401 forgets it and shows "Service key refused"; with no key the page shows "Service key
     required" and sends nothing.
   - A bearer header is never attached by the browser on its own, so a foreign page cannot make
     the browser send it (no CSRF), and without CORS it cannot read an answer.
6. **The page's responses carry a strict policy**: `Content-Security-Policy: default-src 'none';
   script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self';
   form-action 'none'; frame-ancestors 'none'; base-uri 'none'`, `X-Frame-Options: DENY`,
   `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store`. No
   inline script exists, so an injected string cannot run code that would read the key; every
   server string is escaped before it is rendered.

## Alternatives

- **Keep the UI off in service mode (ADR 0002).** Rejected by the owner (above).
- **A browser session for service mode (a login exchanging the key for a cookie).** Rejected for
  now: a new caller-authentication design, with CSRF and origin checks of its own (ADR 0003's), for
  no gain over a bearer the browser never sends by itself.
- **Store the key for the user's convenience.** Rejected: any stored copy outlives the tab and is
  readable by every script of the origin.

## Consequences

- An operator who sets `SPOTDOWN_UI=on` on a service deployment exposes the static page to whoever
  reaches the service; the data stays behind the key. Anyone who types the key into the page holds
  the full service key (every scope), as any other caller does.
- The UI's analyses share the `spotdown_ui` scope; `DELETE /v2/scopes/spotdown_ui` removes them.
- The image runbook: the container keeps the UI off in service mode unless `SPOTDOWN_UI=on` is set;
  set it only on a deployment whose network you would expose the page to.
- Tests: `tests/test_service_mode.py` holds the mode x switch x path table (mutation-checked), and
  `tests/test_ui_service.py` the key flow, a hostile-string render and a real v2 job with the UI on.
