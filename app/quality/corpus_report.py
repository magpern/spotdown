"""The report of one external-corpus run (no I/O): scoring, gates, baseline comparison.

``build`` takes the corpus index, the per-track job results and, optionally, the derived baseline
results of the same corpus, and returns one report: per-corpus metrics with how many tracks each
rests on, the gate verdicts (pass, FAIL, not measured), the coverage of the corpus against its size
rule, the measured time and memory, the hard gates and the baseline rows. Nothing in the report
holds audio, titles or documents: only ids, counts and derived numbers.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import (
    baseline_calc,
    corpus_calc,
    corpus_gates_calc,
    exact_calc,
    measure_calc,
)
from . import corpus_scoring_calc as sc


def _exact_cases(corpus: str, tracks: List[Dict[str, Any]], results: List[Dict[str, Any]]):
    return [
        {
            "corpus": corpus,
            "doc": r["doc"],
            "same_recording": t.get("same_recording", True),
            "true_offset_s": t.get("true_offset_s"),
        }
        for t, r in zip(tracks, results)
        if r["doc"]
    ]


def _baseline_verdict(rows: List[Dict[str, Any]]) -> str:
    if any(r["status"] == corpus_gates_calc.FAIL for r in rows):
        return corpus_gates_calc.FAIL
    if not rows or any(r["status"] == corpus_gates_calc.NOT_MEASURED for r in rows):
        return "incomplete"
    return corpus_gates_calc.PASS


def build(
    gates: Dict[str, Any],
    index: Dict[str, Any],
    results: List[Dict[str, Any]],
    baseline: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    corpus, tracks = index["corpus"], index["tracks"]
    tol = gates["parameters"]
    ext = gates["external"]
    done = {t["id"]: r for t, r in zip(tracks, results) if r["doc"]}
    estimates = {tid: sc.estimate_from_doc(r["doc"], tol) for tid, r in done.items()}
    records = [
        dict(sc.score_track(t["truth"], estimates[t["id"]], tol), id=t["id"])
        for t in tracks
        if t["id"] in done
    ]
    metrics: Dict[str, Any] = dict(sc.aggregate(records))
    n = len(tracks)
    metrics["robustness_fraction"] = round(sum(r["ok"] for r in results) / n, 4) if n else None
    metrics.update(measure_calc.measure(results, ext["measurement"]))
    cases = _exact_cases(corpus, tracks, results)
    metrics["false_exact"] = float(exact_calc.false_exact(cases))
    metrics["audio_kept_after_job"] = float(sum(r["audio_left"] for r in results))
    metrics["alignment_overreach_reported"] = float(exact_calc.alignment_overreach(cases))
    coverage = corpus_calc.coverage(tracks, ext["corpora"][corpus], [r["ok"] for r in results])
    verdict = corpus_gates_calc.evaluate_corpus(gates, corpus, metrics, coverage)
    report: Dict[str, Any] = {
        "corpus": corpus,
        "gates_version": gates["version"],
        "failed_jobs": [t["id"] for t, r in zip(tracks, results) if not r["ok"]],
        "metrics": metrics,
        "result": verdict,
        "baseline": None,
        "records": records,
    }
    rule = gates["per_source"].get(corpus)
    if rule is not None:
        # No baseline file for a source that has a baseline rule is an unmeasured gate.
        cmp = (
            baseline_calc.compare(rule, tracks, estimates, baseline, tol)
            if baseline is not None
            else {"baseline": rule["baseline"], "common_tracks": 0, "rows": []}
        )
        cmp["verdict"] = _baseline_verdict(cmp["rows"])
        report["baseline"] = cmp
    return report


def overall(report: Dict[str, Any]) -> str:
    """``FAIL``, ``incomplete`` or ``pass`` for the corpus, counting its baseline rows."""
    parts = [report["result"]["verdict"]]
    if report["baseline"] is not None:
        parts.append(report["baseline"]["verdict"])
    if corpus_gates_calc.FAIL in parts:
        return corpus_gates_calc.FAIL
    return "incomplete" if "incomplete" in parts else corpus_gates_calc.PASS
