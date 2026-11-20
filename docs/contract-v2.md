# spotdown API contract v2 (service mode)

**Status: DRAFT, PLANNED / NOT AUTHORIZED FOR IMPLEMENTATION.** Nothing here changes the running
app. It is written together with classbeat ADR 0040; where they differ, the classbeat plan
([docs/plans/spotdown-integration.md](https://github.com/magpern/classbeat/blob/main/docs/plans/spotdown-integration.md),
§3 to §6) wins and this file is corrected. Summary of the plan: [integration-plan.md](integration-plan.md).

Machine-readable parts: [`schema/analysis-v1.json`](../schema/analysis-v1.json) and
[`schema/match-v1.json`](../schema/match-v1.json), [`schema/job-v1.json`](../schema/job-v1.json) and [`schema/lookup-v1.json`](../schema/lookup-v1.json), with examples in `schema/examples/`. CI
validates the examples against the schemas (`tests/test_schemas.py`).

## Principles

- **Audio in, analysis out.** spotdown never returns audio, never receives a user token and never
  writes to any account. Audio is deleted at the end of every job.
- **Generic.** The contract and schemas know no consumer, user, workout or catalogue. Callers pass
  an opaque `client_ref` and an opaque `cache_scope` (see Common rules).
- **File clock.** Every time in an analysis is seconds from the start of the decoded file. A
  reference clock appears only as the optional `alignment` object, which the caller applies.
- **Metadata only.** A response carries derived metadata, never audio or a signed URL.

## Authentication

Service mode is off unless the operator sets a service key. Every `/v2` request carries
`Authorization: Bearer <service key>`. A missing or wrong key is `401` with no detail. The key is
never logged. The standalone UI and downloads are not served in service mode.

## Common rules

- **`Idempotency-Key`** (header): required on `POST /v2/analyses` and `PUT /v2/uploads`, optional
  on `POST /v2/match`. The same key with the same body returns the first response; the same key
  with a different body is `409`. Keys are remembered per scope for 24 hours.
- **`cache_scope`** is the one name for the scope, in bodies, query strings and the `DELETE` path
  (`/v2/scopes/{scope}` takes a `cache_scope` value). It is an opaque string of 1 to 128 characters
  `[A-Za-z0-9_-]`. It is **required** for `upload` and `url` sources, for the sha256 lookup and for
  uploads (a missing one is `422 scope_required`), and **optional** for `youtube`-only requests and
  the source lookup of a global source: global (public) sources are shared across scopes, so
  omitting it only means no private source can be used. All lookups and writes are limited to the
  caller's scope; a lookup never crosses scopes, and a cross-scope miss never reveals that another
  scope holds the same bytes.
- **Errors.** Request errors are `4xx` with `{"error": {"code", "message"}}`. Per-track outcomes
  are not HTTP errors; they are the statuses below.
- Times are seconds (float). Confidences are 0 to 1.

## Per-track statuses

Used in `/v2/match` results and in a job's per-track outcome.

| Status | Meaning | Extra fields |
|---|---|---|
| `not_found` | search worked, no candidate | – |
| `uncertain` | best candidate below the `confident` band | `best`, `alternates` |
| `source_unavailable` | removed, region-blocked or age-gated | – |
| `provider_error` | bot challenge, 5xx or network failure at the provider | – |
| `rate_limited` | provider or service limit hit | `retry_after_s` (required) |
| `processing_failed` | decode error, timeout or out of memory; deterministic, so not retried on the same audio (one retry after a timeout) | `failure_kind`: `decode_error`, `timeout`, `out_of_memory` or `analysis_error`. No analysis exists; the run record is read from the job. |
| `alignment_failed` | analysed, but the timeline could not be mapped to the reference | The file-clock analysis **is** produced and cached: the job is `done` with an `analysis_id`, `outcome` `alignment_failed` and `failure_kind` `alignment_failed`. The analysis has no top-level `alignment`; `provenance.alignment` has `result: failed` with the reference and the hypotheses tried. |

`/v2/match` returns the first five plus `matched`; `processing_failed` and `alignment_failed` appear
only on jobs. The caller's reaction to each status (spare, alternate, replacement, fallback) is the
caller's policy and not part of this contract. A `rate_limited` response is not an attempt: the
caller retries at `retry_after_s`.

## Endpoints

### `POST /v2/match`

Match metadata to source candidates for a batch of tracks. **No download, no analysis.** Free.

Request:

```json
{
  "cache_scope": "<cache_scope>",
  "tracks": [
    {"client_ref": "track-7", "title": "...", "artists": ["..."], "duration_s": 213.0, "isrc": "..."}
  ]
}
```

`isrc` is optional; `1 <= len(tracks) <= 50`. Response `200`:
`{"results": [match-v1, ...]}` in request order, validated by `schema/match-v1.json`: status,
the best candidate with its recording-match confidence, band and duration check, up to 2
alternates. **Every candidate** (best and alternates) carries `cached_analysis_id` and a small
`cached_summary` (`analyzer_version`, `bpm`, `bpm_confidence`, `duration_s`, `has_beats`, and
optionally `has_downbeats`, `has_events`) when an analysis for it is already cached in the caller's
scope, and neither otherwise. This is free and is how a cached analysis is found without bytes. A
miss never starts an analysis.

Recording-match bands: `confident` >= 0.85, `uncertain` 0.6 to 0.85, below 0.6 `not_same`
(tuned on the evaluation set). Duration check against the requested duration: |delta| <= 2 s
`ok`, <= 5 s `tolerable`, above 5 s `rejected`.

### `PUT /v2/uploads`

Push audio bytes (`Content-Type: application/octet-stream`; query `cache_scope`). Returns `201`
`{"upload_id", "sha256", "bytes"}`. Uploads are temporary (deleted after the job that uses them or
after 1 hour) and no file name is kept. Callers first try the sha256 lookup below, so unchanged
audio is never uploaded twice.

### `POST /v2/analyses`

Start an analysis. Exactly one of `upload_id`, `source_ref` (`<kind>:<ref>`, for example
`youtube:<video id>`) or `url` (https, operator host allowlist).

```json
{
  "cache_scope": "<cache_scope>",
  "client_ref": "track-7",
  "upload_id": "...",
  "reference": {"kind": "spotify", "id": "...", "duration_s": 213.0}
}
```

`reference` is optional and requests an `alignment` object; `kind` is an open set. A cache hit
(equal sha256 and `analyzer_version` **in the caller's scope**, or the same source reference in that scope; a global `youtube` analysis is reused in any scope) returns `200`
`{"job_id", "status": "done", "analysis_id"}` at once. Otherwise `202` `{"job_id", "status":
"queued"}`. A source that is disabled in this deployment is `422 source_disabled`.

### `GET /v2/jobs/{id}`

Poll a job. Response `200`, validated by `schema/job-v1.json`:

```json
{"schema_version": "job-v1", "job_id": "...", "status": "queued|running|done|failed", "client_ref": "...",
 "outcome": "<per-track status or null while running>", "retry_after_s": null,
 "analysis_id": "...", "failure_kind": null}
```

`analysis_id` is set when `status` is `done`. `GET /v2/analyses/{id}` returns the analysis
(`schema/analysis-v1.json`) for an id in the caller's scope or a global (public `youtube`) analysis; any other id is `404`. Jobs are visible only in the scope
that created them. Callers poll with a backoff; no callback is made.

### Lookups

Both are limited to the caller's scope and return `{"schema_version": "lookup-v1", "analysis_id": "..."}` (`schema/lookup-v1.json`) or `404`:

- **By source reference:** `GET /v2/analyses/lookup?source=<kind>:<ref>&cache_scope=...`. For sources
  whose bytes the caller never holds, for example `youtube:<video id>`.
- **By content:** `GET /v2/analyses/lookup?sha256=<hex>&cache_scope=...`. For audio the caller holds.
  Run it **before** any upload. A cached analysis is reused only on an equal sha256 (or through the
  source-reference link to the same audio identity), never on the decoded-audio fingerprint alone.

### `DELETE /v2/scopes/{scope}`

Delete everything held for the scope: analyses, source-reference links, uploads, jobs, run records and
idempotency records. Idempotent: `204` whether or not the scope existed. Global (public) entries are not
touched.

## Schemas

### analysis-v1

A source-neutral analysis on the file's own clock. Every feature carries `confidence` and `method`:
`measured` (signal-derived and checkable), `estimated` (inference on measured data) or `heuristic`
(rule-based label). Consumers validate with `measured` and `estimated` features above a threshold
and treat `heuristic` ones as hints.

| Group | Fields | Required |
|---|---|---|
| `provenance` | analyzer (name, `analyzer_version`, component versions, config hash), audio identity (sha256, decoded-audio fingerprint, codec, bitrate, sample rate, channels, file and content durations), source (kind, ref, match record with `matcher_version`), alignment record (reference, result `ok` or `failed`, the hypotheses tried, chosen offset, residual, confidence, `alignment_version`, anchor agreement), run (job id, timings, status, failure kind), `created_at` | yes |
| `signal` | duration, content start and end (one `confidence` and `method` for the group), silence regions and fades (each with its own), energy curves (full, percussive, low band) at 1 s | yes |
| `tempo` | BPM, half and double candidates, stability, change points | yes |
| `grid` | `beats` | yes |
| `grid` | `meter` (estimated or heuristic, never assumed 4/4), `downbeats`, `phrases` (8 and 16 bars) | optional |
| `structure` | segments with boundary confidence, repetition groups | yes |
| `events` | build-ups, drops, breakdowns and transitions (hard stop, fade, tempo change), each with strength and confidence | yes |
| `labels` | intro, verse, chorus, bridge, outro, breakdown per segment; always `heuristic` | optional |
| `alignment` | reference (kind, id, duration), offset, residual, confidence, carried recording-match confidence | optional |

Baseline fields are those the integration depends on; labels, phrases, downbeats and meter are
incremental enhancements and are never blocking. Until an enhancement passes its gate the field is
absent or `heuristic` with a low confidence. `alignment.offset_s` means
`reference_time = file_time + offset_s`. There are no `*_spotify` fields.

**`provenance.source.ref`** never holds a file name or a signed URL: absent for `upload` (the sha256 identifies the audio), the video id for `youtube`, and for `url` a normalised https URL with host and path only (no userinfo, query or fragment) or `sha256:<hex digest of it>`.

**Provenance** is kept after the audio is deleted, so a decision can be reproduced, caches
invalidated, analyser versions compared and alignment problems diagnosed. It never holds audio,
user tokens, signed URLs or upload file names.

### match-v1

One track's match result (see `POST /v2/match`): `status`, `best` and up to 2 `alternates` (each
with `source`, `recording_match_confidence`, `band`, `duration_s`, `duration_delta_s`,
`duration_check`, and optional `cached_analysis_id` and `cached_summary`; the summary only with the id, no audio or names) and `matcher_version`. `band` must agree with `recording_match_confidence`; `retry_after_s` appears only for `rate_limited` and `best` only for `matched` and `uncertain`.

### Versioning

`schema_version` is `analysis-v1`, `match-v1`, `job-v1` or `lookup-v1`. A change that adds an optional field keeps v1; any
other change is a new version served beside the old one. `analyzer_version` changes invalidate
cached analyses but not the schema.

## Open points (for ADR 0040 to confirm)

The frozen plan fixes the endpoints, lookups, statuses, scopes and schema groups. This draft
chooses the following details, which the ADR must confirm or replace before the contract merges:
the request and response envelopes and field names above, the 50-track batch limit, the 24-hour
idempotency window, the 1-hour upload lifetime, and `retry_after_s` as the field name (the plan
writes `retry_after`).
