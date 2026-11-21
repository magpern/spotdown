# ADR 0003: standalone exposure: a token always, plus same-origin requests and loopback defaults

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
- **`SameSite=Strict` is not enough.** It blocks cross-*site* requests. It does not block same-site,
  cross-origin ones: another port on `localhost`, or a sibling host under the same registrable domain.
- **DNS rebinding.** A page under an attacker's name that resolves to `127.0.0.1` is same-origin
  with itself. Only checking `Host` stops it.
- **Bind and publishing.** The `Dockerfile` runs `uvicorn ... --host 0.0.0.0`, which a container
  needs for port publishing. `docker-compose.yml` publishes `api` as `"8000:8000"`, which means
  every host interface. Redis is published as `"6379:6379"` with no password, although only the
  containers use it.
- **No caller authentication.** The standalone app has none. Service mode has its service key,
  installs no CORS and serves no UI.
- **Exposure cannot be detected.** Three review rounds of PR #53 each found a new way around a design
  that required a token only when the app detected that it was exposed:
  - **Round 1, B2:** a token tied to `ui_allowed_hosts` missed a plain `0.0.0.0` publish.
  - **Round 2, B3:** a "container gateway" exception let a bare host or WSL2 accept its LAN router.
  - **Round 3, B1 and B2:** a same-host TLS reverse proxy is a loopback peer with a matching
    `Origin`, and `--lifespan off` bypassed a launcher-only rule.

  The app cannot see reverse proxies, port forwarders (`socat`, `ssh -L`/`-R`, `netsh portproxy`,
  the WSL2 relay), compose overrides or `network_mode: host`. Any detection is a blocklist with
  holes.

## Decision

1. **A token always, in standalone mode.**
   - **Scope.** Every standalone route needs a valid session. That covers `/`, the UI assets,
     `/api/...`, `/files/...` and #49's upload route. There are exactly three exceptions, named in
     code:
     - `GET /login`;
     - `POST /login`;
     - `GET /healthz`. It is the handler shared with service mode, returning
       `{"ok": ..., "redis": ...}` (liveness and whether Redis answers). It is unchanged, and
       classbeat probes it in service mode.
   - **No detection at all.** There is no exposure detection, no peer, gateway or local-address
     exception, no container signal and no launcher requirement. Whether the app runs on loopback,
     in a container, behind a proxy or behind a forwarder changes nothing about authentication.
   - **Without a session:**
     - a `GET /` whose `Accept` includes `text/html` gets `303` to `/login` (usability only, not a
       gate);
     - every other request gets `401`.
2. **The token: owner-supplied, or generated once.**
   - **Owner-supplied.** If `standalone_access_token_file` is set, the token is read from that file.
     It must hold at least 32 characters after stripping trailing whitespace.
   - **Generated.** If it is not set, the token lives at `data_dir/standalone_token`.
     - If that file does not exist at construction, the app generates `secrets.token_hex(32)`
       (32 random bytes) and creates the file with `os.open(path, O_WRONLY | O_CREAT | O_EXCL,
       0o600)`.
     - `O_EXCL` means two processes cannot both write it: the loser reads the winner's file.
   - **Console line.** At construction the app prints one line: the **path** of the token file and
     "open /login and paste it". It never prints, logs or returns the token itself.
   - **Mode bits are set, not checked.** On create the mode is `0600`. On read the mode is not
     enforced: a bind mount on Docker Desktop for Windows can show other bits. The token's secrecy
     rests on `data_dir` being the operator's own directory.
   - **No token means nothing is served.** An unreadable, empty or short token file, or a failure
     to create one, does not stop construction. Instead the auth middleware answers every request
     except `/healthz` with `503` and a message naming the file. Nothing is served.
   - **Rotation** is deleting the file, or editing the owner-supplied one, and restarting. Every
     session then ends.
   - **Generating a token by hand:** `openssl rand -hex 32`. The token must be random.
3. **Authentication runs in middleware installed when the app is built.**
   - `create_standalone_app` reads or creates the token and installs the auth middleware at
     construction. Nothing depends on the lifespan.
   - The same holds for the module-level `app.main:app`, whatever server runs it and with or
     without `--lifespan off`.
   - A settings error (item 5) raises during construction, so no app object exists at all.
