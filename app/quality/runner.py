"""The quality runner: every committed fixture through the real service path, scored and gated.

Each fixture is rendered to a temporary WAV, uploaded to a throwaway service store and executed by
``app.service.worker.execute_job`` with the real sandbox, exactly as a v2 job runs. The document the
store returns is scored against the fixture's truth (``scoring_calc``) and the corpus metrics are
checked against ``quality/quality_gates.json`` (``gates_calc``). Exit status 1 on any failing gate.

    python -m app.quality.runner [--workers N] [--only SUBSTRING] [--json PATH]

Hard gates measured here: no audio left on disk after any job, and no ``exact`` claim the case is
not entitled to (``exact_calc``).
Nothing touches the network.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core import ANALYZER_VERSION
from ..service.analysis_doc import ANALYZER_VERSION_V2
from . import align_cases, exact_calc, gates_calc, scoring_calc, track_job
from .fixtures_io import ROOT, load_manifest, render_to

GATES = ROOT / "quality_gates.json"


def run_track(entry: Dict[str, Any]) -> Dict[str, Any]:
    """One fixture as one job; never raises (a failure is a result)."""
    with tempfile.TemporaryDirectory(prefix="quality-render-") as tmp:
        audio = render_to(entry["spec"], Path(tmp))
        res = track_job.run_audio_job(entry["id"], audio, entry.get("reference"))
    res["per_5min_s"] = res["work_s"] * 300.0 / entry["truth"]["duration_s"]
    return res


def run_corpus(entries: List[Dict[str, Any]], workers: int) -> List[Dict[str, Any]]:
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return list(pool.map(run_track, entries))


def exact_cases(
    entries: List[Dict[str, Any]],
    results: List[Dict[str, Any]],
    aligned: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Every analysed document as a case for ``exact_calc``: a fixture is the played recording."""
    plain = [
        {"corpus": e["spec"].get("corpus", "baseline"), "doc": r["doc"]}
        for e, r in zip(entries, results)
        if r["doc"]
    ]
    return plain + [c for c in aligned if c["doc"]]


def build_report(
    gates: Dict[str, Any],
    entries: List[Dict[str, Any]],
    results: List[Dict[str, Any]],
    aligned: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    tol = gates["parameters"]
    records = []
    for entry, res in zip(entries, results):
        if res["doc"] is not None:
            records.append(
                scoring_calc.score_track(
                    dict(
                        entry["truth"],
                        id=entry["id"],
                        corpus=entry["spec"].get("corpus", "baseline"),
                    ),
                    res["doc"],
                    tol,
                )
            )
    metrics = scoring_calc.aggregate(records) if records else {}
    n = len(results)
    times = sorted(r["per_5min_s"] for r in results if r["ok"])
    metrics["robustness_fraction"] = round(sum(r["ok"] for r in results) / n, 4) if n else 0.0
    metrics["p95_seconds_per_5min"] = (
        round(times[min(len(times) - 1, int(0.95 * len(times)))], 2) if times else 0.0
    )
    metrics["peak_memory_gib"] = round(
        resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024.0 / 1024.0, 3
    )
    aligned = aligned or []
    cases = exact_cases(entries, results, aligned)
    metrics["audio_kept_after_job"] = float(sum(r["audio_left"] for r in [*results, *aligned]))
    metrics["false_exact"] = float(exact_calc.false_exact(cases))
    metrics["alignment_cases_reported"] = float(len(aligned))
    metrics["alignment_overreach_reported"] = float(exact_calc.alignment_overreach(aligned))
    hits = exact_calc.offset_hits(aligned)
    metrics["alignment_offset_within_0_05s_reported"] = (
        round(sum(hits) / len(hits), 4) if hits else 0.0
    )
    rows = gates_calc.evaluate(gates, metrics)
    return {
        "analyzer_version": ANALYZER_VERSION,
        "document_version": ANALYZER_VERSION_V2,
        "gates_version": gates["version"],
        "tracks": n,
        "metrics": metrics,
        "gates": rows,
        "failed": gates_calc.failures(rows),
        "worst": {
            "beat_f": scoring_calc.worst_tracks(records, "beat_f"),
            "boundary_f": scoring_calc.worst_tracks(records, "boundary_f"),
        },
        "records": records,
    }


def format_table(report: Dict[str, Any]) -> str:
    lines = [
        f"quality gates v{report['gates_version']}, analyzer {report['analyzer_version']} "
        f"(document {report['document_version']}), "
        f"{report['tracks']} tracks ({int(report['metrics'].get('baseline_tracks', 0))} baseline)",
        f"{'gate':32} {'rule':>10} {'value':>9}  result",
    ]
    for r in report["gates"]:
        lines.append(
            f"{r['gate']:32} {r['op']} {r['threshold']:>7} {r['value']!s:>9}  "
            f"{'pass' if r['pass'] else 'FAIL'}"
        )
    m = report["metrics"]
    lines.append(f"(reported only) boundary F at 1.5 s: {m.get('boundary_f_1_5s_reported')}")
    lines.append(f"(reported only) label ECE: {m.get('ece_labels_reported')}")
    lines.append(
        f"(reported only) phrase precision {m.get('phrase_precision')}, "
        f"recall {m.get('phrase_recall')}"
    )
    for part in ("baseline", "pickup", "waltz"):
        lines.append(
            f"(reported only) phrases on the {part} fixtures: "
            f"F {m.get(f'phrase_f_{part}_reported')}, "
            f"overconfidence {m.get(f'phrase_overconfidence_{part}_reported')}"
        )
    lines.append(
        f"(reported only) all fixtures: boundary F {m.get('boundary_f_3s_all_reported')}, "
        f"beat F {m.get('beat_f_overall_all_reported')}"
    )
    return "\n".join(lines)


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    ap.add_argument("--only", default="", help="run fixtures whose id contains this text")
    ap.add_argument("--json", type=Path, help="write the full report here")
    args = ap.parse_args(argv)
    gates = json.loads(GATES.read_text(encoding="utf-8"))
    entries = [t for t in load_manifest()["tracks"] if args.only in t["id"]]
    results = run_corpus(entries, args.workers)
    report = build_report(gates, entries, results, align_cases.run_cases(entries, args.workers))
    print(format_table(report))
    if args.json:
        args.json.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
