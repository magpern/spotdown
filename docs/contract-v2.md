# spotdown API contract v2 (service mode)

**Status: DRAFT. Authorized by the owner on 2026-11-20** (except classbeat#1297 to #1299 and
spotdown#1; classbeat#1296 was accepted on 2026-11-20; no deploy or real provider call). Parts are built, see [integration-plan.md](integration-plan.md). It is written together with classbeat ADR 0040; where they differ, the classbeat plan
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
never logged. The standalone UI, its routes and downloads are not served in service mode. With
`SPOTDOWN_UI=on` (off unless set; [ADR 0004](adr/0004-ui-switch-independent-of-service-mode.md))
the service also serves a static page at `/` and a fixed list of its assets, without a key: the
page holds no data and calls `/v2` with the key the user types in (kept in memory only, sent as
this same bearer header). Every rule of this contract holds unchanged with it on.

## Common rules

- **`Idempotency-Key`** (header): required on `POST /v2/analyses` and `PUT /v2/uploads`, optional
  on `POST /v2/match`. The same key with the same body returns the first response; the same key
  with a different body is `409`. This holds for concurrent requests too: the one that records
  first wins, a concurrent copy of it gets the winner's response, and a concurrent different
  request gets `409` (an upload it claimed is released). One narrow window remains: a concurrent
  copy whose upload claim fails before the winner has recorded its response gets `404
  upload_not_found`; retrying with the same key then replays. Keys are remembered per scope for
  24 hours.
- **`cache_scope`** is the one name for the scope, in bodies, query strings and the `DELETE` path
  (`/v2/scopes/{scope}` takes a `cache_scope` value). It is an opaque string of 1 to 128 characters
  `[A-Za-z0-9_-]`. It is **required** for `upload` and `url` sources, for the sha256 lookup and for
  uploads (a missing one is `422 scope_required`), and **optional** for `youtube`-only requests and
  the source lookup of a global source: global (public) sources are shared across scopes, so
  omitting it only means no private source can be used. All lookups and writes are limited to the
  caller's scope; a lookup never crosses scopes, and a cross-scope miss never reveals that another
  scope holds the same bytes.
- **Errors.** Request errors are `4xx` with `{"error": {"code", "message"}}`, an unknown route or
  method included (`404 not_found`, `405 method_not_allowed`). A message never echoes input: a
  `422` names only the request's known fields, never an unknown one. Per-track outcomes are not
  HTTP errors; they are the statuses below.
- Times are seconds (float). Confidences are 0 to 1.

## Per-track statuses

Used in `/v2/match` results and in a job's per-track outcome.

| Status | Meaning | Extra fields |
|---|---|---|
| `not_found` | search worked, no candidate | – |
| `uncertain` | best candidate below the `confident` band; on a job, the decoded duration was more than 5 s from the matched one (no analysis is kept) | `best`, `alternates` (match only) |
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
    {"client_ref": "track-7", "title": "...", "artists": ["..."], "duration_s": 213.0, "isrc": "...",
     "explicit": true}
  ]
}
```

`isrc` is optional; `1 <= len(tracks) <= 50`. `explicit` (optional, a JSON boolean; anything else
is `422 invalid_request`) is the caller's explicit-lyrics flag for the track: it does not change the
search, the scores or the source-match cache, and is kept with the match record of the chosen video
as evidence for its analysis' `content.explicit` (see "Explicit content" below). Response `200`:
`{"results": [match-v1, ...]}` in request order, validated by `schema/match-v1.json`: status,
the best candidate with its recording-match confidence, band and duration check, up to 2
alternates. **Every candidate** (best and alternates) carries `cached_analysis_id` and a small
`cached_summary` (`analyzer_version`, `bpm`, `bpm_confidence`, `duration_s`, `has_beats`, and
optionally `has_downbeats`, `has_events`) when an analysis for it is already cached in the caller's
scope, and neither otherwise. spotdown always sends the two together. The schema requires
`cached_analysis_id` with a `cached_summary` but not the reverse, so a consumer treats an id
without a summary as a cached analysis to read (`GET /v2/analyses/{id}`), never as an error. This is free and is how a cached analysis is found without bytes. A
miss never starts an analysis.

Recording-match bands: `confident` >= 0.85, `uncertain` 0.6 to 0.85, below 0.6 `not_same`
(tuned on the evaluation set). Duration check against the requested duration: |delta| <= 2 s
`ok`, <= 5 s `tolerable`, above 5 s `rejected`.

The matcher is the `youtube` source's (spotdown#10):

- **Off by default.** Unless the operator sets `ALLOW_YTDLP_SOURCING=true`, `POST /v2/match` is
  `422 source_disabled`. `cache_scope` is optional; when given it only decides which cached
  analyses a candidate reports (a `youtube` analysis is global, so every scope sees it).
- **Signals** (`matcher_version` `youtube-1`): positive are an equal ISRC (when the source exposes
  one), a `- Topic` release channel or a verified artist channel (a channel name alone, VEVO
  included, is not a signal), and an equal normalised title and artist; negative are a version
  marker on one side only (live, remix, mix, edit, version, re-recorded, acoustic, unplugged, sped
  up, slowed, reverb, nightcore, daycore, cover, karaoke, instrumental, piano, extended, radio edit,
  VIP, rework, bootleg, lo-fi, 8D, bass boosted, mashup, demo; "original mix", "album version" and
  "remastered version" name the release itself and are not markers), a different ISRC, title words
  the request lacks, and the duration. A marker mismatch or a `rejected` duration caps the
  confidence below 0.6, so a wrong version is never `confident`; a different ISRC or extra title
  words cap it below 0.85; an upload with no channel signal and no equal ISRC is at most 0.8
  (`uncertain`).
- **Status:** `matched` when the best candidate is `confident`, `uncertain` when it is `uncertain`,
  `not_found` when there is no candidate or the best is `not_same` (no `best`, no alternates).
  Alternates are the next candidates that are not `not_same`, at most two. A search entry without
  an 11-character video id or a duration, or a live stream, is not a candidate (it cannot be checked
  before download).
- **Timing-approximate.** A match is evidence (the recording-match confidence and the duration
  checks), never a claim that the audio is identical to the caller's reference; the caller decides
  how far to trust it.
- **Source-match cache:** global, keyed on the normalised metadata fingerprint (the ISRC, or the
  normalised title, artists and a 5 s duration bucket) plus `matcher_version`. It holds the search
  entries' id, title, channel and duration only, never a scope, `client_ref` or URL; results are
  re-scored per request. Kept 3 days, a miss (no candidate) 6 hours.
- **Rate limits:** a provider rate limit starts a global backoff; until it ends, a search or a
  download that is not answered from a cache is `rate_limited` with the seconds left in
  `retry_after_s`, without calling the provider.
- A bot challenge, a 5xx or a network failure is `provider_error`; a removed, private,
  region-blocked or age-gated video is `source_unavailable`. yt-dlp's "Requested format is not
  available" is `provider_error`: with the https-only audio format it means the provider served
  no such format (an HLS- or SABR-only response after a client change), not that the video is gone.

### `PUT /v2/uploads`

Push audio bytes (`Content-Type: application/octet-stream`; query `cache_scope`). Returns `201`
`{"upload_id", "sha256", "bytes"}`. Uploads are temporary (deleted after the job that uses them or
after 1 hour) and no file name is kept. Callers first try the sha256 lookup below, so unchanged
audio is never uploaded twice.

Every upload is treated as hostile (spotdown#29). Only mp3 (MPEG Layer III), mp4/m4a (an MP4 brand
with an AAC track) and wav (PCM or float) are accepted, recognised by their bytes and then checked
whole; the extension and `Content-Type` are never trusted. A body over the byte cap (the operator's
`MAX_UPLOAD_BYTES`, 60 MiB by default, whatever the caller declares) is `413 too_large`. A file
that is empty, truncated, malformed, mixed with other content (anything after the audio but an
ID3v1 or APEv2 tag), outside 8000 to 192000 Hz or 1 to 8 channels, or whose tables declare more
than they hold, is `415 unsupported_media_type`. A refused upload leaves no file, no row and no
idempotency record. Tags and metadata are never read into any output. A file whose structure
already shows more than the decoded-length cap is accepted here and refused by its job
(`processing_failed`, `out_of_memory`) before it is decoded.

The job checks every source (upload, `url`, `youtube`) the same way in the analysis sandbox before
decoding, and decodes with a decoder pinned to that container (no format guessing): libsndfile for
wav and mp3, which must report the same container and stream; ffmpeg for mp4 with the `mov`
demuxer, the AAC decoder, local files only and no data references. A file refused there is
`processing_failed` with `decode_error`; a check and decode past the operator's decode deadline
(`ANALYSIS_MAX_DECODE_S`, 120 s by default) is `processing_failed` with `timeout`.

In an mp4, every track is checked, whatever its handler (a demuxer decides the stream type from
the sample entry): sound tracks carry only AAC (`mp4a` naming MPEG-4 AAC main, LC, SSR, LTP, HE or
HE v2, or MPEG-2 AAC), video tracks only a video entry, and any other track is refused.

Refused on purpose (a safe default; expect some real-library files among them):
- ALAC (Apple Lossless) m4a, mp3 inside mp4, QuickTime `.mov` and 3GP (by major brand), encrypted
  tracks, and m4a with a chapter (`text`) or timed-metadata track;
- AAC above 65535 Hz (the 16-bit `mp4a` rate field holds 0, as ffmpeg writes for 96 kHz);
- mp3 with anything between or after the frames other than an APEv2 and/or ID3v1 tag at the end
  (a Lyrics3v2 tag, junk, two files concatenated at another rate), a free-format bitrate, or an
  ID3v2 tag header whose size is not synchsafe;
- wav that is compressed (ADPCM and others), has more than 64 chunks, or has bytes after its RIFF
  chunk.

What the decoders still parse is accepted surface, inside the sandbox's rlimits and deadline:
libsndfile reads the wav chunks the check only bounds (`LIST`, `cue `, `smpl` and others), mpg123
reads the ID3v2 tag the check skips, and the probe's mutagen stream-info readers walk the mp3 frame
headers and the mp4 atom headers (including `udta`/`meta`, never their values).

### `POST /v2/analyses`

Start an analysis. Exactly one of `upload_id`, `source_ref` (`<kind>:<ref>`, for example
`youtube:<video id>`) or `url` (https, operator host allowlist).

```json
{
  "cache_scope": "<cache_scope>",
  "client_ref": "track-7",
  "upload_id": "...",
  "reference": {"kind": "spotify", "id": "...", "duration_s": 213.0},
  "explicit": false
}
```

`explicit` is optional (a JSON boolean, `422 invalid_request` otherwise): the caller's
explicit-lyrics flag, recorded as evidence in the new analysis' `content.explicit`. It never
changes whether or how a job runs.

`reference` is optional and requests an `alignment` object; `kind` is an open set and
`duration_s` is above 0 and at most 7200 (`422 invalid_request` otherwise).

**Alignment** (`alignment_version` `1`, spotdown#11). Two hypotheses are tried on the file-clock
analysis: `untrimmed` (the reference starts at the file's start, `offset_s` 0; the residual is
|file duration - reference duration|) and `trimmed` (the reference starts at the content's start,
its leading silence trimmed, `offset_s` = -content start; the residual is |content duration -
reference duration|). The smaller residual is chosen (`untrimmed` on a tie). A best residual
above 5 s, or no positive reference duration, is `alignment_failed`. The confidence is a first
estimate, calibrated by the quality harness: it falls linearly from 1 at a zero residual to 0 at
5 s and is reduced when the other hypothesis, at a different offset, misses by less than 1 s
more. For a `youtube` source chosen by a match, `recording_match_confidence` carries the chosen
candidate's score. spotdown reports this evidence only; it never emits a timing class.

A job with a `reference` ends on an **aligned analysis**: its own `analysis_id`, the file-clock
analysis unchanged plus the top-level `alignment` (`ok` only) and `provenance.alignment` (always,
with every hypothesis tried and the chosen one's `name` as `chosen`); on a failed alignment
`provenance.run.failure_kind` is `alignment_failed`. The file-clock analysis keeps its own id, is
what the lookups and `/v2/match` report, and is reused for every reference. The alignment cache is
keyed on the audio identity (sha256), the reference (kind, id, duration) and `alignment_version`,
in the analysis' scope (global for a `youtube` analysis, so the reference of a global alignment is
readable in every scope that knows its id), and is removed with its scope. A cache hit with a
`reference` aligns at once and answers `200` with the aligned analysis id.
`source_ref` is `youtube:<11-character video id>` (nothing else: no URL, no other kind, `422
invalid_request` otherwise); it is `422 source_disabled` unless `ALLOW_YTDLP_SOURCING=true`, and its
`cache_scope` is optional. The worker downloads that one video's audio (a single audio-only https
format; the duration and live-stream checks use the metadata before any byte; the size cap is
yt-dlp's `max_filesize`, plus the metadata size when known, checked again on the file; yt-dlp runs
no post-processor, fixup or hook, so ffmpeg only ever sees the bytes in the analysis sandbox),
analyses it, and stores the analysis **globally** with a
global source link, so every scope then gets it as a cache hit. After decoding, the measured file
and silence-trimmed content durations are checked against the requested duration of the match
that chose the video (or, without one, the video's metadata duration); above 5 s the job ends
`failed` with outcome `uncertain` and no analysis is kept. A cache hit
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

`analysis_id` is set when `status` is `done`. `outcome` is null on a `done` job except `alignment_failed` (with `failure_kind` `alignment_failed`). `GET /v2/analyses/{id}` returns the analysis
(`schema/analysis-v1.json`) for an id in the caller's scope or a global (public `youtube`) analysis; any other id is `404`. Jobs are visible only in the scope
that created them. Callers poll with a backoff; no callback is made.

A job that stays `running` longer than its lease (the queue's job timeout, the fetch and analysis
limits plus a minute, and 4 more minutes) lost its worker (killed, out of memory, redeployed): it is
failed as `processing_failed` with `failure_kind` `timeout` (so the caller may retry it once), its
URL and upload are removed and its temporary audio is deleted. Polling such a job reports it.

### Retention of global references

Global rows hold no caller, but two kinds hold a reference derived from the caller's catalogue
(classbeat ADR 0040 §7): the match record of a chosen video (the requested title, artists and
duration) and a global (`youtube`) alignment (the reference kind, id and duration). No scope
deletion removes them, so they have a bounded lifetime, the operator setting
`GLOBAL_REFERENCE_TTL_S` (default 3 days, the source-match cache's hit lifetime). An expired row is
never served and is purged (when one of its kind is next stored, and by the worker before each job).
An expired aligned analysis id is `404`; the same request then aligns again at once under a new id.
### Retention of global analyses

The global analyses themselves (a public `youtube` source), their source links, the global
alignments built on them and the match record inside them hold no user data, but no scope deletion
removes them, so they are purged too (classbeat ADR 0040 §7):

- **Unused for 90 days.** An analysis not read for `GLOBAL_ANALYSIS_TTL_S` (default 90 days, at
  least one day) is purged with its links and alignments. A read renews it: a source lookup or
  an analysis request that hits it, a content hit on its bytes, `GET /v2/analyses/{id}` of it or of
  an alignment built on it. The renewal is written at most once an hour. An entry that existed
  before this rule counts from its creation.
- **Analyzer version change.** An analysis of another `analyzer_version` than the running one can
  never be a hit again and is purged at the next sweep, however recently it was read.
- **Takedown.** The operator runs `python -m app.service.takedown youtube:<video id>` on the host
  (not an endpoint; nothing is reachable over HTTP). It removes every global analysis of that
  source (found by link and by the source in the document), the links to them, the alignments built
  on them and the match records of the video, in one transaction, and prints counts only
  (`alignments=… analyses=… links=… match_records=…`). It only accepts a global source reference,
  and a second run reports zeros.
- **Where it runs.** The purge runs in the worker's sweep before each job (the same path that purges
  expired match records and alignments) and is idempotent. Scoped data is never selected: a scope
  deletion never touches a global entry, and the purge never touches a scoped one.
- **Nothing left behind.** Audio is never kept. The purge overwrites the freed database pages
  (`secure_delete`) and truncates the write-ahead log, so a purged document is not left in the
  database files. A job row that ended on a purged analysis keeps its id and answers `404` when
  the analysis is fetched; the same request analyses again. Job rows (they hold the video id and
  the analysis id, not the analysis) and idempotency records are not part of a takedown or of this
  purge: those of a scope go with it, but a scope-less `youtube` job and the `/v2/match`
  idempotency records (scope `""`) persist. A lifetime for them is a known follow-up.
- **Backups** are outside this service: a takedown does not reach a copy of `service.db` made
  earlier, so the operator rotates those by their own schedule.

### Lookups

Both are limited to the caller's scope and return `{"schema_version": "lookup-v1", "analysis_id": "..."}` (`schema/lookup-v1.json`) or `404`:

- **By source reference:** `GET /v2/analyses/lookup?source=<kind>:<ref>&cache_scope=...`. For sources
  whose bytes the caller never holds, for example `youtube:<video id>`. A `url` source is looked
  up by its digest, `url:sha256:<hex>` (the stored ref, see `provenance.source.ref`; the caller
  computes it from the URL it submitted). **A signed URL is never sent in a lookup:** a query
  string travels in request lines and logs. spotdown still normalises a raw `url:<https URL>`, and
  drops every query string from its access log, but callers must not rely on that.
- **By content:** `GET /v2/analyses/lookup?sha256=<hex>&cache_scope=...`. For audio the caller holds.
  Run it **before** any upload. A cached analysis is reused only on an equal sha256 (or through the
  source-reference link to the same audio identity), never on the decoded-audio fingerprint alone.

### `DELETE /v2/scopes/{scope}`

Delete everything held for the scope: analyses, source-reference links, uploads, jobs, run records and
idempotency records. Idempotent: `204` whether or not the scope existed. Global (public) entries are not
touched (see "Retention of global analyses" for their own lifetime).

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
| `grid` | `meter` and `downbeats` (analyzer `6.3`: always `heuristic`, confidence at most 0.4, absent without an accent pattern, see below), `phrases` (8 and 16 bars; always `heuristic`, confidence at most 0.4, see below) | optional |
| `structure` | segments with boundary confidence, repetition groups | yes |
| `events` | build-ups, drops, breakdowns and transitions (hard stop, fade, tempo change), each with strength and confidence | yes |
| `labels` | intro, verse, chorus, bridge, outro, breakdown per labelled segment, each with a label confidence; always `heuristic` (analyzer `5.1`: present when the content has two or more segments, confidence at most 0.4, see below) | optional |
| `alignment` | reference (kind, id, duration), offset, residual, confidence, carried recording-match confidence | optional |
| `content` | `explicit`: `yes`, `no` or `unknown`, with confidence, method `metadata` and the evidence (first shipped as document version `6.4`, mapping version 4; see below) | optional |

**Labels (spotdown#7).** Rule-based, from the segment structure alone (energy, repetition group, position, detected drops): a chorus starts at a drop or belongs to the loudest repeating group when that group is clearly louder (at least 0.1 on the 0 to 1 energy scale) than the other middle segments; on a steady-energy track the single most repeated group is the chorus if it covers at most half the content time, otherwise there is no chorus unless a drop starts one (spotdown#47), the first and last other segments are intro and outro, a much quieter middle segment is a breakdown, a middle segment right after a chorus with another chorus to come is a bridge, the rest are verses (a build into a drop is a verse, the pre-chorus). Silence around the content is not labelled. The label gates (`label_accuracy`, `label_overconfidence`) pass on the synthetic corpus but are only regression checks there (the rules were written on it), so no label confidence exceeds 0.4; the cap is not raised until held-out evidence exists (spotdown#20: CC-licensed fixtures or the owner corpus). Treat every label as a hint. The schema is unchanged (`labels` was already optional and `heuristic`).

**Phrases (spotdown#8).** Between structural boundaries (the segment starts, which include the detected drops), the next boundary 8 bars later closes an 8-bar phrase, else one 16 bars later closes a 16-bar phrase; a section that is not a whole phrase long gives nothing, so `grid.phrases` is absent when the structure has no such sections. A bar is four beats of the estimated tempo: a phrase without a `meter` assumes 4/4, and a 3/4 meter claim yields no phrases (they were overconfident on the synthetic 3/4 fixtures); every phrase is a `heuristic`. A phrase's times are boundary times on the file's clock, snapped to a tracked bar start when one is within half a bar. Confidence is half the mean boundary confidence of its two ends, capped at 0.4; the cap is not raised until held-out evidence exists (spotdown#20). The phrase gates pass on the synthetic corpus but are regression checks there (see `docs/quality-harness.md`). **Phrases are unreliable after a pickup or when a boundary is missed**: on the synthetic pickup fixtures only 44 % of the true phrases are found, because the structure analysis misses the first boundary after a short pickup, and blind 8-bar tiling does as well on the other fixtures. Treat a phrase as a hint and its absence as no information. The schema is unchanged (`grid.phrases` was already optional).

**Meter and downbeats (spotdown#9).** `grid.meter.beats_per_bar` is 3 or 4, from the accent: the onset strength at the tracked beats differs by position in the bar more than chance explains (an adjusted explained-variance z-score of at least 3, and a margin over the other meter); with no such accent pattern neither `meter` nor `downbeats` is present, so the meter is never assumed to be 4/4. `grid.downbeats.times_s` steps one bar at a time from the start of the music and from each detected drop, snapped to a tracked beat, and restarts at the next drop. Both are `heuristic`: the confidence is how clearly the meter was decided, halved for a piece whose next anchor is not a whole number of bars away, and never above 0.4 (`meter_calc.CAP`; the cap is not raised until held-out evidence exists, spotdown#20). The schema holds one confidence for the downbeat list: it is the mean of the per-bar confidences. **Downbeats assume that the music and every drop start on a bar**: a track that starts on a pickup, or a drop that is not on a downbeat, shifts the bars of that piece without lowering the confidence much. On the synthetic corpus a forced 4/4 on the same grid scores as well as the estimate on 4/4 tracks (see `docs/quality-harness.md`); only 3/4 needs the estimate. The schema is unchanged (`grid.meter` and `grid.downbeats` were already optional).

**Explicit content (spotdown#36).** `content.explicit` says whether the recording has explicit
lyrics. **It is reported only: spotdown never blocks, filters, refuses or ranks anything on it; the
consumer decides.** In this phase it comes from metadata alone (method `metadata`, no audio model);
each piece of evidence is listed in `evidence` as `{"source", "value"}`:

- `caller`: the `explicit` flag of the `POST /v2/analyses` request (any source kind);
- `caller_match`: the `explicit` flag of the `POST /v2/match` item whose match chose the video
  (`youtube` only; the latest unexpired match that chose it wins, as for `source.match`, so a later
  match without a flag removes it);
- `youtube_age_limit`: the video's age restriction from its metadata, in years, when it is 18 or
  more. A lower age limit is no evidence either way and is not listed. An upload or a `url` file
  carries no evidence of its own.

The value is `unknown` with confidence 0 and no evidence when there is none, and never `no` without
evidence. Any `yes` evidence (a true flag or an age limit) makes it `yes`, even against a false flag:
a caution flag errs toward flagging, and the conflict shows in the evidence. The confidence is the
strongest weight on the winning side (a true flag 0.9, a false flag 0.8, an age limit 0.6; first
documented estimates, `explicit_calc.WEIGHTS`), at most 0.5 when the evidence conflicts. The schema
checks that the value agrees with the evidence. The evidence is the one known when the analysis was
made: a cache hit or a reuse of equal bytes returns the stored analysis unchanged, without the new
request's flag, so a caller that knows its own flag combines it itself. A global (`youtube`)
analysis is read by every scope, so its caller flags are visible to every scope; they name no
caller. Analyses made before mapping version 4 (document version `6.4`) have no `content` group: absent means not
reported, not `unknown` evidence.

Baseline fields are those the integration depends on; labels, phrases, downbeats and meter are
incremental enhancements and are never blocking. Until an enhancement passes its gate the field is
absent or `heuristic` with a low confidence. `alignment.offset_s` means
`reference_time = file_time + offset_s`. There are no `*_spotify` fields. The schema checks that the alignment
parts agree: an `ok` record comes with the top-level `alignment` and the reverse; a `failed` record
has no top-level `alignment` and `provenance.run.failure_kind` `alignment_failed`, which appears
only then.

**`provenance.source.ref`** never holds a file name or a signed URL: absent for `upload` (the sha256 identifies the audio), the video id for `youtube`, and for `url` `sha256:<hex digest>` of the normalised URL (`https://` plus the lower-case host, the path (`/` when empty) and `?query` when there is one; no userinfo, port or fragment), as lower-case hex. It is a digest because a signature can sit in the path as well as in the query, and because two URLs that differ only in their query may be different audio: each is its own source reference, so a cache hit by source needs the same URL, query included, and a `url` lookup gives the digest of that URL (`url:sha256:<hex>`, never the URL itself; the same bytes behind another URL are still reused through their sha256 after the fetch). The schema also accepts the older form (`https://` host and path only) that analyses stored before this rule carry; such an analysis is found by its sha256, not by a `url` lookup.

