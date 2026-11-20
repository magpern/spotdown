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
| `phrase_f` | phrases (SD-P2+, spotdown#8): pooled F of phrases, right when start and end are within 2 beats of a truth phrase (one to one; the ends fix 8 or 16 bars). Precision and recall are reported only |
| `phrase_overconfidence` | phrase confidence above achieved precision, 10 bins (one-sided) |
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
overconfidence <= 0.10) are set by this enhancement, are not in plan section 7 and are to be recorded in classbeat ADR 0040. Achieved on the
24 baseline fixtures: accuracy 0.914, overconfidence 0.0 with the 0.4 cap (the two-sided calibration error, 0.53, is reported only: the confidences are deliberately low). Failures are visible, not tuned away: the labels
mistake a thin breakdown for a bridge (slow-drop-80), end on a verse when the outro is a fade
(fade-out-108) and miss the chorus on noisy-break-98.

**The two label gates are regression checks, not evidence of label quality.** The truth labels are a
convention written for the same three section templates the rules were written against, so the
accuracy is in-sample, and the one-sided overconfidence gate constrains almost nothing while
accuracy is about 0.9 (a mutation setting every confidence to 0.99 still passed it). So
`label_calc.GATE_PASSED` stays False and **no label confidence exceeds 0.4**; the cap is not raised
until held-out evidence exists (CC-licensed fixtures or the owner corpus, spotdown#20), ideally with
a two-sided calibration gate or per-label reliability. `tests/test_label_calc.py` pins the flag to
False. The base confidences are deliberately conservative priors, not measured hit rates. The report
records both the core analyzer version and the document version (`5.1`) that produced the labels.

## Phrase gate (SD-P2+, spotdown#8)

Truth phrases come from the arrangement by arithmetic (`synth._phrases`): consecutive sections
are grouped until they add up to exactly 8 or 16 bars; a group that skips past both starts over at
the section that overshot (a 4-bar pickup, then 8-bar sections, puts the first phrase after the
pickup; 3-bar sections never make one). Two extra fixtures, `pickup-edm-140` and `pickup-groove-118` (`corpus: phrases`), put a 4-bar
pickup before the first phrase. They enter only the phrase gate: the baseline and label metrics
are computed on the original 24 fixtures (`baseline_tracks`), so the committed floors measure the
same set. With them included `boundary_f_3s` is 0.964 (reported as `boundary_f_3s_all_reported`,
with the beat and BPM equivalents), below its floor of 0.975: the structure analysis misses the
first boundary after a 4-bar pickup.

Thresholds set by this enhancement, to be recorded in classbeat ADR 0040: `phrase_f` >= 0.70
and `phrase_overconfidence` <= 0.03, pooled over all 26 fixtures (achieved 0.864: precision 0.884,
recall 0.844; overconfidence 0.0). A mutation that inflates the confidences (cap 0.99, scale 1.2)
gives 0.097 and fails the overconfidence gate; an empty detector fails `phrase_f`.

**What the numbers show, per corpus (reported only, and pinned by achieved floors):**

| phrases | F | precision | recall | overconfidence |
|---|---|---|---|---|
| pooled, 26 fixtures (the gate) | 0.864 | 0.884 | 0.844 | 0.0 |
| the 24 baseline fixtures | 0.900 | 0.911 | 0.889 | 0.0 |
| the 2 pickup fixtures (9 truth phrases) | 0.500 | 0.571 | 0.444 | 0.0 |

The pickup fixtures do not show that the detector handles a pickup; they show that it does not,
on audio. The cause is the missed first boundary after the pickup (boundary F 0.92 and 0.60 on the
two tracks), so the phrase chain has no boundary to start from. Before the content end became a
candidate boundary the same two tracks scored F 0.40 with overconfidence 0.059, above the 0.03
threshold; the pooled gate hid that. **Blind 8-bar tiling from the first boundary (no structure)
scores at least as well as the detector on the baseline fixtures** (0.917 against 0.885 measured
at the first review of this PR), because those arrangements are built from 8-bar blocks. So the
pooled `phrase_f` gate cannot tell the detector from tiling and is a regression check only, not
evidence of phrase quality. `tests/test_phrase_calc.py` pins the pure function on hand-made
boundaries (a pickup moves the first phrase); it does not show that the pipeline finds the
boundary that function needs.

The content end counts as a closing boundary (with a fixed 0.5 boundary confidence, below the
cap): it adds the last phrase of tracks that end on a phrase boundary, raising recall from 0.800
to 0.844 and F from 0.842 to 0.864 with no threshold changed.

Like the labels, the rules and the truth convention come from one author and the corpus is small
(26 tracks, 5 phrase shapes), so the 0.4 cap (`phrase_calc.CAP`, pinned by a test) is not raised
until held-out evidence exists (spotdown#20). Phrases also depend on the downbeat grid and a 4/4
assumption; #9 (downbeats and meter) should improve them and may let the meter claim be made.

## Changing a threshold

Only with evaluation evidence in the PR and the usual review, recorded in classbeat ADR 0040. Bump
`version` in the gates file. Never lower a threshold to make a run pass.

## Synthetic fixtures

`app/quality/specs.py` lists 26 specs (tempo, sections, silence, fades, noise); `synth.py` renders
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
