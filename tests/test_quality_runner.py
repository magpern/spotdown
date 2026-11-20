"""The quality run over the committed fixtures: the CI gate. A regression fails this test."""

from __future__ import annotations

import json

from app.quality import fixtures_io, gates_calc, runner
from app.quality.runner import claims_exact


def test_claims_exact_finds_a_match_claim_anywhere() -> None:
    assert not claims_exact({"a": [1, {"b": "estimated"}]})
    assert claims_exact({"a": [{"b": "exact"}]})
    assert claims_exact({"match": {"alignment": {}}})


def test_the_analysis_meets_every_gate_on_the_committed_fixtures() -> None:
    gates = json.loads(runner.GATES.read_text(encoding="utf-8"))
    entries = fixtures_io.load_manifest()["tracks"]
    results = runner.run_corpus(entries, workers=2)
    report = runner.build_report(gates, entries, results)
    rows = report["gates"]
    assert gates_calc.failures(rows) == [], runner.format_table(report)
    by_name = {r["gate"]: r["value"] for r in rows}
    assert by_name["false_exact"] == 0 and by_name["audio_kept_after_job"] == 0
