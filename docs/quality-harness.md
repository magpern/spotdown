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
| `robustness_fraction`, `p95_seconds_per_5min`, `peak_memory_gib` | jobs that finish, time per 5 minutes of audio, peak child memory |
| `false_exact`, `audio_kept_after_job` | **hard gates**, always 0 |

The hard gates are never relaxed: `gates_calc.validate` refuses a file where either is not 0, and
an unmeasured gate fails. `false_exact` counts any `exact` claim in an analysis document; matching
does not exist yet, so it is 0 by construction until a later phase adds it and must then be
measured against real alignments.

## Changing a threshold

Only with evaluation evidence in the PR and the usual review, recorded in classbeat ADR 0040. Bump
`version` in the gates file. Never lower a threshold to make a run pass.

## Synthetic fixtures

`app/quality/specs.py` lists 24 specs (tempo, sections, silence, fades, noise); `synth.py` renders
them with a seeded generator and computes the truth (beats, boundaries, drops, edges) arithmetically
from the spec. Only the truth manifest is committed; audio is rendered to a temporary directory at
run time. Add a fixture by adding a spec and running `fixtures_io --write`.

## Annotated CC-licensed tracks (procedure, nothing downloaded here)

1. The owner picks tracks under a licence that allows analysis and derived annotations (for example
   CC BY or CC0) and records title, artist, licence and source URL.
2. Annotate on the owner's machine: BPM, beat times, boundaries, drop times, silence/fade edges, in
   the same truth shape as `manifest.json` (`bpm`, `beats`, `boundaries`, `drops`, `edges`,
   `duration_s`).
3. Commit only the annotation file and the licence record under `quality/fixtures/cc/`. Never commit
   audio (`test_no_audio_is_committed_under_quality` enforces it) and never download in CI.

## Owner-run real-music corpus

Real songs stay on the owner's machine. The owner annotates or reuses MOWL/classbeat analyses as
reference, runs the runner against the local audio, and shares only the derived result JSON
(`--json`). Per-source gates (Spotify, YouTube, upload vs the baseline path) need that corpus and
the baseline path (classbeat#1298); they are listed under `per_source` in the gates file and are
not evaluated by this harness yet.

## Known limits of the synthetic set

It is easy and the analysis constants were tuned on it, so passing it proves the harness and the
absence of regressions, not real-music accuracy. Calibration error is low partly because the set has
no wrong-octave cases. Octave choice prefers 70-170 BPM. Breakdown sections without a pulse can
produce spurious beats.
