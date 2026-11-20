"""The quality run over the committed fixtures: the CI gate. A regression fails this test."""

from __future__ import annotations

import json
from typing import Dict

from app.quality import align_cases, exact_calc, fixtures_io, gates_calc, runner
from tests.corpus_helpers import make_doc

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
    "downbeat_f_4_4": 0.90,
    "downbeat_f_3_4": 0.78,
    # 3/4 phrases are withheld (structure_doc.phrases): F 0.0 by design, and no wrong phrase
    "phrase_f_waltz_reported": 0.0,
    "phrase_precision_waltz_reported": 1.0,
    "meter_accuracy_4_4": 0.90,
    "meter_accuracy_3_4": 0.74,
    # Reported-only per-corpus phrase results, pinned so they cannot get worse unseen. The pickup
    # fixtures (2 tracks, 9 truth phrases) are the case the structure analysis is weak on.
    "phrase_f_baseline_reported": 0.85,
    "phrase_f_pickup_reported": 0.45,
    "boundary_f_3s_all_reported": 0.95,
    # the file's own offset is found for the two same-recording references of each fixture
    "alignment_offset_within_0_05s_reported": 1.0,
}
ACHIEVED_CEILINGS: Dict[str, float] = {
    "ece_bpm": 0.08,
    "ece_boundaries": 0.075,
    "label_overconfidence": 0.05,
    "phrase_overconfidence": 0.02,
    "downbeat_overconfidence": 0.02,
    "phrase_overconfidence_waltz_reported": 0.03,
    "meter_wrong_claims_reported": 0.0,
    "meter_overconfidence_reported": 0.02,
}
# The 60 s plan threshold stays in the gates file and in the command line run. Under pytest the
# machine is shared and the run uses fewer workers, so this test only guards against a blow-up.
LOOSE_SECONDS_PER_5MIN = 120.0


def test_exact_claims_find_a_claim_anywhere_and_an_alignment_alone_is_not_one() -> None:
    assert not exact_calc.exact_claims({"a": [1, {"b": "estimated"}]})
    assert exact_calc.exact_claims({"a": [{"b": "exact"}]}) == ["/a/0/b"]
    assert not exact_calc.exact_claims({"match": {"alignment": {"offset_s": 0.0}}})


def test_the_alignment_cases_cover_the_trimmed_untrimmed_and_other_references() -> None:
    entries = fixtures_io.load_manifest()["tracks"]
    cases = align_cases.build_cases(entries)
    assert len(cases) == 6
    silence = [c for c in cases if c["id"].startswith("silence-edges-130")]
    assert [c["id"].split("+")[1] for c in silence] == ["trimmed", "untrimmed", "other"]
    trimmed, untrimmed, other = silence
    assert trimmed["true_offset_s"] == -2.0 and untrimmed["true_offset_s"] == 0.0
    assert other["same_recording"] is False and other["true_offset_s"] is None
    assert other["reference"]["duration_s"] > untrimmed["reference"]["duration_s"]


def test_an_exact_claim_in_the_run_fails_the_hard_gate() -> None:
    gates = json.loads(runner.GATES.read_text(encoding="utf-8"))
    entry = fixtures_io.load_manifest()["tracks"][0]
    doc = make_doc(extra={"timing_class": "exact"})
    clean = {"id": "x", "ok": True, "doc": make_doc(), "audio_left": 0, "per_5min_s": 1.0}
    claimed = dict(clean, doc=doc)
    assert exact_calc.false_exact(runner.exact_cases([entry], [clean], [])) == 0
    assert exact_calc.false_exact(runner.exact_cases([entry], [claimed], [])) == 0  # an upload
    spotify = dict(entry, spec=dict(entry["spec"], corpus="spotify"))
    assert exact_calc.false_exact(runner.exact_cases([spotify], [claimed], [])) == 1
    assert gates["hard"]["false_exact"] == 0


def test_the_analysis_meets_every_gate_and_its_achieved_floor_on_the_fixtures() -> None:
    gates = json.loads(runner.GATES.read_text(encoding="utf-8"))
    entries = fixtures_io.load_manifest()["tracks"]
    results = runner.run_corpus(entries, workers=2)
    aligned = align_cases.run_cases(entries, workers=2)
    report = runner.build_report(gates, entries, results, aligned)
    timing = "p95_seconds_per_5min"
    failed = [g for g in gates_calc.failures(report["gates"]) if g != timing]
    assert failed == [], runner.format_table(report)
    m = report["metrics"]
    assert m[timing] <= LOOSE_SECONDS_PER_5MIN
    assert m["false_exact"] == 0 and m["audio_kept_after_job"] == 0
    assert m["alignment_cases_reported"] == 6 and all(c["ok"] for c in aligned)
    below = {k: m[k] for k, v in ACHIEVED_FLOORS.items() if m[k] < v}
    above = {k: m[k] for k, v in ACHIEVED_CEILINGS.items() if m[k] > v}
    assert below == {} and above == {}, runner.format_table(report)
