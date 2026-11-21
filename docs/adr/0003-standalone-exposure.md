# ADR 0003: standalone exposure: no wildcard CORS, loopback by default, a token beyond loopback

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
  in the user's browser can call every standalone route and read the answer, including
  `POST /api/v1/convert`, which starts a download job, and `/files`.
- **Bind and publishing.** The `Dockerfile` runs `uvicorn ... --host 0.0.0.0`. Inside a container
  that is required, or port publishing cannot reach the process. `docker-compose.yml` publishes
  `api` as `"8000:8000"`, which means every host interface.
- **Redis is published too.** `docker-compose.yml` publishes Redis as `"6379:6379"` on every
  interface, with no password, although only the `api` and `worker` containers use it, over the
  compose network.
- **No caller authentication.** The standalone app has none at all. Service mode is different: it
  has its service key, installs no CORS and serves no UI.
- **DNS rebinding.** A page under an attacker's name that resolves to `127.0.0.1` is same-origin
  with itself. Neither CORS nor binding to loopback stops it; only checking `Host` does.

## Decision

1. **CORS is an allowlist, same-origin by default.**
   - A new setting `ui_cors_origins` takes comma-separated exact origins (scheme, host, port). It
     defaults to empty, which means no CORS headers at all: same-origin only.
   - A value containing `*`, or one that is not an exact origin, fails startup.
   - A listed origin gets exactly that origin back in `Access-Control-Allow-Origin`, never `*`.
   - Credentials are never allowed cross-origin (`allow_credentials=False`), so the session cookie
     of item 4 is never sent with a CORS request that can read the answer.
   - Routes that ADR 0002 exempts from CORS (#49's upload route) stay exempt, even for listed
     origins.
2. **Loopback by default, decided where the port is published.**
   - Inside the container the process keeps listening on `0.0.0.0`.
   - `docker-compose.yml` publishes `api` on `${SPOTDOWN_PUBLISH_HOST:-127.0.0.1}:8000:8000`.
     The explicit setting `SPOTDOWN_PUBLISH_HOST` (for example `0.0.0.0`) widens it.
   - Redis gets no `ports` at all; the containers reach it as `redis:6379` on the compose network.
   - Outside Docker, `README.md` and any launcher run `uvicorn ... --host 127.0.0.1`.
   - The owner's existing local stack keeps working at `localhost:8000`.
3. **A `Host` allowlist for every standalone route (DNS rebinding).**
   - The setting `ui_allowed_hosts` (introduced by ADR 0002) applies app-wide in standalone mode,
     as a middleware in front of every route, the token form included.
   - It defaults to the loopback names `localhost`, `127.0.0.1` and `[::1]`, with any port. Entries
     are exact host names or addresses; a `*` fails startup.
   - A request whose `Host` (port stripped, lowercased) is not listed gets `403`. Nothing else runs.
4. **Beyond loopback, a token is required, and the app fails closed without one.**
   - **When a token is required.** If `ui_allowed_hosts` lists any name other than the loopback
     names, the operator means to reach the UI over the network. Standalone mode then refuses to
     start unless `standalone_access_token_file` names a readable file holding a token of at least
     32 characters. An empty, short or unreadable file also fails startup. The token comes only from
     that file, never from a plain environment value, and is never logged.
   - **With a token configured, every standalone route needs a session.** That covers `/`, the UI
     assets, `/api/...` and `/files/...`. A request without a valid session gets `401`. The only
     exceptions are the token form (`GET /login` and `POST /login`).
   - **`POST /login`.** It takes the token as `application/x-www-form-urlencoded`. It passes
     the `Host` check, and it has the same-origin `Origin` check of ADR 0002 Decision 2 (including
     `ui_public_origin` behind a TLS proxy), so no other site can post a login. It compares the
     token with `hmac.compare_digest`.
     - A wrong token gets `401` and no cookie.
     - The right token sets the session cookie and redirects to `/`.
   - **The session cookie.**
     - Its value is an expiry time plus `HMAC-SHA256(key, expiry)`, where the key is derived from the
       token (`HMAC-SHA256(token, "spotdown-session-v1")`). Sessions therefore need no server state,
       work across processes, and all end when the token is changed.
     - The lifetime is 12 hours.
     - Flags: `HttpOnly`, `SameSite=Strict` and `Path=/`, plus `Secure` when the expected origin is
       `https`.
     - Neither the token nor a cookie value is ever logged or returned in a body.
   - **With only loopback names, no token is needed.** Loopback publishing, the `Host` allowlist
     and no CORS are then the defence.
5. **Service mode is out of scope.** Its service-key authentication, its routes and its deployment
   bind are unchanged. None of these middlewares is installed there.

## Alternatives

- **Bind the process to `127.0.0.1` inside the container.** Rejected: published ports could not
  reach it, so the container would be unusable. Publishing on loopback gives the same exposure.
- **Keep the wildcard CORS for "dev convenience".** Rejected: it lets any page drive conversions and
  read results. A developer who needs a cross-origin editor lists that origin in `ui_cors_origins`.
- **A token always, even on loopback.** Not chosen: on loopback, with the `Host` check and no CORS,
  a browser page cannot reach the app, and only local processes can. A token would add friction to
  the single-user test tool for little gain. It is a reversible setting.
- **No token beyond loopback, "trusted network" only.** Rejected: any device on that network could
  then start conversions and feed files to the decoder.
- **Server-side sessions.** Not chosen: they need state shared across processes. A signed expiry is
  enough for one operator-held token.
- **HTTP Basic authentication.** Not chosen: browsers cache it with no logout or expiry, and send it
  with every request, including cross-site ones that the form-and-cookie design blocks with
  `SameSite=Strict`.

## Consequences

- By default, the standalone app is reachable only from the machine it runs on, from no foreign
  origin, and under no rebound host name.
- Reaching it from another device takes two explicit steps: set `SPOTDOWN_PUBLISH_HOST`, and list the
  name in `ui_allowed_hosts`. The second step makes the app demand a token, so a network-reachable
  standalone app is never unauthenticated.
- A cross-origin editor preview that called the API stops working until its origin is listed in
  `ui_cors_origins`.
- Redis is no longer reachable from the host. A developer who needs it runs `docker compose exec redis
  redis-cli`.
- Like ADR 0002's checks, the `Origin` and `Host` checks stop browsers only. A non-browser caller that
  reaches the port can forge them. On loopback that caller is a local process; beyond loopback it
  must hold the token.
- When the owner pulls this change, their compose stack publishes on `127.0.0.1` only, unless they
  set `SPOTDOWN_PUBLISH_HOST`. `localhost:8000` keeps working.
- Service mode and classbeat are unaffected.
