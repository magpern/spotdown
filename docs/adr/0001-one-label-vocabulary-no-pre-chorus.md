# ADR 0001: one label vocabulary; no pre-chorus label in analysis-v1

**Status:** Proposed (2026-11-21). It becomes Accepted when its Opus review passes. The Architect
then sets it to Accepted, citing the PASS comment, in a PR of its own right after this one merges.
That PR also marks #44 merged in the issue table of `docs/integration-plan.md`, and #45 starts after
it.

**Issue:** spotdown#44. It is built in spotdown#45 (one vocabulary and the v1 cache key) and
spotdown#48 (the waveform, which shades the existing `build_up` events). The numbers are spotdown's
own; classbeat ADRs (for example classbeat ADR 0040) keep theirs.

## Context

The owner's real-music trial (`docs/quality-trial-2026-11-21.md`) found two label problems:

- **Two vocabularies.** spotdown has two labellers. The core analyser still labels every segment
  (`app/core/signal_calc.py` `segment_label`: `intro`, `outro`, `chorus/drop`, `breakdown/quiet`,
  `verse`). The structure document's labeller (`app/core/label_calc.py`) is the one that the
  analysis-v1 `labels` and the standalone UI use. Its labels are `intro`, `verse`, `chorus`, `bridge`,
  `outro` and `breakdown`, the `label` enum of `schema/analysis-v1.json`. The older labels reach only
  the standalone v1 response (`GET /api/v1/analyze/{track_id}`, `segments[].label`), never an
  analysis-v1 document. Two vocabularies for one segment is confusing, and a tool may read the wrong
  one.
- **No pre-chorus.** The owner hears verse, pre-chorus, chorus on one track. spotdown has no
  pre-chorus label: `label_calc` calls a build into a chorus a `verse`, and so does the synthetic
  corpus's annotation (`app/quality/specs.py`).

Constraints:

- **Adding a value to the `label` enum is a breaking change.** `docs/contract-v2.md` "Versioning"
  keeps analysis-v1 only for an added optional field. Any other change is a new schema version
  served beside the old one. classbeat validates against its copy of the schema, which forbids
  unknown values, so it would also need an update.
