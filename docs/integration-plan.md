# spotdown as classbeat's music-analysis service (integration plan)

**Status: FROZEN, 2026-11-20. Every phase is PLANNED / NOT AUTHORIZED FOR IMPLEMENTATION** until
the owner authorizes it explicitly.

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
| Sources: `url` (host allowlist), `youtube` (yt-dlp, off by default) | optional |
| Matchers (YouTube first) | optional |
| Alignment to a reference clock ("spotify" is one kind) | optional |
| Standalone extras (UI, Spotify URL resolution, file downloads) | off in service mode |

**Terms of service:** downloading from YouTube is against YouTube's terms, and using it for
Spotify-catalogue classes touches Spotify's Developer Policy. Returning only metadata, or running
noncommercially, does not remove that risk. YouTube stays optional and off by default; enabling it
is the owner's decision
([classbeat#1296](https://github.com/magpern/classbeat/issues/1296)).

## Issues in this repository

| Phase | Issue | Depends on |
|---|---|---|
| planning | #12 this page | – |
| owner | #1 ruleset and required checks on `main` | #2 merged |
| P0 | #2 governance bootstrap (AGENTS.md, CLAUDE.md, CI, pr-title, agent roles) | the classbeat plan doc |
| P0 | #3 v2 contract and analysis schema v1 | #2; written together with classbeat#1300 (ADR 0040) |
| P1 | #4 service mode, service key, upload and url sources, sandboxed analysis | #2, #3 |
| P1 | #5 cache layers, both lookups, provenance, scope deletion | #4 |
| P2 | #6 quality harness and baseline gates (bounded) | #4, #5; the real-music runs need classbeat#1298 |
| P2+ | #7 labels, #8 phrases, #9 downbeats and meter (**never blocking**) | #6 |
| P3 | #10 YouTube matcher and source | #4, #5 |
| P3 | #11 alignment layer | #3, #5 |

The classbeat issues SD-P4 to SD-P7 (classbeat#1301 to #1306) depend on #3, #4, #5, #6, #10 and #11.

## Governance

This repository uses classbeat's workflow unchanged:

**Roles**
- Opus is the Architect and Orchestrator;
- Sonnet is the Developer;
- Haiku is the Explorer;
- **Opus reviews every PR.**

**Review gate**
- a fresh reviewer for the first review, the same reviewer resumed for later rounds;
- `Agent review (opus): PASS <exact head SHA>` recorded on the PR.

**Merge controls**
- green `ci` and `pr-title`;
- merge only with `gh pr merge <n> --squash --match-head-commit <sha>`;
- never `--auto` or `--admin`, never an approving review.

**Work rules**
- Conventional Commits;
- one issue per PR;
- tests never touch the network.

#2 writes these rules into the repository.
