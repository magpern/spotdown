"""The quality run over the committed fixtures: the CI gate. A regression fails this test."""

from __future__ import annotations

import json
from typing import Dict

from app.quality import fixtures_io, gates_calc, runner
from app.quality.runner import claims_exact

# Floors (">=") and ceilings ("<=") a little inside the values achieved on the committed
# fixtures (analyzer 5), so a regression that stays inside the plan thresholds is still caught.
# They guard the synthetic set only; raise them when the analysis improves, never lower one to
# make a run pass.
ACHIEVED_FLOORS: Dict[str, float] = {
    "bpm_within_2pct_fraction": 1.0,
    "bpm_exact_octave_fraction": 1.0,
    "beat_f_four_on_the_floor": 0.90,
    "beat_f_overall": 0.90,
    "boundary_f_3s": 0.975,
    "edge_within_0_25s_fraction": 1.0,
    "drop_precision": 1.0,
    "drop_recall": 1.0,
    "robustness_fraction": 1.0,
    "label_accuracy": 0.85,
    "phrase_f": 0.80,
}
ACHIEVED_CEILINGS: Dict[str, float] = {
    "ece_bpm": 0.08,
    "ece_boundaries": 0.075,
    "label_overconfidence": 0.05,
    "phrase_overconfidence": 0.02,
}
# The 60 s plan threshold stays in the gates file and in the command line run. Under pytest the
# machine is shared and the run uses fewer workers, so this test only guards against a blow-up.
LOOSE_SECONDS_PER_5MIN = 120.0


def test_claims_exact_finds_a_match_claim_anywhere() -> None:
    assert not claims_exact({"a": [1, {"b": "estimated"}]})
    assert claims_exact({"a": [{"b": "exact"}]})
    assert claims_exact({"match": {"alignment": {}}})


def test_the_analysis_meets_every_gate_and_its_achieved_floor_on_the_fixtures() -> None:
    gates = json.loads(runner.GATES.read_text(encoding="utf-8"))
    entries = fixtures_io.load_manifest()["tracks"]
    results = runner.run_corpus(entries, workers=2)
    report = runner.build_report(gates, entries, results)
    timing = "p95_seconds_per_5min"
    failed = [g for g in gates_calc.failures(report["gates"]) if g != timing]
    assert failed == [], runner.format_table(report)
    m = report["metrics"]
    assert m[timing] <= LOOSE_SECONDS_PER_5MIN
    assert m["false_exact"] == 0 and m["audio_kept_after_job"] == 0
    below = {k: m[k] for k, v in ACHIEVED_FLOORS.items() if m[k] < v}
    above = {k: m[k] for k, v in ACHIEVED_CEILINGS.items() if m[k] > v}
    assert below == {} and above == {}, runner.format_table(report)