4. **Login, logout and the session cookie.**
   - **`GET /login`** is one self-contained HTML response with inline CSS and JS and no other assets.
     It is served with:
     `Content-Security-Policy: default-src 'none'; style-src 'sha256-<css>'; script-src 'sha256-<js>';
     connect-src 'self'; form-action 'none'; frame-ancestors 'none'`.
   - **`POST /login`** takes `{"token": "..."}` as `application/json` and passes item 6's checks:
     `Origin`, exact content type and `X-Spotdown-UI`.
     - The token is compared with `hmac.compare_digest`.
     - A wrong token gets `401` and no cookie.
     - The right token sets the cookie and answers `204`.
     - **Rate limit:** one in-process limit for the whole app, not per peer, so no address is
       trusted. After 5 failures within 60 s, every login gets `429` with `Retry-After: 60` for the
       next 60 s. That lets anyone who can reach `/login` lock the owner out for a minute at a time.
       This is accepted for a single-user tool; brute force of a 32-byte token is infeasible anyway.
   - **`POST /logout`** passes item 6's checks, clears the cookie (`Max-Age=0`) and answers `204`.
   - **The cookie.**
     - **Value:** `<expiry>.<mac>`, which must match `^[0-9]{1,12}\.[0-9a-f]{64}$`. `<mac>` is the
       hex `HMAC-SHA256(HMAC-SHA256(token, "spotdown-session-v1"), "<expiry>")`.
     - **Verification:** the MAC is compared with `hmac.compare_digest`, and the expiry must be in
       the future and at most 12 h ahead. Anything else gets `401`.
     - **Lifetime:** 12 hours, with no server state. Rotating the token ends every session.
     - **Replay window:** a stolen cookie works until its expiry or a rotation. This is accepted.
     - **Name and flags.**
       - When the expected origin (item 6) is `https`, the cookie is named
         `__Host-spotdown_session`, with `Secure`, `HttpOnly`, `SameSite=Strict`, `Path=/` and no
         `Domain`.
       - Over plain `http`, a `__Host-` cookie cannot be set, so it is `spotdown_session` with
         `HttpOnly`, `SameSite=Strict` and `Path=/`.
     - Neither the token nor a cookie value is ever logged or returned in a body.
5. **Settings are validated at construction.**
   - **`ui_allowed_hosts`.** Each entry must be one of:
     - a DNS name of RFC 1123 labels;
     - an IPv4 literal;
     - a bracketed IPv6 literal such as `[::1]`.

     An empty entry, `*`, a port, a scheme, a path or whitespace raises.
   - **`ui_cors_origins` and `ui_public_origin`.** Each must be an exact `scheme://host[:port]` with
     scheme `http` or `https`, and nothing else. `*` raises.
6. **Defence in depth.** These no longer decide authentication. They limit what a browser page or a
   misconfiguration can do. Each has its own test.
   - **Same-origin unsafe requests.** Every unsafe method (every method except `GET`, `HEAD` and
     `OPTIONS`) needs two things before the body is read:
     - **`Origin`:** a present `Origin` equal to the expected origin, otherwise `403`. The expected
       origin is `ui_public_origin` if set. Otherwise it is `http://` plus the request's `Host`. It
       is never taken from the scheme uvicorn sees or from forwarded headers.
     - **`Content-Type`:** exact, otherwise `415`. It is `application/json` (with optional
       `charset=utf-8`) for `/api/v1/convert`, `/login` and `/logout`, and
       `application/octet-stream` for #49's upload route.
   - **`X-Spotdown-UI`.** Every request under `/api/` needs `X-Spotdown-UI: 1`, otherwise `403`.
     - A non-safelisted header cannot be sent cross-origin without a preflight, which never
       succeeds.
     - `<img>`, `<form>` and `no-cors` requests cannot set it.
     - It is preferred to `Sec-Fetch-Site` because not every browser sends that header.
   - **`Host` allowlist.** `ui_allowed_hosts` (default `localhost`, `127.0.0.1`, `[::1]`, any port)
     applies to every standalone route, the three exceptions included.
     - Parsing handles bracketed IPv6: `[::1]:8000` and `[::1]` are host `::1`.
     - An unlisted or unparsable `Host` gets `403`.
   - **CORS allowlist.** `ui_cors_origins` defaults to empty, which means no CORS headers.
     - A listed origin is echoed exactly, with `allow_credentials=False`.
     - #49's route stays exempt.
   - **Loopback publishing.**
     - `docker-compose.yml` publishes `api` on `${SPOTDOWN_PUBLISH_HOST:-127.0.0.1}:8000:8000`. The
       explicit setting `SPOTDOWN_PUBLISH_HOST` widens it, and the app never reads that variable.
     - Redis gets no `ports`.
     - `README.md` runs `uvicorn app.main:app --host 127.0.0.1 --no-proxy-headers`.
   - **Forwarded headers ignored.** The `Dockerfile` and `README.md` run uvicorn with
     `--no-proxy-headers`. In any case, nothing in this ADR reads the scheme or the client address,
     so a forwarded header can change no decision.
