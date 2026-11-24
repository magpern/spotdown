# spotdown as classbeat's music-analysis service (integration plan)

**Status: FROZEN, 2026-11-20. AUTHORIZED by the owner on 2026-11-20.** classbeat#1296 (YouTube
terms of service) was **accepted by the owner on 2026-11-20**, so SD-P6 is unblocked. The owner
decisions classbeat#1297 to #1299 and spotdown#1 are still the owner's. No deploy, no live-account test and no real provider call
(YouTube, Spotify, SwedeBeat, MOWL) by any agent or CI job; those stay the owner's.

**Merged state (2026-11-20).** P0, P1, P3 and P2+ are merged, and P2 is merged except #6's real-music
runs. Issues #2 to #5, #7 to #11 and #20 are closed: governance and the v2 contract (#2, #3), service
mode, v2 API, caches and scoped lookups (#4, #5), the YouTube matcher and source, faked in tests (#10),
the alignment layer (#11), section labels (#7), 8 and 16 bar phrases (#8), downbeats with meter (#9),
the external-corpus runner (#20, PR #28) and hardening (PR #23). #6 stays open only for the real-music runs (classbeat#1298); its
harness is merged. #29 hostile-input hardening (PR #32) and #30 global cache retention purge (90 days
unused or a version change, with an operator takedown; classbeat ADR 0040, amendment "#1296
accepted"; PR #34) are merged. Open spotdown work: #40 (lifetime of scoped entries that are never
deleted; option 1 decided 2026-11-24: the same 90-days-unused and version-change purge as global
analyses, with their job and idempotency records) and the follow-ups of the 2026-11-21 real-music trial, #46 to #49, #52 and #54 (below).
Merged so far: #43 (PR #50); #44 (PR #51: ADR 0001 and ADR 0002, Accepted); ADR 0003 (PR #53, for
#52); and #45 (PR #55, one label vocabulary and the v1 response version in the cache key). All the
ADRs are in `docs/adr/`. ADR 0005 (owner, 2026-11-24) splits spotdown into two images, a backend that is API only and a separate UI; the classbeat plan's layer table and its "never returns audio" line need a matching amendment from the owner. Build order: #54, #46, #47, #48, #52, #49, one PR at a time. classbeat#1296 accepted means YouTube sourcing is the owner's deployment choice:
`ALLOW_YTDLP_SOURCING` is on by default in code by the owner's direction (2026-11-24, spotdown#86; the classbeat plan needs the matching amendment) and `false` turns it off. Quality gates file `quality/quality_gates.json` is at v6 (v6 added the reported segments-per-truth-section metric, spotdown#46); the document version is 8.5 (core 8, mapping 5; mapping 5 added the chorus margin, spotdown#47; mapping 4 added the reported `content.explicit`
signal, spotdown#36)
and the core analyzer version is 8 (running version `8.5`: over-fine segments are merged before labelling, spotdown#46; steady-energy tracks are split by timbre novelty and a chorus needs an energy margin, spotdown#47). The quality thresholds are set by the amendment in classbeat
ADR 0040 (Accepted); see that ADR for the numbers.

The complete plan, with every decision, is in classbeat:
[docs/plans/spotdown-integration.md](https://github.com/magpern/classbeat/blob/main/docs/plans/spotdown-integration.md)
(roadmap Amendment 31; tracking issue [classbeat#1308](https://github.com/magpern/classbeat/issues/1308)).
This page is spotdown's summary. Where the two differ, the classbeat plan wins.

## spotdown's role

spotdown is an independent music-analysis service. Its core contract is **audio in → structured
analysis out**. It is not specific to classbeat.

**It owns:**
- source matching;
- temporary audio processing;
- derived music metadata.

**It never:**
- returns audio;
- receives a user token;
- writes to any account.

classbeat keeps Spotify user integration, music selection, workout generation and publishing. In
the integration, spotdown needs no Spotify API: classbeat sends the title, artists, duration and
ISRC.

**Layers:**

| Layer | Status |
|---|---|
| Core analyser (on the file's own clock) | required |
| Sources: `upload` | required |
| Sources: `url` (host allowlist), `youtube` (yt-dlp, on by default, owner 2026-11-24) | optional |
| Matchers (YouTube first) | optional |
| Alignment to a reference clock ("spotify" is one kind) | optional |
| Backend image `spotdown`: API only (`/v2` behind a caller key, `/healthz`), no mode | required (ADR 0005) |
| Spotify link resolution (`POST /v2/spotify/resolve`) and the mp3 (`POST`/`GET /v2/audio`, by owner direction) | optional (ADR 0005) |
| UI image `spotdown-ui`: the page, the token session (ADR 0003), calls the backend server-side with its own key | optional, its own container (owner, 2026-11-24, ADR 0005) |

**Terms of service:** downloading from YouTube is against YouTube's terms, and using it for
Spotify-catalogue classes touches Spotify's Developer Policy. Returning only metadata, or running
noncommercially, does not remove that risk. The owner accepted that risk on 2026-11-20
([classbeat#1296](https://github.com/magpern/classbeat/issues/1296)). YouTube stays optional; it is on
by default in code (`ALLOW_YTDLP_SOURCING`, owner, 2026-11-24, spotdown#86; classbeat amendment
pending) and `false` turns it off.

## Issues in this repository

| Phase | Issue | State | Depends on |
|---|---|---|---|
| planning | #12 this page | kept current | – |
| owner | #1 ruleset and required checks on `main` | owner | #2 merged |
| P0 | #2 governance bootstrap (AGENTS.md, CLAUDE.md, CI, pr-title, agent roles) | merged | the classbeat plan doc |
| P0 | #3 v2 contract and analysis schema v1 | merged | #2; written together with classbeat#1300 (ADR 0040) |
| P1 | #4 service mode, service key, upload and url sources, sandboxed analysis | merged | #2, #3 |
| P1 | #5 cache layers, both lookups, provenance, scope deletion | merged | #4 |
| P2 | #6 quality harness and baseline gates (bounded) | open only for the real-music runs | #4, #5; the real-music runs need classbeat#1298 |
| P2 | #20 external-corpus runner and baseline gates (PR #28) | merged | #6 |
| P2+ | #7 labels, #8 phrases, #9 downbeats and meter (**never blocking**) | merged | #6 |
| P3 | #10 YouTube matcher and source | merged | #4, #5 |
| P3 | #11 alignment layer | merged | #3, #5 |
| hardening | PR #23 deferred review findings of #16 to #21 | merged | – |
| hardening | #29 hostile-input hardening (PR #32) | merged | – |
| hardening | #30 global cache retention purge (PR #34) | merged | – (builds on #5, merged) |
| hardening | #40 scoped retention: 90 days unused or a version change, old job and idempotency records | open | #30 |
| trial follow-up | #43 docs points of the #42 review (PR #50) | merged | – |
| trial follow-up | #44 ADR 0001 (one label vocabulary, no pre-chorus in analysis-v1), ADR 0002 (local-file input in the standalone UI) (PR #51) | merged | – |
| trial follow-up | #45 one label vocabulary (retire the core analyser's older labels) (PR #55) | merged | #44 |
| trial follow-up | ADR 0003 standalone exposure (PR #53) | merged | – |
| trial follow-up | #54 ADR 0003 Accepted, lifespan and `Origin` wording, #52's assumptions | open | #45 |
| trial follow-up | #46 merge over-fine segments before labelling | open | #45 |
| trial follow-up | #47 labels and segments on steady-energy tracks | open | #46 |
| trial follow-up | #48 waveform view in the standalone UI | open | #45 |
| trial follow-up | #52 standalone exposure: a token always on every standalone route, plus same-origin requests, a `Host` allowlist, no CORS and loopback publishing (ADR 0003; Opus Developer) | open | #44, #54 |
| trial follow-up | #49 local-file input in the standalone UI (Opus Developer) | open | #44, #48, #52 |
| owner, 2026-11-24 | #68 the UI's own switch `SPOTDOWN_UI`, independent of service mode (ADR 0004; Opus Developer) | merged (PR #69), superseded by ADR 0005 | – |
| owner, 2026-11-24 | #75 ADR 0005: two images, a backend that is API only and a separate UI | open | – |
| owner, 2026-11-24 | #76 `/v2` Spotify link resolution and a second caller key (Opus Developer) | open | #75 |
| owner, 2026-11-24 | #77 `/v2` mp3 of a matched YouTube video (Opus Developer) | open | #75 |
| owner, 2026-11-24 | #78 the UI image `spotdown-ui` (Opus Developer) | open | #75, #76, #77 |
| owner, 2026-11-24 | #79 backend API only: retire `SERVICE_MODE` and the standalone app; deploy both images (Opus Developer) | open | #75 to #78 |

The classbeat issues SD-P4 to SD-P7 (classbeat#1301 to #1306) depend on #3, #4, #5, #6, #10 and #11;
all but the real-music runs of #6 are merged, and classbeat#1296 is accepted, so SD-P6 is unblocked.

## Governance

This repository uses classbeat's workflow unchanged:

**Roles**
- Opus is the Architect and Orchestrator;
- Sonnet is the Developer;
- Haiku is the Explorer;
- **Opus reviews every PR except a docs-only one** (owner, 2026-11-20); "docs-only" is defined only
  in AGENTS.md review gate item 2, and a PR that fails any of its conditions is reviewed by Opus;
- after a second `FAIL` on a PR, an Opus Developer does the next fix round, and every Developer runs
  a class-wide self-check before handing back.

**Review gate**
- a fresh reviewer for the first review, the same reviewer resumed for later rounds;
- `Agent review (opus): PASS <exact head SHA>` recorded on the PR; an `Agent review (sonnet): PASS
  <exact head SHA>` with `docs-only: yes` counts only for a PR that is docs-only by AGENTS.md review
  gate item 2.

**Merge controls**
- green `ci` and `pr-title`;
- merge only with `gh pr merge <n> --squash --match-head-commit <sha>`;
- never `--auto` or `--admin`, never an approving review.

**Work rules**
- Conventional Commits;
- one issue per PR;
- tests never touch the network.

#2 wrote these rules into the repository.
