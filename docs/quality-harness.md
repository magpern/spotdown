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
python -m app.quality.corpus --corpus DIR [--baseline FILE]   # an external corpus (below)
python -m app.quality.cc_fetch --dest DIR     # the CC-licensed audio of the catalog, owner only
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
| `false_exact`, `audio_kept_after_job` | **hard gates**, always 0 (`false_exact` is defined below) |

In the external runner a hard gate is `not_measured` (with its `n`) when nothing was examined: no document for `false_exact`, no job for `audio_kept_after_job`; a job process that was killed (timeout, out of memory) has its leftover upload and work files counted, then removed.

The hard gates are never relaxed: `gates_calc.validate` refuses a file where either is not 0, and
an unmeasured gate fails.

## `false_exact` (spotdown#20)

classbeat plan section 2: a track is `exact` only when the analysed file *is* the played recording
(an upload that is played, a SwedeBeat studio track, the mix that is played), never for a YouTube
match of a Spotify track however confident. spotdown reports evidence and never emits a timing
class, so the gate (`exact_calc`) counts the **claims a document could make that its case does not
earn**:

- a claim is the string `exact` anywhere in an analysis document. An `alignment` object alone is
  evidence, not a claim (the placeholder counted it, which made any alignment a failure);
- it is entitled when the corpus is one whose files are the played recording (`baseline`,
  `phrases`, `meter`, `cc`, `studio`, `mix`; **not** `spotify`, `youtube` or an unknown name), the
  truth says the file is the played recording (`same_recording`, default true) and, when the document
  carries an alignment, its `offset_s` is within 50 ms of the true offset (`true_offset_s`, the plan's
  beat-level equivalence tolerance);
- anything else is a false exact, counted once per document. Today the analysis never emits the
  string, so the value is 0; the tests show the gate fails for each way of earning it wrongly.

The synthetic run adds six alignment jobs (`align_cases`: the same recording with its leading silence
cut or not, true offsets `-content_start_s` and 0, on the two fixtures that have leading silence,
and a version 3 s longer that is not the same recording). The offset is found within 50 ms on the
four same-recording cases (`alignment_offset_within_0_05s_reported`). **What this does not show:**
durations alone cannot tell two recordings of similar length apart, so an `ok` alignment on another
version with a high confidence is possible; it is counted as `alignment_overreach_reported` and is
the reason spotdown never promotes an alignment to `exact` by itself (the consumer's timing class
needs a beat-level reference, plan section 2).

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

## Over-split check (spotdown#46)

