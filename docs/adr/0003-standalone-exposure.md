# ADR 0003: standalone exposure: same-origin requests, loopback by default, a token beyond loopback

**Status:** Proposed (2026-11-21). It becomes Accepted when its Opus review passes. The Architect
then sets it to Accepted, citing the PASS comment, in its next PR that touches
`docs/integration-plan.md`. That PR comes before #52's PR merges.

**Issue:** spotdown#52 (Opus Developer). It is built before spotdown#49, whose route-level checks
(ADR 0002) stay on top of these as defence in depth.

## Context

The standalone app (the web UI, Spotify URL resolution, conversions and `/files` downloads) is open
to any web page and to the whole network. The Opus review of PR #51 found this (B3), and ADR 0002
recorded it as a follow-up.

- **CORS.** `app/main.py` `create_standalone_app` installs
  `CORSMiddleware(allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])`. Any page open
  in the user's browser can call every standalone route and read the answer.
- **Cross-site requests are sent even without CORS.** Removing CORS stops a foreign page from
  *reading* answers, not from *sending* requests.
  - A `fetch(..., {mode: "no-cors", method: "POST", body: new Blob([json])})` carries no
    `Content-Type`, so it stays a CORS-simple request. FastAPI parses such a body as JSON.
    `POST /api/v1/convert` would then make the worker download a URL the attacker chose
    (`ConvertRequest.source_url`): SSRF from the user's machine into their network, plus disk use.
  - An `<img src>` is enough to fire the GETs with effects:
    - `/api/Gettrack` calls Spotify;
    - `/api/v1/analyze/{id}` runs a CPU-heavy analysis on a cache miss or with `?refresh=true`.
  - Private Network Access (Local Network Access) is not in every browser, so it cannot be relied on.
- **Bind and publishing.**
  - The `Dockerfile` runs `uvicorn ... --host 0.0.0.0`. Inside a container that is required, or
    port publishing cannot reach the process, so the app cannot see from its own bind how far it is
    exposed.
  - `docker-compose.yml` publishes `api` as `"8000:8000"`, which means every host interface.
  - Outside Docker, an operator can run `uvicorn --host 0.0.0.0` directly.
- **Redis is published too.** `docker-compose.yml` publishes Redis as `"6379:6379"` on every
  interface, with no password, although only the `api` and `worker` containers use it, over the
  compose network.
- **No caller authentication.** The standalone app has none at all. Service mode is different: it
  has its service key, installs no CORS and serves no UI.
- **DNS rebinding.** A page under an attacker's name that resolves to `127.0.0.1` is same-origin
  with itself. Neither CORS nor binding to loopback stops it; only checking `Host` does. The `Host`
  check does not stop a plain cross-site request, though: a page on `evil.example` that sends to
  `http://localhost:8000` sends `Host: localhost:8000`.
- **`SameSite=Strict` is not enough.** It blocks cross-*site* requests. It does not block same-site,
  cross-origin ones: another port on `localhost`, or a sibling host under the same registrable domain.

## Decision