- **Labels carry little weight.** They are heuristic, and every confidence is capped at 0.4 until
  held-out evidence exists (spotdown#20). The label gates pass on the synthetic corpus, but they are
  only regression checks there, because the rules were written on it.
- **The pre-chorus evidence is thin:** one non-expert observation on one track. It is not ground
  truth (the trial note says so).
- **The standalone v1 response is cached whole.** The JSON is keyed by `(track_id,
  analyzer_version)` with the core `ANALYZER_VERSION` (`app/standalone.py`
  `analyze_track_endpoint`, `app/db.py` `get_analysis` and `set_analysis`). A cached response keeps
  whatever `segments[].label` it was built with. Some cached responses predate `document`
  (`app/analyze.py`), so they cannot be relabelled from it. The core version cannot be bumped for a
  v1-only change, because the v2 caches are keyed by it too.
- **`build_up` events exist only before a detected drop.** `events_calc.build_ups`
  (`app/core/events_calc.py`) walks back from each detected drop over 3 to 16 s of non-falling
  energy. A chorus that is not at a detected drop (the usual pop verse, pre-chorus, chorus, and the
  owner's case) gets no `build_up`.

## Decision

1. **One vocabulary: the structure document's.** `segment_label` is retired, and core segments no
   longer carry a `label`. The standalone v1 response keeps its field `segments[].label` (and
   `label_method: "heuristic"`) and fills it from `label_calc`. A segment that `label_calc` leaves
   unlabelled (for example silence around the content) reads `unlabelled`. analysis-v1, its schema,
   the core `analyzer_version` and the 0.4 caps are unchanged: no analysis-v1 document ever carried
   the older labels.
2. **The standalone v1 cache gets its own response version.**
   - A new constant `V1_RESPONSE_VERSION` sits next to the v1 formatter (`app/analyze.py`). It
     starts at 2 with this change; every cached row so far counts as version 1.
   - The standalone cache is keyed by `(track_id, ANALYZER_VERSION, V1_RESPONSE_VERSION)`. #45
     adds a `response_version` column with a default of 1 for existing rows (an idempotent
     `ALTER TABLE` at `init_db`), and makes it part of the primary key.
   - A lookup with any other pair is a miss. A miss recomputes the analysis from the converted file,
     so no cached response with the older labels is ever served, without `?refresh=true`. If the
     converted file is gone, the endpoint answers `404` as it does today for a track with no file,
     never the stale row.
   - Old rows are left in place (reversible: lowering the constant serves them again), and are never
     read at the new version.
   - The v2 caches, analysis-v1 and the core `analyzer_version` are untouched.
   - Any later change to the v1 response shape or its labels raises `V1_RESPONSE_VERSION`.
3. **Pre-chorus is out of analysis-v1 for now, and it is an open item.** The `label` enum is
   unchanged, and a build into a chorus stays a `verse`. spotdown has **no** pre-chorus marker
   today:
   - `build_up` events cover only the rise into a **detected drop**, so a pre-chorus before a chorus
     with no drop is not marked at all;
   - a consumer can at most read the segment just before a `chorus` as a candidate. That works only
     where segmentation happened to split the pre-chorus from the verse, and it is not measured.

   The waveform view (spotdown#48) shades the `build_up` events it has, and claims nothing for the
   rest. Pre-chorus is an open item for the owner's feedback on the finished product. There is no
   other marker in the current schema to use instead: `events` kinds are `build_up`, `drop`,
   `breakdown`, `hard_stop`, `fade` and `tempo_change`, none of which describes a pre-chorus.
4. **When to revisit.** Either held-out label evidence exists, or the owner's feedback on the
   finished product asks for a pre-chorus. It then comes as an additive optional field in
   analysis-v1, or as an analysis-v2 label enum served beside v1. Either way, the PR carries a
   classbeat compatibility note. A rise into a chorus without a drop would also need a new detector,
   because `build_ups` looks only before drops.

## Alternatives

- **Add `pre_chorus` to the analysis-v1 enum now.** Rejected: the contract makes this a new schema
  version, and classbeat must update its copy. That is too much for a 0.4-capped heuristic resting on
  one observation.
- **Add an optional per-label field now (for example `role: "pre_chorus"`).** This would be
  contract-compatible, but rejected for now. The rule would be written on the same synthetic corpus
  that grades it, so no gate could show that it is right. A field, once shipped, is also costly to
  remove. It stays the first option to revisit.
- **Keep both vocabularies and document which one to read.** Rejected: the trial note asks for one,
  and the older labels add nothing that `label_calc` lacks.
- **Drop `segments[].label` from the standalone v1 response.** Rejected: it breaks v1 readers for
  no gain. Filling it from `label_calc` keeps the field and gives it one meaning.
- **Relabel cached v1 responses on read from their `document`.** Rejected: responses cached before
  `document` existed have nothing to relabel from, so a fallback would still serve the older labels
  or have to drop them silently.
- **Accept stale v1 labels until `?refresh=true`.** Rejected: it breaks the "one vocabulary"
  promise for every cached track.
- **Bump the core `ANALYZER_VERSION`.** Rejected: it would also invalidate every v2 analysis for a
  change that does not touch them.

## Consequences

- One label for each segment everywhere: the standalone v1 response, the analysis-v1 document and
  the UI agree, including for tracks analysed before the change. Those miss the cache once and are
  recomputed.
- **Cache impact:** the standalone v1 cache misses once per track; the v2 caches see no change.
  Later label or segment changes (spotdown#46, #47) bump the core `analyzer_version` as the contract
  says.
- A cached standalone track whose converted file has since been deleted answers `404` until it is
  converted again, instead of serving its stale response.
- The owner gets no pre-chorus label or marker in this release. Rises into detected drops are shaded
  on the waveform; a pre-chorus before a chorus without a drop is not marked. The owner's product
  feedback decides whether to build one.
- Readers of the standalone v1 `segments[].label` who matched on `chorus/drop` or `breakdown/quiet`
  must match `chorus` or `breakdown` instead. No known consumer reads the v1 field: classbeat uses
  the v2 contract.