The 2026-11-21 trial cut one vocal song into 21 parts, with boundaries inside single verses and
choruses. `app/core/merge_calc.py` merges over-fine segments before the groups and labels are made
(rules and constants in its docstring: segments under 2.5 bars, and neighbours that sound alike
across a weak boundary; drops, content edges and quiet-region edges are never merged away).
The owner's listening is not ground truth, so the check is synthetic: two `oversplit` fixtures
(`corpus: oversplit`) carry fills and timbre changes inside verses and choruses (sections flagged
`continues` in the spec), and the truth has boundaries only at the six real section changes.
`tests/test_quality_oversplit.py` asserts at most truth sections plus one segments, and that the
same audio with the merge switched off makes more (10 and 14 segments against 6 sections before
the merge, 7 and 7 after). The fixtures enter no gate and are reported apart (`*_oversplit_reported`; the `all` set leaves them out, because they also hold the truth boundaries the structure analysis misses, spotdown#47); `segments_per_truth_section_reported`
(oversplit: 1.167, was 2.0 before the merge) and
`segments_per_truth_section_baseline_reported` (1.027, 150 segments in 146 sections, the same with
and without the merge, so the 24 baseline fixtures are unchanged) are reported only.

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
and `phrase_overconfidence` <= 0.03, pooled over the 26 fixtures of the baseline and phrase corpora
(achieved 0.909: precision 0.930, recall 0.889; overconfidence 0.0; they were 0.864, 0.884 and 0.844
before #9 gave the bar grid the right phase, which snaps the phrase times to the right bars). A mutation that inflates the confidences (cap 0.99, scale 1.2)
gives 0.097 and fails the overconfidence gate; an empty detector fails `phrase_f`.

**What the numbers show, per corpus (reported only, and pinned by achieved floors):**

| phrases | F | precision | recall | overconfidence |
|---|---|---|---|---|
| pooled, 26 fixtures (the gate) | 0.909 | 0.930 | 0.889 | 0.0 |
| the 24 baseline fixtures | 0.950 | 0.962 | 0.938 | 0.0 |
| the 2 pickup fixtures (9 truth phrases) | 0.500 | 0.571 | 0.444 | 0.0 |
| the 4 waltz (3/4) fixtures, not in the gate | 0.000 | 1.000 | 0.000 | 0.0 |

**3/4 phrases are withheld.** With bars of three beats the phrases of the waltz fixtures scored F 0.600
and overconfidence 0.090, three times the gate's 0.03, so a 3/4 meter claim now yields no
`grid.phrases` (`structure_doc.phrases`; a unit test pins it) and the waltz row is zero recall with no
wrong phrase. The row stays out of the pooled gate, and `tests/test_quality_runner.py` pins its
overconfidence ceiling (0.03) and precision floor (1.0), so 3/4 phrases cannot come back worse unseen.
When no meter is claimed phrases still assume 4/4.

The pickup fixtures do not show that the detector handles a pickup; they show that it does not,
on audio. The cause is the missed first boundary after the pickup (boundary F 0.92 and 0.60 on the
two tracks), so the phrase chain has no boundary to start from. Before the content end became a
candidate boundary the same two tracks scored F 0.40 with overconfidence 0.059, above the 0.03
threshold; the pooled gate hid that. **Blind 8-bar tiling from the first boundary (no structure)
scores at least as well as the detector on the baseline fixtures** (0.917 against 0.885 measured
at the first review of #25, before #9 changed the bar grid; not measured again), because those arrangements are built from 8-bar blocks. So the
pooled `phrase_f` gate cannot tell the detector from tiling and is a regression check only, not
evidence of phrase quality. `tests/test_phrase_calc.py` pins the pure function on hand-made
boundaries (a pickup moves the first phrase); it does not show that the pipeline finds the
boundary that function needs.

The content end counts as a closing boundary (with a fixed 0.5 boundary confidence, below the
cap): it adds the last phrase of tracks that end on a phrase boundary, raising recall from 0.800
to 0.844 and F from 0.842 to 0.864 with no threshold changed.

Like the labels, the rules and the truth convention come from one author and the corpus is small
(26 tracks, 5 phrase shapes), so the 0.4 cap (`phrase_calc.CAP`, pinned by a test) is not raised
until held-out evidence exists (spotdown#20). Phrases count bars of the estimated meter (4/4
when none is claimed) and snap to the downbeat grid of #9.

## Downbeat and meter gates (SD-P2+, spotdown#9)

Truth comes from the arrangement (`synth._truth`): `downbeats` are the bar starts of every
section that plays a pulse, and `beats_per_bar` is the spec's meter. 26 fixtures are 4/4. Four
(`waltz-*`, `corpus: meter`, `beats_per_bar: 3`) are 3/4 with 96 to 138 BPM, kick on beat 1 or on
every beat, two hat patterns and one noisy; they enter only these gates, the phrase reports and
the all-fixture reports, so the baseline floors still measure the original 24.

Scoring (`downbeats_calc`): downbeats match one to one within the beat tolerance (0.07 s, first
5 s trimmed like beats); the meter is right when the document claims exactly the true beats per
bar (no claim is a miss). Everything is pooled **per true meter, never as one number**:
`downbeat_f_4_4`, `downbeat_f_3_4`, `meter_accuracy_4_4`, `meter_accuracy_3_4`
(`meter_accuracy_min` is the gated one) and the one-sided `downbeat_overconfidence`.

| gate (new thresholds, to be recorded in classbeat ADR 0040) | threshold | achieved |
|---|---|---|
| `downbeat_f_4_4` | >= 0.85 | 0.935 (precision 0.900, recall 0.973) |
| `downbeat_f_3_4` | >= 0.75 | 0.811 (precision 0.878, recall 0.754) |
| `meter_accuracy_min` | >= 0.70 | 0.750 (4/4: 0.923, 3/4: 0.750) |
| `downbeat_overconfidence` | <= 0.03 | 0.0 |

**Against the trivial baselines (same fixtures, previous analyzer where noted):**

| | 4/4 F | 3/4 F | 3/4 meter |
|---|---|---|---|
| the previous output (4/4, the beat position with the most onset weight) | 0.064 | 0.263 | wrong |
| 4/4 from the first tracked beat | 0.217 | 0.264 | wrong |
| 4/4 forced on the new anchored grid (mutation) | 0.935 | 0.000 | wrong |
| the new estimate | 0.935 | 0.811 | 3 of 4 right |

The honest reading: **on 4/4 the meter estimate adds nothing to the downbeat F**. The gain over the
previous output comes from anchoring the bars at the start of the music and at the detected drops,
and a forced 4/4 on that grid scores the same. What the meter estimate adds is the 3/4 fixtures
and the refusal to claim a meter without an accent (`slow-drop-74` and `slow-drop-80`, no accent
pattern: no meter, no downbeats; `waltz-kick1-126`: the accent is below the threshold, no claim).
Both anchors are priors from dance music, not measurements of the bar: a track that starts on an
anacrusis or a drop that is not on a downbeat shifts every bar of its piece, and the bar
confidence only falls when the anchors disagree with each other, not when both are wrong.

Mutation checks (each fails a gate): meter forced to 4 gives `downbeat_f_3_4` 0.0 and
`meter_accuracy_min` 0.0; no drop anchors (the start of the music only) gives `downbeat_f_4_4`
0.783 and `meter_accuracy_min` 0.692; confidences set to 0.99 give `downbeat_overconfidence`
0.093. The 3/4 class is four fixtures (three claimed meters), so `meter_accuracy_3_4` moves in
steps of 0.25 and `downbeat_f_3_4` rests on about 110 bars.

**The refusal threshold is not protected by a gate, and removing it raises the scores here.**
`MIN_Z = 0` and `MIN_MARGIN = 0` (always claim) pass every gate and score better (`downbeat_f_3_4`
0.934, `meter_accuracy_min` 0.923), because the three withheld tracks then get a right claim. Only
`test_no_accent_pattern_claims_nothing` (noise and flat accents claim nothing) keeps the threshold; it
is a conservative choice made for tracks without an accent, which this corpus barely has. Reported
only: `meter_wrong_claims_reported` (0 on the corpus) and `meter_overconfidence_reported` (0.0), pinned
by achieved ceilings. `grid.meter.confidence` has no calibration gate beyond the 0.4 cap and that
reported number (30 tracks, 27 claims), so a raise of the cap needs held-out evidence for the meter
confidence too.

These are regression checks, not evidence of quality: the 3/4 patterns and the anchors were written
with the detector, the corpus has one author and 30 tracks, and the meter rule only separates 3
from 4. So `meter_calc.GATE_PASSED` stays False and **no meter or downbeat confidence exceeds 0.4**
(`meter_calc.CAP`, pinned by a test); the cap is not raised until held-out evidence exists (CC
fixtures or the owner corpus, spotdown#20). The schema has one confidence for the whole downbeat
list, so the per-bar confidence stays in the core (`bars[i].confidence`) and the list carries
their mean.

## Changing a threshold

Only with evaluation evidence in the PR and the usual review, recorded in classbeat ADR 0040. Bump
`version` in the gates file. Never lower a threshold to make a run pass.

## Synthetic fixtures

`app/quality/specs.py` lists 30 specs (tempo, sections, silence, fades, noise); `synth.py` renders
them with a seeded generator and computes the truth (beats, boundaries, drops, edges) arithmetically
from the spec. Only the truth manifest is committed; audio is rendered to a temporary directory at
run time. Add a fixture by adding a spec and running `fixtures_io --write`.

## External corpora: the CC and owner procedures (spotdown#20)

`python -m app.quality.corpus --corpus DIR` runs a directory of local audio with annotated truth
through the same service path as the fixtures (sandboxed child, throwaway store). **Nothing is
downloaded and nothing is committed**: the directory must be outside the repository (the runner
refuses one inside it), the audio is copied into the throwaway store and removed with it, and the
report holds ids and derived numbers only (no titles, file names or documents). Only mp3, mp4 and wav
are accepted, as by the service.

**Layout** of a corpus directory, one per source (`cc`, `spotify`, `studio`, `mix`):

```json
{"corpus_version": 1, "corpus": "studio", "tracks": [
  {"id": "s01", "audio": "audio/s01.wav",
   "truth": {"bpm": 128, "beats": [0.5, 0.97], "boundaries": [32.0], "drops": [64.0],
             "edges": [0.0, 241.3], "four_on_the_floor": true}}]}
```

Truth times are seconds on the file's clock. A key that is absent is *not annotated* and its metric
is not measured for that track; an empty list is an annotation. A mix has `songs` (`start_s`,
`end_s`, `bpm`) instead. Unknown keys are refused. A CC track also carries `licence` (`CC0-1.0`,
`CC-BY-3.0`, `CC-BY-4.0`) and `provenance` (`title`, `artist`, `source_url`). Optional per track:
`reference` (`kind`, `id`, `duration_s`) to run the alignment, `same_recording` and
`true_offset_s` for `false_exact`.

**Gates and verdicts.** Each corpus is judged alone against its set in `quality_gates.json`
(`external.gate_sets`; thresholds are the plan's, plus the mix gates: song start and end within
2 s on at least 95 %, BPM per song within 2 % on at least 95 %, slowest mix 180 s, 1.5 GiB). A gate
is `pass`, `FAIL`, or `not_measured`: no annotation, or too few five-minute tracks, is never a pass.
Each corpus also has a size rule (`external.corpora`: cc 20 tracks all annotated, spotify 30 with 15
annotated, studio 20 with 15, mix 5). Verdict: `FAIL` if any gate fails; otherwise `incomplete` if
anything is not measured or the corpus is too small; `pass` only when all is measured and passes.
Exit status 0, 1 and 2 respectively. Every metric prints how many tracks it rests on (`n`), so a
number over three tracks says so. Nothing is pooled across corpora. The enhancement gates (labels,
phrases, downbeats, meter) are not evaluated on external corpora and their confidence caps stay as
they are: this runner produces the held-out evidence for them but does not raise any cap.

**Time and memory are measured, not extrapolated.** Every track runs in its own process
(`track_job`), so the peak memory of its analysis child is that track's (`ru_maxrss` of the
process's children). `p95_seconds_per_5min` and `peak_memory_gib` use only tracks of 240 to 420 s
and are `not_measured` with fewer than five of them: a corpus of short clips cannot pass them. The
time is the job's decode and analysis time; the length factor only normalises a track a little
under or over five minutes. Runs are serial because the machine is the thing being measured, on the
box the gate is about (the 60 s is "on the box"). The sandbox uses POSIX resource limits, including a
finite `RLIMIT_AS`, which macOS refuses: natively on macOS every job fails with `SubprocessError`.
Run it on Linux or WSL, or in a Linux container on a Mac (`python:3.11-slim` with ffmpeg and
libsndfile, the repository mounted read-only; all 23 synthetic gates passed that way on 2026-11-21).
Not native Windows.

**Baseline comparison.** With `--baseline FILE` (repeatable) the per-source rows of
`per_source` in the gates file are computed from **derived baseline results only**. The owner runs
today's path where the audio is (MOWL sections for Spotify tracks, `mowl.music.analyze_track` for
studio tracks, `design/mixanalysis.py` for mixes) and keeps only this file:

```json
{"baseline_version": 1, "source": "mowl.music.analyze_track",
 "tracks": {"s01": {"bpm": 128.0, "beats": [0.5, 0.97], "boundaries": [32.1], "drops": [64.0],
                    "edges": [0.0, 241.3], "warnings": 0,
                    "songs": [{"start_s": 0.0, "end_s": 200.0, "bpm": 128.0}]}}}
```

The `source` must be one a rule names (`mowl_sections`, `mowl.music.analyze_track` or `design/mixanalysis.py`); a file only feeds the corpus whose rule names it, and a track id in two files of one source is refused. Every field is optional and a field the baseline does not provide is never compared. Both sides are
scored with the same metrics against the same truth, over the tracks where **both sides have that metric** (paired per metric: a baseline missing a key drops that track from that row on both sides), and
a row passes when spotdown is at most the plan's tolerance worse: Spotify BPM agreement and boundary
F within 0.02 of MOWL, warning share at most 5 percentage points above (absolute); studio beat F within 0.02, BPM agreement
within 1 point, boundary F within 0.03; mix song start and end and BPM per song at least the
baseline (and the absolute 95 % above). A row on fewer than five tracks is `not_measured`; a source
that has a baseline rule and no baseline file is `incomplete`. For tracks without truth the report
gives `bpm_agreement_with_baseline_reported` (the plan compares the unannotated rest with the
baseline only). **Reading decisions** (accepted by the Architect): the mix gate reads the structure
boundaries and the content ends as the estimated song starts and ends and takes the tempo at a
song's midpoint from the tempo and its change points, because spotdown has no notion of a song; the
spotdown side of "quality-report warnings" is counted from the document (a tempo confidence below
0.5, fewer than four beats, music covering less than half of the file) because classbeat's quality
report is not available here.

**CC-licensed tracks (at least 20).** The catalog `quality/fixtures/cc/catalog.json` is the committed
part: for each track the title, artist, licence, source page, direct https download URL, the file's
size and sha256, and a **human** annotation with all of `bpm`, `beats`, `boundaries`, `drops` and
`edges`. It is empty today: choosing and annotating real tracks is the owner's work and no annotation
here was produced by running the analyser on the track (that would make the truth circular).
`python -m app.quality.cc_fetch --dest DIR` (run by the owner; never by CI or an agent) downloads each
file over https, keeps it only if its size and sha256 equal the catalog's, and writes `DIR/corpus.json`;
then `python -m app.quality.corpus --corpus DIR`. Until there are 20 tracks the `cc` corpus reports
`incomplete` and the CC set offers no held-out evidence.

**Owner corpus (classbeat#1298).** Real songs stay on the owner's machine in one directory per
source with a `corpus.json` as above, annotated on at least 15 of the real tracks (plan section 7).
Shadow run: analyse, run the baseline path, then `python -m app.quality.corpus --corpus spotify
--corpus studio --corpus mix --baseline spotify-baseline.json --baseline studio-baseline.json
--baseline mix-baseline.json --json report.json` and share `report.json`.

## Scope of what is measured

- **Regression guard.** The runner test asserts every plan gate and, on top of that, a committed
  floor a little inside the achieved values (`ACHIEVED_FLOORS` in `tests/test_quality_runner.py`),
  so a regression that stays inside a plan threshold is still caught. The floors guard the
  synthetic set only; raise them when the analysis improves, never lower one to pass.
- **Time and memory of the synthetic run are extrapolated.** The longest fixture is 108 s.
  `p95_seconds_per_5min` scales the per-track time linearly to 5 minutes and `peak_memory_gib` is
  measured on tracks of 108 s or less. The gates on real five-minute tracks are the external-corpus
  ones above (measured per track, `not_measured` without five such tracks); no such run exists yet,
  because it needs real audio. The 60 s plan threshold is unchanged in the gates file and the
  command-line run; under pytest on a shared machine the test only guards against a blow-up (120 s).

## Known limits of the synthetic set

It is easy and the analysis constants were tuned on it, so passing it proves the harness and the
absence of regressions, not real-music accuracy. Calibration error is low partly because the set has
no wrong-octave cases. Octave choice prefers 70-170 BPM. Breakdown sections without a pulse can
produce spurious beats.

Further limits, carried by the owner's corpus (classbeat#1298):

- Most truth edges are the file's own start and end, which `content_edges` reports exactly, so
  `edge_within_0_25s_fraction` says little; only 3 fixtures carry real silence or fade edges.
- Pulse-free breakdowns (edm-128, silence-edges-130) are not reported as breakdown events; they
  have no gate in the plan.
- BPM confidence is about 0.97 on most tracks (the cap) and ECE passes because accuracy is 100 %;
  only the owner corpus is a real calibration test.
