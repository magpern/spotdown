"""The quality runner: every committed fixture through the real service path, scored and gated.

Each fixture is rendered to a temporary WAV, uploaded to a throwaway service store and executed by
``app.service.worker.execute_job`` with the real sandbox, exactly as a v2 job runs. The document the
store returns is scored against the fixture's truth (``scoring_calc``) and the corpus metrics are
checked against ``quality/quality_gates.json`` (``gates_calc``). Exit status 1 on any failing gate.

    python -m app.quality.runner [--workers N] [--only SUBSTRING] [--json PATH]

Hard gates measured here: no audio left on disk after any job, and no analysis claiming ``exact``.
Nothing touches the network.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import Settings
from ..core import ANALYZER_VERSION
from ..service.analysis_doc import ANALYZER_VERSION_V2
from ..service.store import Store
from ..service.uploads import upload_path
from ..service.worker import execute_job
from . import gates_calc, scoring_calc
from .fixtures_io import ROOT, load_manifest, render_to

GATES = ROOT / "quality_gates.json"
SCOPE = "quality"
MEMORY_LIMIT_MB = 1536  # the 1.5 GiB gate, enforced on the analysis child as well as measured


def _settings(data_dir: Path) -> Settings:
    key = data_dir / "service_key"
    data_dir.mkdir(parents=True, exist_ok=True)
    key.write_text("quality-harness-key-" + "0" * 32 + "\n", encoding="utf-8")
    return Settings(
        service_mode=True,
        service_key_file=key,
        data_dir=data_dir / "data",
        redis_url="redis://127.0.0.1:1/0",
        analysis_max_memory_mb=MEMORY_LIMIT_MB,
        analysis_max_cpu_s=240,
        analysis_wall_timeout_s=300,
    )


def _files_under(*roots: Path) -> List[Path]:
    return [p for r in roots if r.exists() for p in r.rglob("*") if p.is_file()]


def claims_exact(node: Any) -> bool:
    """Whether a document asserts ``exact`` anywhere (no analysis here may: matching is later)."""
    if isinstance(node, dict):
        return "alignment" in node or any(claims_exact(v) for v in node.values())
    if isinstance(node, list):
        return any(claims_exact(v) for v in node)
    return node == "exact"


def run_track(entry: Dict[str, Any]) -> Dict[str, Any]:
    """One fixture as one job; never raises (a failure is a result)."""
    from ..service.routes import job_view

    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="quality-") as tmp:
        root = Path(tmp)
        settings = _settings(root)
        settings.work_dir.mkdir(parents=True, exist_ok=True)
        settings.uploads_dir.mkdir(parents=True, exist_ok=True)
        store = Store(settings.service_db_path)
        audio = render_to(entry["spec"], root)
        data = audio.read_bytes()
        job_id = "job_" + entry["id"].replace("-", "_")
        upload_id = "up_" + job_id
        upload_path(settings, upload_id).write_bytes(data)
        audio.unlink()
        store.add_upload(upload_id, SCOPE, hashlib.sha256(data).hexdigest(), len(data), "wav", 3600)
        store.claim_upload(upload_id, SCOPE)
        store.add_job(
            {
                "job_id": job_id,
                "cache_scope": SCOPE,
                "source_kind": "upload",
                "upload_id": upload_id,
            }
        )
        execute_job(job_id, settings, store)
        job = store.get_job(job_id)
        view = job_view(job) if job else {"status": "missing"}
        doc: Optional[Dict[str, Any]] = (
            store.get_analysis(view["analysis_id"]) if view.get("status") == "done" else None
        )
        left = len(_files_under(settings.uploads_dir, settings.work_dir))
    run = (doc or {}).get("provenance", {}).get("run", {}).get("timings_s", {})
    work_s = float(run.get("decode", 0.0)) + float(run.get("analyse", 0.0))
    return {
        "id": entry["id"],
        "ok": doc is not None,
        "outcome": view.get("outcome"),
        "doc": doc,
        "audio_left": left,
        "per_5min_s": work_s * 300.0 / entry["truth"]["duration_s"],
        "wall_s": time.monotonic() - t0,
    }


def run_corpus(entries: List[Dict[str, Any]], workers: int) -> List[Dict[str, Any]]:
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return list(pool.map(run_track, entries))


def build_report(
    gates: Dict[str, Any], entries: List[Dict[str, Any]], results: List[Dict[str, Any]]
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
    metrics["audio_kept_after_job"] = float(sum(r["audio_left"] for r in results))
    metrics["false_exact"] = float(sum(claims_exact(r["doc"]) for r in results if r["doc"]))
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
        f"{report['tracks']} tracks",
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
    return "\n".join(lines)


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    ap.add_argument("--only", default="", help="run fixtures whose id contains this text")
    ap.add_argument("--json", type=Path, help="write the full report here")
    args = ap.parse_args(argv)
    gates = json.loads(GATES.read_text(encoding="utf-8"))
    entries = [t for t in load_manifest()["tracks"] if args.only in t["id"]]
    report = build_report(gates, entries, run_corpus(entries, args.workers))
    print(format_table(report))
    if args.json:
        args.json.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
