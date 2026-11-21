# ADR 0002: local-file input is in the standalone UI, through the hostile-input checks

**Status:** Accepted (2026-11-21), after the Opus review of PR #51 passed at `dbb0b8b`
([`Agent review (opus): PASS`](https://github.com/magpern/spotdown/pull/51#issuecomment-7064226062)). The same follow-up PR amended it for that review's
non-blocking findings:
- N5: upload results are not readable through `GET /api/v1/analyze`;
- N6: a deadline on reading the body;
- N7: what the `Origin` check compares, a TLS proxy, and the checks stop browsers only;
- N8: the route is POSIX-only.

ADR 0003 hardens the rest of the standalone app (spotdown#52).

**Issue:** spotdown#44. It is built in spotdown#49 (Opus Developer), and its waveform part in
spotdown#48.

## Context

The owner wants to analyse a local audio file from the UI. The trial note
(`docs/quality-trial-2026-11-21.md`, follow-up 4) words this as "accept a local file in service mode".
Today the standalone UI (#38) takes only a Spotify URL.

Constraints:

- **The UI is off in service mode.** The frozen plan's layer table (`docs/integration-plan.md`) puts
  the UI and every standalone extra off in service mode. `app/standalone.py` registers none of its
  routes there.
- **Service mode's callers authenticate with a service key**, and that key must never reach a
  browser.
- **Service mode already accepts uploads** through `PUT /v2/uploads`. Every upload there is treated
  as hostile (spotdown#29, PR #32; `docs/contract-v2.md` `PUT /v2/uploads`):
  - the container is sniffed by its content, and only mp3, mp4/m4a and wav pass; the extension and
    the declared type are ignored;
  - `max_upload_bytes` (60 MiB) is enforced while the body streams in;
  - the decoded length is capped (`analysis_max_decoded_s`, 1200 s);
  - the decoders are pinned;
  - decoding and analysis run in the sandboxed child (`app/service/sandbox.py` `run_analysis`),
    with its rlimits and deadlines;
  - uploads are temporary (`upload_ttl_s`, 1 hour) and keep no file name.
- **That machinery is tied to the service `Store`.** `receive_upload` (`app/service/uploads.py`)
  also needs a `cache_scope`, an `Idempotency-Key` and the service `Store`, and its expiry sweep runs
  through `store.stale_uploads`. `Settings.ensure_dirs` creates `uploads_dir` only in service mode.
  None of this exists in standalone mode.
- **The standalone app is open to any web page.** It has no caller authentication, it installs
  `CORSMiddleware(allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])`
  (`app/main.py`), and the image listens on `0.0.0.0` (`Dockerfile`). Any page open in the user's
  browser can send requests to it and read the answers, including through DNS rebinding.
  "A local or trusted network" is no defence against the browser as the attack path.
- **The standalone app runs one uvicorn process** (`Dockerfile` `CMD`), but nothing stops an
  operator from running more.

## Decision

1. **The standalone UI accepts the local file**, in standalone mode only. The new route (for example
   `POST /api/v1/analyze-upload`) is registered only when the standalone UI is served (`serve_ui`),
   and never in service mode. The UI is not served in service mode.
2. **The browser cannot reach the route from another site.** The route is exempt from the CORS
   middleware and makes three request checks. The checks run before the slot of item 3 is taken and
   before any byte of the body is read. A failed check answers `415` for the content type and `403`
   otherwise, and nothing is written.
   - **Outside the wildcard CORS.** The CORS middleware does not apply to the route's path: a
     preflight for it gets no `Access-Control-Allow-*` headers, and its answers carry none. A
     cross-site page can therefore neither send the request (the preflight fails) nor read a
     response.
   - **A non-simple content type.** `Content-Type` must be exactly `application/octet-stream`.
     Every other type is `415`, including the CORS-simple `text/plain`,
     `application/x-www-form-urlencoded` and `multipart/form-data`. A cross-site form post or a
     simple `fetch` therefore cannot reach the decoder.
   - **A same-origin `Origin`.** `Origin` must be present and must equal the expected origin
     exactly: scheme, host and port, compared as strings after lowercasing the scheme and host.
     - **Expected origin:** the scheme uvicorn sees, plus the request's `Host` header (which must
       pass the next check).
     - **Behind a TLS-terminating proxy:** the scheme uvicorn sees is `http` while the browser's
       `Origin` is `https`. The operator then either runs uvicorn with `--proxy-headers` and
       `--forwarded-allow-ips` set to the proxy's address only, or sets `ui_public_origin` (one exact
       origin, for example `https://spotdown.example`). When `ui_public_origin` is set, it is the
       only origin accepted.
     - A missing `Origin` is rejected: browsers send it on every `POST`, and this route is for the
       UI only.
   - **An expected `Host`** (against DNS rebinding). `Host` must be in a new setting
     `ui_allowed_hosts`, which defaults to `localhost`, `127.0.0.1` and `[::1]` (any port). An
     operator who opens the UI under another name adds it there.
3. **One slot, covering upload and analysis together.**
   - At most **one** local-file request is in flight at a time, from the first body byte to the
     deletion of its file. That is at most one upload in transit and at most one analysis running.
   - The slot is an exclusive, non-blocking lock (`fcntl.flock`) on a lock file in the standalone
     upload directory. It holds across processes, and it is taken before the body is read.
   - A request that cannot take the lock gets `429` with `Retry-After`, and nothing of its body is
     read.
   - A declared `Content-Length` over `max_upload_bytes` is `413` before the body is read. A body
     that grows past the cap while streaming is cut off with `413`.
   - **Read deadline.** Reading the body has a total deadline, a new setting
     `standalone_upload_read_s`.
     - The default is 150 s. The minimum supported upload rate is therefore 60 MiB per 150 s, about
       0.4 MiB/s (3.4 Mbit/s). A slower client can send a smaller file.
     - When the deadline passes, the answer is `408`, so a client that trickles bytes cannot hold the
       slot. The `408` leaves through the same `finally` as every other exit (cleanup below).
     - The sandbox's own deadlines bound the analysis that follows.
   - **POSIX only.** The slot needs `fcntl`, and the sandbox it feeds already needs `resource`.
     Both exist only on Linux and macOS. The slot module imports `fcntl` inside a guard; without it,
     the route is not registered and the UI hides the file input. There is no unsafe fallback lock.
     The test suite still imports on Windows: tests that need the route skip there
     (`pytest.importorskip("fcntl")`). The container is Linux.
   - Disk in use by the route is therefore bounded: at most one partial or whole upload of at most
     `max_upload_bytes` (60 MiB), plus the sandbox's work directory for that one run, where each
     file the child writes is capped by its `RLIMIT_FSIZE`.
4. **One hostile-input module, two callers.**
   - #49 moves the store-free steps out of `app/service/uploads.py` into a shared module (for
     example `app/service/hostile_input.py`): streaming to a given path with the byte cap and a
     sha256, `media_policy.sniff`, and `container_io.inspect_file` (the whole-file inspection, which also checks the decoded length).
   - `receive_upload` keeps its `Store`, `cache_scope` and `Idempotency-Key` logic and calls the
     shared module. The standalone route calls the same module and then `sandbox.run_analysis`
     directly. No check is copied; each one has one implementation.
   - The standalone route has no `Store`:
     - **Directory:** its own `data_dir/standalone_uploads`, which `Settings.ensure_dirs` creates
       when the standalone UI is served. The sandbox's work directory for the run is a subdirectory
       of it.
     - **Names:** random and server-chosen (`up_` prefix).
     - **Cleanup:** every exit of the request goes through one `finally`: success, failure,
       rejection, the read deadline's `408`, and a client disconnect.
       - In that `finally`, the partial or whole file and the work directory are deleted **before**
         the lock is released. So the next holder's leftover sweep never races the deletion.
       - Crashes are covered too. Whenever the slot is taken (and at startup), anything left in the
         directory other than the lock file is deleted, files and work directories alike: with one
         slot, anything present while nobody holds the lock is a leftover.
       - The service's `upload_ttl_s` sweep is not needed there.
     - **No cache scope and no idempotency key:** a repeat of the same bytes is a cache hit instead.
       The result is cached in the standalone `analyses` table under `upl_<sha256>`, keyed by the
       version pair of ADR 0001. A file name never takes part.
     - **Upload results are read only through the protected route.** `GET /api/v1/analyze/{track_id}`
       refuses any `upl_` id with `404`, without reading the cache, so no other site can probe
       whether this install analysed given bytes. A repeat upload of the same bytes is the only way
       back to a cached upload result, and it passes every check of Decision 2.
5. **Nothing is kept beyond the analysis.** No file name is kept, logged or returned, and tags are
   never read into the output. No audio is returned. Only the analysis result, under the sha256 key,
   outlives the request.
6. **The waveform is drawn in the browser** from the file the user picked (Web Audio). The server
   never sends that audio back.
7. **Security-critical.** It is a new caller-facing input path without authentication, so spotdown#49
   has an Opus Developer and an Opus Reviewer.

## Alternatives

- **Serve the UI in service mode and let it call `PUT /v2/uploads`.** Rejected. The browser would
  need the service key, or a new browser-facing caller-authentication design, and the frozen plan
  would have to change, because it keeps the UI off in service mode. The owner's need is met without
  it: the service's own upload checks guard the standalone path. If the owner later wants the UI on a
  service deployment, that is a plan change for the owner.
- **Analyse the file entirely in the browser.** Rejected: the analyser is Python (librosa, numpy),
  and a second implementation would drift from the one classbeat uses.
- **Upload to the standalone app without the hostile-input checks** (`librosa.load` as the standalone
  v1 path does). Rejected: arbitrary user files reach the decoder unsandboxed, the exposure that
  spotdown#29 closed for service mode.
- **Rely on CORS alone, or on "local network only".** Rejected: the wildcard CORS allows every
  origin, and a simple request is sent before any CORS check. The binding to `0.0.0.0` and DNS
  rebinding make "local" no boundary.
- **Narrow the CORS of the whole standalone app.** That is out of scope here: the existing routes'
  exposure predates this ADR, and this route does not widen it. The new route simply does not rely
  on CORS.
- **A bounded queue of several uploads.** Rejected for now: it multiplies the disk and CPU an
  unauthenticated caller can hold, for a single-user test tool. Raising the slot count later is a
  reversible change.
- **A CSRF token in the page.** Not chosen: the `Origin` and `Host` checks with a non-simple
  content type already stop cross-site requests, without session state.

## Consequences

- The owner can test any local file in the standalone UI, with the same input checks as service-mode
  uploads.
- The route cannot be driven from another site: no cross-site form or `fetch` reaches the decoder,
  and a rebound host name is refused.
- The `Origin`, `Host` and content-type checks stop **browsers** only. A non-browser caller that can
  reach the port can forge all three. Such a caller is limited only by these:
  - the single slot;
  - the read deadline;
  - the byte and decoded-length caps;
  - the sandbox;
  - ADR 0003's network defaults: loopback publishing, and a token whenever the app is reachable
    beyond loopback.
- Local-file input is unavailable on Windows (outside a container), as the sandbox already is.
- Only one local file is processed at a time per installation. A second tab gets `429` until the
  first finishes.
- Opening the UI under a host name other than `localhost`, `127.0.0.1` or `[::1]` needs that name in
  `ui_allowed_hosts`.
- The service-mode contract and the service's surface are unchanged. classbeat is unaffected.
- The upload checks gain a second caller, through one shared module. The tests of spotdown#49 assert
  that each check guards both routes.
- The rest of the standalone app keeps its wildcard CORS and its lack of authentication until
  spotdown#52 (ADR 0003) hardens it. #52 is built before #49.
