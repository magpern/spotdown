# ADR 0002: local-file input is in the standalone UI, through the hostile-input checks

**Status:** Proposed (2026-11-21). It becomes Accepted when its Opus review passes.

**Issue:** spotdown#44. It is built in spotdown#49, and its waveform part in spotdown#48.

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
  - decoding and analysis run in the sandboxed child, with its rlimits and deadlines;
  - uploads are temporary (`upload_ttl_s`, 1 hour) and keep no file name.
- **The standalone app has no caller authentication.** A new upload route there is an unauthenticated
  input path for hostile files.

## Decision

1. **The standalone UI accepts the local file**, in standalone mode only. The new route is registered
   only when the standalone UI is served (`serve_ui`), and never in service mode. The UI is not
   served in service mode.
2. **It reuses the hostile-input path, without copying it.** Every check listed under Context runs
   unchanged, through the same modules as `PUT /v2/uploads`. The errors are the existing typed ones.
3. **Nothing is kept beyond the analysis.**
   - The file is streamed to the upload directory under a random name, and deleted when its analysis
     ends (success, failure or rejection) and by the expiry sweep.
   - No file name is kept, logged or returned, and tags are never read into the output.
   - No audio is returned.
   - Any result cache is keyed by the sha256 of the bytes, never by a name.
4. **Concurrency is bounded,** for example to one running local-file analysis per process, with `429`
   beyond it. An unauthenticated route must not let one browser exhaust the host.
5. **The waveform is drawn in the browser** from the file the user picked (Web Audio). The server
   never sends that audio back.
6. **Security-critical.** It is a new caller-facing input path without authentication, so spotdown#49
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

## Consequences

- The owner can test any local file in the standalone UI, with the same safety as service-mode
  uploads.
- The service-mode contract and the service's surface are unchanged. classbeat is unaffected.
- The upload checks gain a second caller. A change to them must keep both routes covered, and the
  tests of spotdown#49 assert that each check also guards the standalone route.
- The standalone app stays unauthenticated. It is meant for a local or trusted network, as it is
  today. The concurrency bound and the size and length caps limit what an untrusted caller can do.