**Provenance of a global (`youtube`) analysis** names no caller, because every scope reads it:
`run.job_id` is an opaque run id (not the id of the job that produced it), `run.timings_s` is empty,
and no `cache_scope` or `client_ref` appears anywhere. `source.match` is the match record of the
latest `/v2/match` that chose this video (normalised query, candidates with scores and duration
deltas, choice, `matcher_version`): global public metadata, absent when the video was never
matched. `source.decoded_duration_check` (an optional field added to analysis-v1; a consumer
validating against an older copy of the schema rejects it) records the second duration check.
Within the match record's lifetime (see "Retention of global references" below) the latest match
that chose a video wins for `source.match` and for the expected duration; an expired record is not
used (the check then uses the video's metadata duration). A job that reuses a global analysis of
equal bytes under another video id checks the reused analysis' measured `signal` durations
against that id's expected duration; above 5 s it ends `failed` with outcome `uncertain` and no
link is made. The job record of the caller who started it stays in that caller's scope.

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
cached analyses but not the schema. `analyzer_version` is the core analyser's version, a dot, then
the document mapping's version, which goes up whenever the document builder emits something new
(mapping version 1 section labels, 2 phrases, 3 meter and downbeats, 4 the `content` group, 5 the chorus margin;
the running version is `8.5`: core version 7 merges over-fine segments before the groups and
labels are made, `app/core/merge_calc.py`, spotdown#46; core version 8 lets timbre and harmony
novelty alone place boundaries on a track whose energy barely varies (a lower peak threshold,
`novelty_calc.energy_contrast`), and mapping version 5 makes the chorus label need a clear energy
margin over the other middle segments (`label_calc.PEAK_MARGIN`), spotdown#47; the schema is
unchanged). Optional fields
added to analysis-v1 so far: `provenance.source.decoded_duration_check` (spotdown#18), the
alignment hypotheses' `name` and `chosen` (spotdown#21) and `content` (spotdown#36, document
version `6.4`, mapping version 4). A consumer that validates against an older copy of the schema rejects a document
carrying a newer optional field (the schema forbids unknown properties), so it updates its copy;
one that only reads the fields it knows is unaffected.

## Open points (for ADR 0040 to confirm)

The frozen plan fixes the endpoints, lookups, statuses, scopes and schema groups. This draft
chooses the following details, which the ADR must confirm or replace before the contract merges:
the request and response envelopes and field names above, the 50-track batch limit, the 24-hour
idempotency window, the 1-hour upload lifetime, and `retry_after_s` as the field name (the plan
writes `retry_after`).
