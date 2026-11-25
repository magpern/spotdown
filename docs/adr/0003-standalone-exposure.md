# ADR 0003: standalone exposure: a token always, plus same-origin requests and loopback defaults

**Superseded in part by [ADR 0005](0005-two-images-backend-api-and-ui.md) (owner, 2026-11-24):** these
protections now guard the separate UI image; the backend is API only and has no browser surface.

**Amendment (2026-11-25, spotdown#105): a list of public origins, matched by `Host`.** In the UI
image, `UI_PUBLIC_ORIGIN` (this ADR's `ui_public_origin`) may be a comma-separated list, so one UI
can be served under two public names. Items 5 and 6 change only as follows:

- **One value behaves exactly as before:** it is the expected origin for every allowed `Host`, and
  it is not checked against `UI_ALLOWED_HOSTS` (a proxy may rewrite `Host`).
- **Start-up, fail closed.** Each entry obeys item 5's rule for `ui_public_origin`. An empty entry
  or any whitespace raises: whitespace is **rejected, not trimmed**, as in `UI_ALLOWED_HOSTS`. A
  list of two or more also raises on two entries with the same host and port (duplicates
  included), on mixed `http` and `https` (so the cookie name and `Secure` of item 4 stay one choice
  for the process), and on an entry whose host is not in `UI_ALLOWED_HOSTS`.
- **The expected origin (item 6)** for a list is the entry whose host and port equal the request's
  `Host` (which has passed the allowlist). If none does, nothing matches: every unsafe request and
  `POST /login` from that `Host` gets `403`, while `GET` pages behave as before. The comparison
  rule stays exact, and forwarded headers stay ignored.
- **Sessions stay per host:** the `__Host-` cookie has no `Domain`, so a login on one name is not
  a login on the other (the access token is the same). The login rate limit stays process-wide.
- Out of scope: wildcards or patterns, and mixed schemes.

**Status:** Accepted (2026-11-21), after the Opus review of PR #53 passed at `1024de3`
([`Agent review (opus): PASS`](https://github.com/magpern/spotdown/pull/53#issuecomment-7064851523)). The follow-up PR (spotdown#57, for issue #54) folded in that review's
non-blocking findings: the assumptions and named outcomes that #52 implements, the lifespan
wording, and one `Origin` rule shared with ADR 0002.

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
     `/api/...`, `/files/...`, #49's upload route, FastAPI's `/docs`, `/docs/oauth2-redirect`,
     `/redoc` and `/openapi.json`, and any unknown path. There are exactly three exceptions, each
     matched on the **exact method and exact `scope["path"]`**:
     - `GET /login`;
     - `POST /login`;
     - `GET /healthz`. It is the handler shared with service mode, returning
       `{"ok": ..., "redis": ...}` (liveness and whether Redis answers). It is unchanged, and
       classbeat probes it in service mode.

     Variants are not exempt: `/login/`, `/LOGIN`, `//login`, `/healthz/` and `HEAD /healthz` all
     need a session. Percent-encoding is decoded before `scope["path"]`, so `/%6Cogin` is `/login`.
   - **No detection at all.** There is no exposure detection, no peer, gateway or local-address
     exception, no container signal and no launcher requirement. Whether the app runs on loopback,
     in a container, behind a proxy or behind a forwarder changes nothing about authentication.
   - **Without a session:**
     - a `GET /` whose `Accept` includes `text/html` gets `303` to `/login` (usability only, not a
       gate);
     - every other request gets `401`, unknown paths included, so routes cannot be enumerated.
2. **The token: owner-supplied, or generated once.**
   - **Owner-supplied.** If `standalone_access_token_file` is set, the token is that file's content
     with trailing whitespace stripped.
     - **Rule:** 32 to 512 characters, each printable ASCII from `0x21` to `0x7E` except `"` and
       `\`. Spaces, control characters and anything non-ASCII are excluded.
     - **Why:** such a token needs no JSON escaping and is one byte per character. So
       `{"token":"<token>"}` is at most 512 + 12 = 524 bytes, well under the 1 KiB login body cap,
       and every valid token can be submitted.
     - **A token outside the rule** makes the token state invalid. That is `503`, with a console
       line at construction saying which rule failed (length or character set), never the token.
     - A generated token (64 hex characters) always satisfies the rule.
   - **Generated.** If it is not set, the token lives at `data_dir/standalone_token`, and the
     content must be exactly 64 lowercase hex characters (with one optional trailing newline).
     - **Creation is a small state machine with one rule: no process ever deletes, replaces or
       truncates a file another process may be using.** There is no sweep and no lock. Each
       construction runs these steps:
       1. **Read.** Read `data_dir/standalone_token` (the *final* file).
          - If it holds a valid token, use it. Done.
          - If it exists but is short or malformed, re-read every 50 ms for up to 2 s. This covers
            the fallback in step 5, where another worker may not have finished writing. A valid token
            within that time is used. Otherwise the result is `503`, with the console line below.
            The 2 s bound applies at construction only; see "Recovery" below.
          - If it does not exist, go to step 2.
       2. **Create own temporary file.** Create `standalone_token.tmp-<pid>-<16 random hex>` in the
          same directory, with `O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW` and mode `0600`. The name
          is unique to this process, and no other process touches it. `O_NOFOLLOW` is used as
          `getattr(os, "O_NOFOLLOW", 0)`, because Windows has no such flag.
       3. **Write.** Write `secrets.token_hex(32)` and a newline, `fsync` the file and close it.
       4. **Link.** Call `os.link(tmp, final)`. It is atomic and never replaces an existing file.
          - On success, `fsync` the directory and use the token just written. The final file is the
            same inode, complete before it appeared.
          - The directory `fsync` is **best-effort**. It is skipped where a directory cannot be
            opened (Windows), and any `OSError` from it is ignored (for example `EINVAL` on some FUSE
            and 9p bind mounts). It never turns a successful link into a token failure.
          - On **any** failure (`EEXIST`, `ENOENT` or anything else), go back to step 1 once. If a
            valid final file now exists, use it.
          - If none exists and the failure was not `EEXIST`, the filesystem may not support hard
            links (for example some Docker Desktop bind mounts). Go to step 5.
       5. **Fallback, only when linking is unsupported.**
          - Create the final file directly with `O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW`, mode
            `0600`. Write the token in one `write`, then `fsync` it.
          - If that create fails with `EEXIST`, another worker won; go to step 1, including its
            2 s re-read.
          - After the write, the same best-effort directory `fsync` as in step 4 runs.
          - **Assumption:** the fallback relies on `O_EXCL` being atomic on the mount. Local
            filesystems, and virtiofs and 9p (which pass it through to the host), provide this.
       6. **Clean up own file.** In a `finally`, unlink this process's own temporary file if it still
          exists. Only its own, never a pattern.
     - **Every interleaving converges.** Two or three workers starting together either read an
       existing valid file, or race to `link` or to the fallback's `O_EXCL`. Exactly one of them
       wins, because each is atomic, and every loser re-reads the winner's complete file. No step
       deletes or changes a file another worker uses, so no worker can be left without a source or a
       result. The fix map of PR #53 lists each interleaving and each crash point.
     - **Crashes.** A crash before the link leaves only an orphaned temporary file; the final file
       is absent, and the next start creates one. A crash after the link leaves a valid final file
       and an orphan.
       - **Orphans** are never read, hold an unused random token with mode `0600`, and are harmless.
         `README.md` says they may be deleted by hand while spotdown is stopped.
       - **The fallback can leave a bad final file.** A crash between the fallback's create and
         its write leaves an empty final file, and so does a `write` or `fsync` there that fails
         without a crash (for example `ENOSPC`). This can happen only on the fallback path, which
         runs when `link` fails with an error other than `EEXIST` and no final file exists. That is
         usually a filesystem without hard links, but a transient error on another filesystem
         counts too. The result is `503` with the console line.
     - **Named failure outcomes.** Each gives `503` and the console line naming the file:
       - the link failed with `EEXIST`, but the re-read finds no final file (deleted from outside);
       - the fallback's write or `fsync` failed;
       - after the 2 s re-read, the final file is empty, short or malformed;
       - the owner-supplied file is unreadable, or breaks the owner-token rule (length 32 to 512,
         printable ASCII without `"` or `\`).
     - **Recovery without a restart.** A worker with no valid token keeps answering `503`, but on
       later requests it re-reads the token file (generated or owner-supplied) **at most once a
       second**. It switches to the token as soon as the file holds a valid one. This is a re-read,
       never a regeneration and never a write. It also covers a fallback winner that stalls for
       more than 2 s between its create and its write.
     - **Never regenerated over an existing file.** A final file that is still empty, short or
       malformed after the re-read is not overwritten. The app answers `503` (the failure rule
       below) and prints one console line naming the file and saying "delete it and restart to
       generate a new token".
     - **Symlinks.**
       - The final file is read without `O_NOFOLLOW`. A symlink planted there makes the link
         `EEXIST`, and its target is almost certainly not 64 hex characters, so the result is `503`
         and fails closed. Anyone who can write `data_dir` could replace the token anyway.
       - The owner-supplied `standalone_access_token_file` follows symlinks on purpose: Docker and
         Kubernetes secrets are often symlinks.
   - **Console line.** At construction the app prints one line: the **path** of the token file and
     "open /login and paste it". It never prints, logs or returns the token itself.
   - **Mode bits are set, not checked.** On create the mode is `0600`. On read the mode is not
     enforced: a bind mount on Docker Desktop for Windows can show other bits. The token's secrecy
     rests on `data_dir` being the operator's own directory.
   - **No valid token means nothing is served.** An unreadable, empty, short or malformed token, or
     a failure to create one, does not stop construction. Instead the auth middleware answers every
     request except `GET /healthz` with `503` and a message naming the file. Nothing is served.
   - **Rotation** is deleting the file, or editing the owner-supplied one, and restarting. Every
     session then ends.
   - **Generating a token by hand:** `openssl rand -hex 32`. The token must be random.
   - **Reading the generated token.**
     - With compose it is `./data/standalone_token` on the host, or
       `docker compose exec api cat /app/data/standalone_token`.
     - On a Linux host the container runs as root, so the file on the bind mount is root-owned with
       mode `0600`. Read it with `sudo cat`, or with `docker compose exec`.
     - `README.md` documents both.
3. **Authentication runs in a pure ASGI middleware installed when the app is built.**
   - **At construction.** `create_standalone_app`:
     - calls `Settings.ensure_dirs` (moved from the lifespan to construction);
     - reads or creates the token;
     - installs the auth middleware.

     **Authentication** depends on nothing in the lifespan. The same holds for the module-level
     `app.main:app`, whatever server runs it and with or without `--lifespan off`. Other start-up
     work stays in the lifespan, notably `db.init_db()` and with it ADR 0001's cache rebuild. Under
     `--lifespan off` such routes can therefore error, but only **after** authentication: an
     unauthenticated request is refused either way.
   - **A pure ASGI middleware**, not Starlette's `BaseHTTPMiddleware`, which passes non-HTTP scopes
     through. It handles scope types like this:
     - `lifespan` passes through;
     - `http` is checked;
     - `websocket` is closed with code 1008 before `accept`;
     - any other scope type is refused.
   - **Order for an `http` scope**, outermost first:
     1. the `Host` allowlist (item 6);
     2. the token state: `503` if there is no valid token, except for `GET /healthz`, which is
        always served (item 2);
     3. the exemptions and the session (item 1);
     4. the unsafe-method checks (item 6);
     5. `X-Spotdown-UI` (item 6).
   - A settings error (item 5) raises during construction, so no app object exists at all.
   - **Tests** set a temporary `data_dir`, so no test writes `./data/standalone_token`.
4. **Login, logout and the session cookie.**
   - **`GET /login`** is one self-contained HTML response with inline CSS and JS and no other assets.
     It is served with:
     `Content-Security-Policy: default-src 'none'; style-src 'sha256-<css>'; script-src 'sha256-<js>';
     connect-src 'self'; form-action 'none'; frame-ancestors 'none'`.
   - **`POST /login`** runs these checks in this order. Each answers at once and stops:
     1. the `Host` allowlist, `403`;
     2. the token state, `503`;
     3. the lockout: while the rate limit is closed, `429` with `Retry-After`. The token is not
        evaluated, and the attempt does **not** count;
     4. a same-origin `Origin`, `403`;
     5. `X-Spotdown-UI: 1`, `403`;
     6. an exact `Content-Type: application/json`, `415`;
     7. a body of at most 1 KiB, checked against `Content-Length` before reading and enforced while
        reading, `413`;
     8. a JSON object with a string `token`, otherwise `400`;
     9. `hmac.compare_digest` on the **UTF-8 bytes** of the submitted and the stored token (never
        on `str`, which raises `TypeError` for non-ASCII input). A wrong token, including one over
        512 characters, gets `401` and no cookie, and counts as one failure. The right token sets
        the cookie and answers `204`.

     Only step 9 failures count towards the limit.
   - **Rate limit.** There is one in-process limit for the whole app, not per peer, so no address is
     trusted. After 5 counted failures within 60 s, the limit closes for the next 60 s. That lets
     anyone who can reach `/login` lock the owner out for a minute at a time. This is accepted for a
     single-user tool; brute force of a 32-byte token is infeasible anyway.
   - **`POST /logout`** needs a session and passes item 6's checks. It answers `204` and clears the
     cookie: same name, `Max-Age=0`, `Path=/` and, for the `__Host-` name, `Secure`. Its body is
     `{}` or empty, also capped at 1 KiB.
   - **The cookie.**
     - **Value:** `<expiry>.<mac>`, which must match `^[0-9]{1,12}\.[0-9a-f]{64}$`. `<mac>` is the
       hex `HMAC-SHA256(HMAC-SHA256(token, "spotdown-session-v1"), "<expiry>")`.
     - **Verification:** the MAC is compared with `hmac.compare_digest`, and the expiry must be in
       the future and at most 12 h ahead. Anything else gets `401`.
     - **Lifetime:** 12 hours, sent as `Max-Age=43200`, with no server state. Rotating the token
       ends every session.
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
   - **`ui_public_origin`** must be an exact `scheme://host[:port]` with scheme `http` or `https`,
     and nothing else: no userinfo, path (not even `/`), query or fragment. An explicit default
     port (`:80` with `http`, `:443` with `https`) also raises, because browsers omit it from
     `Origin`, so such a setting could never match.
6. **Defence in depth.** These no longer decide authentication. They limit what a browser page or a
   misconfiguration can do. Each has its own test.
   - **Same-origin unsafe requests.** Every unsafe method (every method except `GET`, `HEAD` and
     `OPTIONS`) needs two things before the body is read:
     - **`Origin`:** a present `Origin` that matches the expected origin, otherwise `403`.
       - **The one comparison rule**, which ADR 0002 also uses: both sides are split into scheme,
         host and port. The scheme and host are lowercased, and the three parts must be equal as
         strings. No default port is added or removed, so `http://localhost` and
         `http://localhost:80` differ. A missing `Origin` and `Origin: null` never match, and
         neither does an `Origin` with userinfo, a path, a query or a fragment.
       - **The expected origin** is `ui_public_origin` if set. Otherwise it is `http://` plus the
         request's `Host` (which has passed the `Host` allowlist). It is never taken from the scheme
         uvicorn sees or from forwarded headers.
     - **`Content-Type`:** exact, otherwise `415`. It is `application/json` (with optional
       `charset=utf-8`) for `/api/v1/convert`, `/login` and `/logout`, and
       `application/octet-stream` for #49's upload route. **Default deny:** every other unsafe
       request (a route with no listed type) gets `415`, so a later route cannot ship with a
       CORS-simple type by omission.
   - **`X-Spotdown-UI`.** Every request under `/api/` needs `X-Spotdown-UI: 1`, otherwise `403`.
     - A non-safelisted header cannot be sent cross-origin without a preflight, which never
       succeeds.
     - `<img>`, `<form>` and `no-cors` requests cannot set it.
     - It is preferred to `Sec-Fetch-Site` because not every browser sends that header.
   - **`Host` allowlist.** `ui_allowed_hosts` (default `localhost`, `127.0.0.1`, `[::1]`, any port)
     applies to every standalone route, the three exceptions included.
     - Parsing handles bracketed IPv6: `[::1]:8000` and `[::1]` are host `::1`.
     - An unlisted or unparsable `Host` gets `403`.
   - **No CORS at all.** Standalone mode installs no CORS middleware: the wildcard is removed and
     nothing replaces it. Under an always-on session and the exact `Origin` check, a CORS origin
     could neither send an unsafe request nor read an authenticated answer, so an allowlist setting
     would do nothing useful.
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
- **Keep the wildcard CORS for "dev convenience", or a CORS allowlist.** Rejected. The wildcard lets
  any page drive conversions and read results. An allowlist could do nothing under the session and
  the exact `Origin` check, so standalone mode has no CORS. A cross-origin editor preview is not
  supported; open the UI from the app itself.
- **Write the generated token with `O_EXCL` on the final path.** Rejected as the main path: the
  file exists empty before the token is written, so a racing worker can read a partial token, and a
  crash leaves an empty file. It survives only as the fallback for filesystems without hard links,
  with a bounded re-read.
- **Sweep leftover temporary files at construction.** Rejected: one worker's sweep can delete
  another's in-flight temporary file, whose link then fails (the PR #53 review at `5def3d3`). An age
  bound only narrows the window for a paused process. Each process removes only its own file.
- **An `flock` around create-or-read.** Not chosen: `fcntl` is POSIX-only, so Windows would need the
  link procedure anyway, which makes two procedures to get right instead of one. The link
  procedure alone is correct on every platform that has hard links.
- **Regenerate a malformed generated token automatically.** Rejected: it would silently overwrite a
  file the owner may have edited, and would hide a crash. `503` with a named fix is explicit.
- **`BaseHTTPMiddleware` for authentication.** Rejected: it passes WebSocket and other non-HTTP
  scopes through untouched.
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
- **No cross-origin use.** A cross-origin editor preview that called the API stops working; the UI is
  used from the app's own origin.
- **The API docs** (`/docs`, `/redoc`, `/openapi.json`) need a session too.
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
