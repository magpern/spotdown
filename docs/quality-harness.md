# Quality harness and baseline gates (SD-P2)

The harness scores the analysis on corpora with known truth and fails CI when a gate regresses.
Thresholds are the classbeat plan section 7 initial values, kept in
[`quality/quality_gates.json`](../quality/quality_gates.json) (versioned).

## Running

```bash
python -m app.quality.runner                  # all fixtures, prints one row per gate, exit 1 on failure
python -m app.quality.runner --only edm-128   # fixtures whose id contains the text
python -m app.quality.runner --json out.json  # full report with per-track records
python -m app.quality.fixtures_io --write     # regenerate quality/fixtures/synthetic/manifest.json
```

Each fixture is analysed as a real job (`execute_job`, sandboxed child, `Store`), so time, memory,
robustness and "no audio kept" are measured on the production path. CI runs it as
`tests/test_quality_runner.py`; `tests/test_quality_fixtures.py` fails if the committed manifest
differs from what the code generates.

## Metrics

| Gate | Definition |
|---|---|
| `bpm_within_2pct_fraction` | BPM within 2 % at x0.5, x1 or x2 (octave-aware) |
| `bpm_exact_octave_fraction` | BPM within 2 % at the true octave |
| `beat_f_*` | beat F-measure, +-70 ms, beats before 5 s not scored; "four on the floor" subset and overall |
| `boundary_f_3s` | structure boundary F, +-3 s, file ends excluded (1.5 s reported only) |
| `edge_within_0_25s_fraction` | silence and fade start/end within 0.25 s |
| `drop_precision`, `drop_recall` | drops within one beat |
| `ece_bpm`, `ece_boundaries` | expected calibration error, 10 bins |
| `label_accuracy` | section labels (SD-P2+, spotdown#7): share of 0.5 s frames in the content range whose label equals the annotated one, mean over tracks; a frame without a label is wrong |
| `label_overconfidence` | label confidence above achieved accuracy, per segment, 10 bins (one-sided calibration error; underconfidence is not penalised). The two-sided `ece_labels_reported` is reported only |
| `robustness_fraction`, `p95_seconds_per_5min`, `peak_memory_gib` | jobs that finish, time per 5 minutes of audio, peak child memory |
| `false_exact`, `audio_kept_after_job` | **hard gates**, always 0 |

The hard gates are never relaxed: `gates_calc.validate` refuses a file where either is not 0, and
an unmeasured gate fails. `false_exact` counts any `exact` claim in an analysis document; matching
does not exist yet, so it is 0 by construction until a later phase adds it and must then be
measured against real alignments.

## Section label gate (SD-P2+, spotdown#7)

Truth labels are annotated per section in the specs (`label` on each section, carried into the truth
as `labels`): intro, verse, chorus, bridge, outro, breakdown. Convention: a build into a drop is a
`verse` (pre-chorus), a drop a `chorus`, a pulse-free pad section a `breakdown`, the thinner middle
section between two choruses of a groove track a `bridge`. The thresholds (accuracy >= 0.70,
overconfidence <= 0.10) are set by this enhancement; they are not in plan section 7. Achieved on the
24 fixtures: accuracy 0.914, overconfidence 0.003 (the two-sided calibration error, 0.30, is reported: the confidences are deliberately low). Failures are visible, not tuned away: the labels
mistake a thin breakdown for a bridge (slow-drop-80), end on a verse when the outro is a fade
(fade-out-108) and miss the chorus on noisy-break-98.

Gate status is `label_calc.GATE_PASSED`: while False no label confidence exceeds 0.4; it may only
be True while both gates pass on the corpus (the runner test fails otherwise, as it fails on any
failing gate). The rules were written while looking at the synthetic fixtures, so the accuracy is an
in-sample figure; the owner corpus (spotdown#20) is the real test and recalibrates the confidences.

## Changing a threshold

Only with evaluation evidence in the PR and the usual review, recorded in classbeat ADR 0040. Bump
`version` in the gates file. Never lower a threshold to make a run pass.

## Synthetic fixtures

`app/quality/specs.py` lists 24 specs (tempo, sections, silence, fades, noise); `synth.py` renders
them with a seeded generator and computes the truth (beats, boundaries, drops, edges) arithmetically
from the spec. Only the truth manifest is committed; audio is rendered to a temporary directory at
run time. Add a fixture by adding a spec and running `fixtures_io --write`.

## Not yet runnable: CC-licensed tracks, the owner corpus, the baseline comparison

Nothing below can be run with the code in this repository today. The runner only renders the
committed synthetic specs: it has no input for local audio plus a truth record, nothing loads
`quality/fixtures/cc/`, and `per_source` in the gates file is prose, not a computed gate. The work
is tracked in [spotdown#20](https://github.com/magpern/spotdown/issues/20) (#6 stays open until it
lands). What the procedures will be, so that the annotations made meanwhile have the right shape:

- **CC-licensed tracks (at least 20).** The owner picks tracks whose licence allows analysis and
  derived annotations (CC BY, CC0) and records title, artist, licence and source URL. Annotations
  use the truth shape of `manifest.json` (`bpm`, `beats`, `boundaries`, `drops`, `edges`,
  `duration_s`). Only annotations and provenance are committed, never audio
  (`test_no_audio_is_committed_under_quality` enforces it); CI never downloads.
- **Owner corpus.** Real songs stay on the owner's machine; only derived result JSON is shared.
  Per-source gates (Spotify, YouTube, upload) against the baseline path (MOWL sections,
  `mowl.music.analyze_track`, `mixanalysis`) need the corpus (classbeat#1298) and a
  baseline-comparison mode.

## Scope of what is measured

- **Regression guard.** The runner test asserts every plan gate and, on top of that, a committed
  floor a little inside the achieved values (`ACHIEVED_FLOORS` in `tests/test_quality_runner.py`),
  so a regression that stays inside a plan threshold is still caught. The floors guard the
  synthetic set only; raise them when the analysis improves, never lower one to pass.
- **Time and memory are extrapolated.** The longest fixture is 108 s. `p95_seconds_per_5min` scales
  the per-track time linearly to 5 minutes and `peak_memory_gib` is measured on tracks of 108 s or
  less. Neither has been measured on a real 5-minute track (spotdown#20). The 60 s plan threshold
  is unchanged in the gates file and the command-line run; under pytest on a shared machine the
  test only guards against a blow-up (120 s).

## Known limits of the synthetic set

It is easy and the analysis constants were tuned on it, so passing it proves the harness and the
absence of regressions, not real-music accuracy. Calibration error is low partly because the set has
no wrong-octave cases. Octave choice prefers 70-170 BPM. Breakdown sections without a pulse can
produce spurious beats.

Further limits, all carried by spotdown#20:

- Most truth edges are the file's own start and end, which `content_edges` reports exactly, so
  `edge_within_0_25s_fraction` says little; only 3 fixtures carry real silence or fade edges.
- `false_exact` is a placeholder: any `exact` string or `alignment` key counts. Upload and studio
  sources are `exact` under the plan, so it must be redefined when matching and alignment exist.
- Pulse-free breakdowns (edm-128, silence-edges-130) are not reported as breakdown events; they
  have no gate in the plan.
- BPM confidence is about 0.97 on most tracks (the cap) and ECE passes because accuracy is 100 %;
  only the owner corpus is a real calibration test.