1. **CORS is an allowlist, same-origin by default.**
   - A new setting `ui_cors_origins` takes comma-separated exact origins (scheme, host, port). It
     defaults to empty, which means no CORS headers at all: same-origin only.
   - A value containing `*`, or one that is not an exact origin, fails startup.
   - A listed origin gets exactly that origin back in `Access-Control-Allow-Origin`, never `*`.
   - Credentials are never allowed cross-origin (`allow_credentials=False`).
   - Routes that ADR 0002 exempts from CORS (#49's upload route) stay exempt, even for listed
     origins.
2. **Every request with an effect must come from the UI's own origin.** This holds in every mode,
   token or not. It is the defence against cross-site and same-site cross-origin requests. CORS and
   `SameSite` are not.
   - **Every non-safe method** (`POST`, `PUT`, `PATCH`, `DELETE`) on a standalone route must pass
     both of these before the body is read:
     - **Origin:** a present, same-origin `Origin`, compared exactly as in ADR 0002 Decision 2 (the
       scheme uvicorn sees plus `Host`, or `ui_public_origin` when set). Otherwise `403`.
     - **Content type:** an exact `Content-Type`, otherwise `415`. It is `application/json`
       (parameter `charset=utf-8` allowed) for `POST /api/v1/convert`, `POST /login` and
       `POST /logout`, and `application/octet-stream` for #49's upload route. No CORS-simple type is
       ever accepted.
   - **Every request under `/api/`**, any method, must carry the header `X-Spotdown-UI: 1`.
     Otherwise `403`. The UI's `fetch` calls set it.
     - It covers the GETs with effects (`/api/Gettrack`, `/api/v1/analyze/{id}` including
       `?refresh=true`) and the read-only `/api/v1/tasks/{id}`, under one rule.
     - A custom header is not CORS-safelisted. A cross-origin browser request can carry it only
       after a successful preflight, and no preflight succeeds unless the origin is in
       `ui_cors_origins`.
     - An `<img>`, a `<form>` or a `no-cors` `fetch` cannot set it at all.
   - **Why a custom header, not the other options.**
     - Not `Sec-Fetch-Site`: not every browser sends it (Safari before 16.4 does not). Failing closed
       when it is missing would break those users; failing open would leave the gap.
     - Not moving the GETs to `POST`: that would change the documented standalone v1 API for no
       extra protection.
     - Non-browser clients (scripts, `curl`) add the header themselves; it is not a secret.
   - `/`, the UI assets and `GET /files/{name}` stay plain GETs without the header. They have no
     effect, a foreign page cannot read their answers, and links and downloads must keep working.
3. **Loopback by default, decided where the port is published.**
   - **Launcher.** A small launcher, `python -m app.serve`, runs uvicorn on `SPOTDOWN_BIND_HOST`
     (default `127.0.0.1`) and port 8000. `README.md` uses it.
   - **Container.** The `Dockerfile` sets `SPOTDOWN_BIND_HOST=0.0.0.0` and runs the launcher, so
     service deployments still listen as before.
   - **Publishing.** `docker-compose.yml` publishes `api` on
     `${SPOTDOWN_PUBLISH_HOST:-127.0.0.1}:8000:8000`, and passes the same value into the `api`
     container as the environment variable `SPOTDOWN_PUBLISH_HOST`. The explicit setting
     `SPOTDOWN_PUBLISH_HOST` (for example `0.0.0.0`) widens it.
   - **Redis** gets no `ports` at all; the containers reach it as `redis:6379` on the compose network.
   - The owner's existing local stack keeps working at `localhost:8000`.
4. **A `Host` allowlist for every standalone route (DNS rebinding).**
   - The setting `ui_allowed_hosts` (introduced by ADR 0002) applies app-wide in standalone mode,
     as a middleware in front of every route, the token form included.
   - It defaults to the loopback names `localhost`, `127.0.0.1` and `[::1]`, with any port. Entries
     are exact host names or addresses; a `*` fails startup.
   - The `Host` header is parsed with bracketed IPv6 in mind:
     - `[::1]:8000` is host `::1`, port 8000;
     - `[::1]` is host `::1`;
     - `localhost:8000` is host `localhost`.
   - The host is lowercased and compared with the entries. An unlisted or unparsable `Host` gets
     `403`, and nothing else runs.
5. **A token whenever the app may be reachable beyond loopback, failing closed.**
   - **What triggers the token.** Standalone mode is *exposed* when any of these holds:
     - (a) `ui_allowed_hosts` lists a name other than the loopback names;
     - (b) `SPOTDOWN_PUBLISH_HOST` is set to anything other than `127.0.0.1`, `::1` or `localhost`;
     - (c) `SPOTDOWN_BIND_HOST` is not loopback and `SPOTDOWN_PUBLISH_HOST` is **unset**. This is a
       non-loopback bind outside compose, or a container started without the compose file. It fails
       closed.
   - **Startup.** When exposed, standalone mode refuses to start unless `standalone_access_token_file`
     names a readable file holding a token of at least 32 characters. An empty, short or unreadable
     file also fails startup. The token comes only from that file, never from a plain environment
     value, and is never logged.
     - The token must be random. The operator generates it with `openssl rand -hex 32`, and
       `README.md` documents that.
   - **The peer layer, a further check.** With no token configured, any request whose peer address
     is not loopback gets `403`.
     - That catches a direct `uvicorn --host 0.0.0.0` that bypasses the launcher.
     - Inside a container where (b) and (c) do not hold, the published port is loopback-only. The
       peer is then the container's default gateway, read from `/proc/net/route`, which is also
       accepted.
     - Docker's userland proxy can present any client as that gateway. So in a container this layer
       adds little, and the startup trigger is the defence.
   - **With a token configured, every standalone route needs a session.** That covers `/`, the UI
     assets, `/api/...` and `/files/...`. There are three exceptions:
     - `GET /login` and `POST /login`;
     - `GET /healthz`, which returns only `{"status": "ok"}` and reveals nothing.

     Without a valid session:
     - a browser `GET /` (whose `Accept` includes `text/html`) gets a `303` redirect to `/login`;
     - every other request gets `401`.
   - **`POST /login`.**
     - It takes `{"token": "..."}` as `application/json` and passes item 2's checks: `Origin`,
       content type and `X-Spotdown-UI`.
     - It compares the token with `hmac.compare_digest`. A wrong token gets `401` and no cookie. The
       right token sets the session cookie and answers `204`, and the login page then goes to `/`.
     - **Rate limit:** in-process, per peer address. After 5 failures within 60 s, every login from
       that peer gets `429` with `Retry-After: 60` for the next 60 s. Behind Docker's proxy all
       peers can share one address, so the limit then applies to everyone, which fails safe.
   - **`POST /logout`** passes item 2's checks, clears the cookie (`Max-Age=0`) and answers `204`.
   - **The session cookie.**
     - **Value:** `<expiry>.<mac>`. `<expiry>` is a Unix time in decimal digits only. `<mac>` is the
       hex `HMAC-SHA256(key, "<expiry>")`, where the key is `HMAC-SHA256(token,
       "spotdown-session-v1")`.
     - **Verification:** the value must match `^[0-9]{1,12}\.[0-9a-f]{64}$`. The expiry must be in
       the future and at most 12 h ahead. The MAC is compared with `hmac.compare_digest`. Anything
       else gets `401`.
     - **Lifetime:** 12 hours. Sessions need no server state, work across processes, and all end
       when the token is changed.
     - **Replay window:** a stolen cookie works until its expiry, at most 12 h, or until the token is
       rotated. This is accepted; `HttpOnly` keeps it from page scripts.
     - **Flags:** `HttpOnly`, `SameSite=Strict` and `Path=/`, plus `Secure` when the expected origin
       is `https`.
     - Neither the token nor a cookie value is ever logged or returned in a body.
   - **TLS.** For any use beyond loopback, the operator should serve the UI over TLS and set
     `ui_public_origin=https://...`, so the token and the cookie never cross the network in clear
     text. Plain HTTP beyond loopback is allowed but documented as unsafe (Consequences).
   - **Not exposed means no token.** With none of (a) to (c), no token is needed. The defences are
     then loopback publishing, the peer layer, the `Host` allowlist, no CORS and item 2.
6. **Service mode is out of scope.** Its service-key authentication, its routes and its listening
   address are unchanged. None of these middlewares or startup checks is installed there.

## Alternatives

- **Bind the process to `127.0.0.1` inside the container.** Rejected: published ports could not
  reach it, so the container would be unusable. Publishing on loopback, and telling the app the
  publish host, gives the same exposure.
- **Keep the wildcard CORS for "dev convenience".** Rejected: it lets any page drive conversions and
  read results. A developer who needs a cross-origin editor lists that origin in `ui_cors_origins`.
- **Rely on removing CORS, or on `SameSite`.** Rejected: neither stops a foreign page from *sending*
  a simple request, and `SameSite` lets same-site origins through (item 2).
- **`Sec-Fetch-Site` instead of a custom header, or moving the effectful GETs to `POST`.** Rejected;
  the reasons are in item 2.
- **A token always, even on loopback.** Not chosen. On loopback, item 2, the `Host` allowlist and
  the peer layer leave only local processes able to drive the app. A token would add friction to the
  single-user test tool for little gain. It is a reversible setting.
- **Trigger the token only from `ui_allowed_hosts`.** Rejected: publishing on `0.0.0.0` alone would
  expose an unauthenticated app to the network, because any client can send `Host: localhost`.
- **No token beyond loopback, "trusted network" only.** Rejected: any device on that network could
  then start conversions and feed files to the decoder.
- **Server-side sessions.** Not chosen: they need state shared across processes. A signed expiry is
  enough for one operator-held token.
- **HTTP Basic authentication.** Not chosen: browsers cache it with no logout or expiry, and send it
  with every request, including cross-site ones.

## Consequences

- **What a foreign web page can still do on the default loopback setup:** load `/`, the assets or a
  `/files` link in a way it cannot read. It cannot start a conversion, an analysis or a Spotify
  lookup, and it cannot read any answer. A rebound host name is refused.
- **What local processes can do:** any local process can still drive the app without a token, as
  with any loopback service. The `Origin`, `Host`, content-type and `X-Spotdown-UI` checks stop
  browsers only.
- **Exposure always brings a token.** Publishing beyond loopback (`SPOTDOWN_PUBLISH_HOST`), a
  non-loopback bind outside compose, or a non-loopback name in `ui_allowed_hosts` makes startup
  demand a token. A network-reachable standalone app started through the launcher or the compose
  file is therefore never unauthenticated. A direct `uvicorn --host 0.0.0.0` that bypasses both
  still meets the peer layer, which refuses non-loopback peers.
- **Plain HTTP beyond loopback exposes the token and the cookie** to anyone on the network path. TLS
  with `ui_public_origin=https://...` is the documented setup for that case.
- **Clients that break:**
  - Scripts that call `/api/...` must add `X-Spotdown-UI: 1`.
  - A `POST` must send `Content-Type: application/json` and a same-origin `Origin`.
  - A cross-origin editor preview stops working until its origin is listed in `ui_cors_origins`.
- **Redis** is no longer reachable from the host. A developer who needs it runs `docker compose exec
  redis redis-cli`.
- **The owner's stack.** When the owner pulls this change, their compose stack publishes on
  `127.0.0.1` only, unless they set `SPOTDOWN_PUBLISH_HOST`. `localhost:8000` keeps working.
- **Service mode** and classbeat are unaffected. Service deployments keep listening on `0.0.0.0` in
  the container, through the launcher.