7. **Service mode is out of scope.** Its service-key authentication, routes, `/healthz` and listening
   address are unchanged. None of these middlewares or settings checks is installed there.

## Alternatives

- **Require a token only when the app detects exposure.** This was tried in three revisions of this
  ADR, through the loopback names in `ui_allowed_hosts`, the bind and publish host, a container
  signal, peer and gateway checks, and a launcher. Each review round found a hole (see Context).
  Rejected: the app cannot observe the proxies and forwarders in front of it, so detection is a
  blocklist. A token always is an allowlist, the only way in, with no environment fact to infer.
- **A token always, but only on routes with side effects.** Not chosen. It is stricter to protect
  every route: `/files` serves converted audio, and the analysis and task routes leak what the user
  has worked on. The UI needs the session anyway.
- **Leave loopback without a token for convenience.** Rejected: loopback is exactly what a same-host
  proxy or forwarder presents. The generated token keeps the friction to one paste per 12 h.
- **Print the token at startup.** Rejected: console output ends up in `docker compose logs` and in
  CI logs. Printing the path is enough.
- **Enforce `0600` on read.** Not chosen: bind mounts on Docker Desktop for Windows can show other
  bits, which would lock the owner out for a cosmetic reason. The bits are set on create.
- **A per-peer login rate limit.** Not chosen: it trusts the peer address, which a proxy or forwarder
  rewrites. A global limit trusts nothing.
- **Keep the wildcard CORS for "dev convenience".** Rejected: it lets any page drive conversions and
  read results. A developer who needs a cross-origin editor lists that origin in `ui_cors_origins`.
- **`Sec-Fetch-Site` instead of a custom header, or moving the GETs with effects to `POST`.**
  Rejected: not every browser sends the header, and moving the GETs would change the documented v1
  API for no extra protection.
- **Server-side sessions.** Not chosen: they need state shared across processes. A signed expiry is
  enough for one operator-held token.
- **HTTP Basic authentication.** Not chosen: browsers cache it with no logout or expiry, and send it
  with every request, including cross-site ones.

## Consequences

- **Nothing in standalone mode is served without the token,** wherever the app runs and whatever
  sits in front of it, except `/login` and `/healthz`.
- **First start.** The owner opens `/login`, pastes the token from the file the console names
  (`data/standalone_token` in the compose layout), and has a session for up to 12 h.
- **Scripts** that call the standalone API first `POST /login` with `Origin`, the JSON type and
  `X-Spotdown-UI`, then send the cookie and `X-Spotdown-UI` on every `/api/` request.
- **Plain HTTP beyond loopback** exposes the token and the cookie to anyone on the network path, and
  the cookie cannot use the `__Host-` prefix. Over plain HTTP the cookie is also shared with every
  other service on the same host name (browsers ignore ports for cookies), so those services are
  trusted with it. For any use beyond loopback, serve TLS and set `ui_public_origin=https://...`.
  The cookie then becomes `__Host-` and `Secure`.
- **A foreign web page** can load nothing it can read, and cannot start a conversion, an analysis or a
  Spotify lookup: it has no session, and item 6 stops it again if it had one. A rebound host name is
  refused.
- **A non-browser caller,** local or remote, can forge `Origin`, `Host` and the headers. Without the
  token it gets `401` everywhere except `/login` (rate-limited) and `/healthz`.
- **Who can read the token:** anyone who can read `data_dir` can read the token, and with it reach
  the app. That is the same trust boundary as the converted files kept there.
- **Redis** is no longer reachable from the host. A developer runs `docker compose exec redis
  redis-cli`.
- **The owner's stack.** When the owner pulls this change, their compose stack publishes on
  `127.0.0.1` unless they set `SPOTDOWN_PUBLISH_HOST`, and the UI asks for the token once.
  `localhost:8000` keeps working.
- **Service mode** and classbeat are unaffected.
