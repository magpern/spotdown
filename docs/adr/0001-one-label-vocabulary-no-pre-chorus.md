# ADR 0001: one label vocabulary; no pre-chorus label in analysis-v1

**Status:** Proposed (2026-11-21). It becomes Accepted when its Opus review passes.

**Issue:** spotdown#44. It is built in spotdown#45 (one vocabulary) and spotdown#48 (build-ups on the
waveform). The numbers are spotdown's own; classbeat ADRs (for example classbeat ADR 0040) keep theirs.

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

## Decision

1. **One vocabulary: the structure document's.** `segment_label` is retired, and core segments no
   longer carry a `label`. The standalone v1 response keeps its field `segments[].label` (and
   `label_method: "heuristic"`) and fills it from `label_calc`. A segment that `label_calc` leaves
   unlabelled (for example silence around the content) reads `unlabelled`. analysis-v1, its schema,
   the `analyzer_version` and the 0.4 caps are unchanged: no analysis-v1 document ever carried the
   older labels.
2. **Pre-chorus is out of analysis-v1 for now.** The `label` enum is unchanged, and a build into a
   chorus stays a `verse`.
3. **What consumers have instead.** analysis-v1 already reports `events` of kind `build_up`, which
   mark the rise into a chorus or a drop. The standalone waveform view (spotdown#48) draws them as
   shaded ranges, so the owner can judge pre-chorus regions in product testing. A consumer that wants
   a pre-chorus hint can take a `verse` whose end overlaps a `build_up` that leads into a `chorus`.
4. **When to revisit.** Either held-out label evidence exists, or the owner's feedback on the
   finished product asks for a pre-chorus. It then comes as an additive optional field in
   analysis-v1, or as an analysis-v2 label enum served beside v1. Either way, the PR carries a
   classbeat compatibility note.

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

## Consequences

- One label for each segment everywhere: the standalone v1 response, the analysis-v1 document and
  the UI agree.
- No contract change and no cache invalidation from the vocabulary retirement alone. Later label or
  segment changes (spotdown#46, #47) bump the `analyzer_version` as the contract says.
- The owner gets no pre-chorus label in this release. The build-ups on the waveform are the stand-in.
- Readers of the standalone v1 `segments[].label` who matched on `chorus/drop` or `breakdown/quiet`
  must match `chorus` or `breakdown` instead. No known consumer reads the v1 field: classbeat uses
  the v2 contract.
