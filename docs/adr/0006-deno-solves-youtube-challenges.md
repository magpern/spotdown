# ADR 0006: the worker runs deno to solve YouTube's player challenges

**Status:** Proposed (2026-11-25, spotdown#109).

## Context

From late 2025 YouTube serves plain-https audio formats only to clients that solve its player's
JavaScript challenges. yt-dlp solves them with an external JS runtime and the `yt-dlp-ejs` solver
scripts. Without them, v0.2.0's pinned yt-dlp 2025.10.14 and its forced `android`, `tv` and `web`
clients got no https format: the android formats need a PO token and the web client is
SABR-only. So every download failed as `provider_error` ("Requested format is not available"),
seen live on a v0.2.0 deployment.

## Decision

1. The backend image pins `yt-dlp`, `yt-dlp-ejs` and `deno` together in `requirements.txt`, at the
   versions yt-dlp pins for itself (its `default` and `pin-deno` extras). deno comes from its
   official Python redistribution (manylinux wheels for x86_64 and aarch64), so it is on `PATH`
   for the image's user on both release architectures, with no extra download step in the build.
2. `ytdlp_client` sets `js_runtimes={"deno": {}}` (deno only, even if another runtime is present)
   and `remote_components=[]`: the solver is the pinned package, never fetched at run time. It no
   longer forces `player_client` and uses yt-dlp's defaults, which track YouTube.
3. Every other hardening rule is unchanged: only the `youtube` extractors, https-only formats, no
   post-processors, no fixup, no hooks, quiet logger, metadata checks before any byte, size cap.

## Consequences

- A new program runs in the worker: deno executes YouTube's **player JavaScript**, through the
  `yt-dlp-ejs` scripts, as uid 10001 with a read-only root, no capabilities and `/tmp` as its only
  writable place. yt-dlp starts deno with its own restricted permissions. deno never sees the
  downloaded audio, so "nothing runs a program on the untrusted audio bytes outside the analysis
  sandbox" still holds.
- The image grows by the deno binary (about 100 MB uncompressed).
- Upkeep: a YouTube change usually means bumping all three pins together (README, "yt-dlp upkeep").
- Tests stay offline: they check the options and the pins, never a real solve.
