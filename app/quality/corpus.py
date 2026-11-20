"""Runs an external corpus (local audio plus annotated truth) through the real service path.

    python -m app.quality.corpus --corpus DIR [--corpus DIR ...] [--baseline FILE ...]
                                 [--only TEXT] [--json PATH] [--timeout SECONDS]

Each ``--corpus`` is one source (``cc``, ``spotify``, ``studio`` or ``mix``; see
``docs/quality-harness.md``), judged on its own: its gates, its size rule and, with a ``--baseline``
file of derived results of today's path for that source (``baseline-v1``), its baseline rows. Every
track runs as one job in its **own process**, so time and the peak memory of its analysis child are
measured, not extrapolated. Exit status: 0 everything measured and passing, 1 a gate failed,
2 nothing failed but something is not measured or the corpus is too small. Audio stays where it
is; the ``--json`` report holds ids and derived numbers only. Nothing touches the network.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from . import corpus_io, corpus_report, track_job
from .corpus_calc import CorpusError
from .runner import GATES

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def run_isolated(
    track: Dict[str, Any], audio: Path, timeout_s: float, python: str = sys.executable
) -> Dict[str, Any]:
    """One track in a fresh process; a crash or timeout of that process is a failed job."""
    failed: Dict[str, Any] = {
        "id": track["id"],
        "ok": False,
        "outcome": "runner_failed",
        "doc": None,
        "audio_left": 0,
        "duration_s": None,
        "work_s": 0.0,
        "wall_s": 0.0,
        "peak_gib": None,
    }
    with tempfile.TemporaryDirectory(prefix="quality-corpus-") as tmp:
        out = Path(tmp) / "result.json"
        cmd = [python, "-m", "app.quality.track_job", str(audio), str(out), track["id"]]
        if track.get("reference"):
            cmd.append(json.dumps(track["reference"]))
        # The child's temp root is ours: whatever it leaves after a kill (timeout, OOM) is
        # counted here and removed with this directory, never reported as "nothing kept".
        env = dict(os.environ, TMPDIR=tmp)
        res = failed
        try:
            subprocess.run(
                cmd,
                cwd=str(PROJECT_ROOT),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout_s,
                check=True,
            )
            res = json.loads(out.read_text(encoding="utf-8"))
        except (subprocess.SubprocessError, OSError, ValueError):
            pass
        res["audio_left"] = int(res.get("audio_left") or 0) + leftovers(Path(tmp))
        return res


def leftovers(tmp: Path) -> int:
    """Files a dead job left in its upload and work stores under ``tmp``."""
    return sum(
        len(track_job.files_under(d / "data" / "uploads", d / "data" / "work"))
        for d in tmp.glob("quality-*")
    )


def run_corpus(
    index: Dict[str, Any], audio: Dict[str, Path], only: str, timeout_s: float
) -> Dict[str, Any]:
    """The index narrowed to ``only`` and its results, one track at a time (serial: the machine
    is the one being measured)."""
    tracks = [t for t in index["tracks"] if only in t["id"]]
    narrowed = dict(index, tracks=tracks)
    results = [run_isolated(t, audio[t["id"]], timeout_s) for t in tracks]
    return {"index": narrowed, "results": results}


def format_report(report: Dict[str, Any]) -> str:
    res = report["result"]
    cov = res["coverage"]
    lines = [
        f"corpus {report['corpus']}: {corpus_report.overall(report)} "
        f"(gates v{report['gates_version']}); {cov['tracks']} tracks "
        f"(min {cov['min_tracks']}), {cov['annotated']} annotated (min {cov['min_annotated']}), "
        f"{cov['jobs_ok']} jobs ok",
        f"{'gate':36} {'rule':>10} {'value':>9} {'n':>5}  result",
    ]
    for r in res["gates"]:
        n = "" if r["n"] is None else int(r["n"])
        lines.append(
            f"{r['gate']:36} {r['op']} {r['threshold']:>7} {r['value']!s:>9} {n!s:>5}  "
            f"{r['status']}"
        )
    base = report["baseline"]
    if base is not None:
        lines.append(
            f"baseline {base['baseline']}: {base['verdict']} over "
            f"{base['common_tracks']} common tracks"
        )
        for r in base["rows"]:
            lines.append(
                f"  {r['metric']:34} {r['direction']:>6} tol {r['tolerance']:<5} "
                f"spotdown {r['spotdown']!s:>7} baseline {r['baseline']!s:>7} n {r['n']:<4} "
                f"{r['status']}"
            )
    m = report["metrics"]
    lines.append(
        f"(reported) five-minute tracks {m.get('long_tracks')}, peak memory over every track "
        f"{m.get('peak_memory_gib_all')} GiB, slowest track {m.get('max_track_seconds')} s"
    )
    if report["failed_jobs"]:
        lines.append(f"failed jobs: {', '.join(report['failed_jobs'])}")
    return "\n".join(lines)


def exit_code(reports: List[Dict[str, Any]]) -> int:
    states = [corpus_report.overall(r) for r in reports]
    return 1 if "FAIL" in states else (2 if "incomplete" in states or not states else 0)


def load_baselines(paths: List[Path], gates: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Baseline files keyed by the path they describe (``source``); a source no rule uses, or
    an id present in two files of the same source, is refused rather than merged."""
    known = {r["baseline"] for r in gates["per_source"].values() if isinstance(r, dict)}
    out: Dict[str, Dict[str, Any]] = {}
    for path in paths:
        source, entries = corpus_io.load_baseline(path)
        if source not in known:
            raise CorpusError(f"{path}: baseline source {source!r} is not one of {sorted(known)}")
        bucket = out.setdefault(source, {})
        dup = sorted(set(bucket) & set(entries))
        if dup:
            raise CorpusError(f"{path}: ids already in another {source} file: {dup[:5]}")
        bucket.update(entries)
    return out


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--corpus", type=Path, action="append", required=True)
    ap.add_argument("--baseline", type=Path, action="append", default=[])
    ap.add_argument("--only", default="", help="run tracks whose id contains this text")
    ap.add_argument("--json", type=Path, help="write the derived report here (no audio, no docs)")
    ap.add_argument("--timeout", type=float, default=900.0)
    args = ap.parse_args(argv)
    gates = json.loads(GATES.read_text(encoding="utf-8"))
    reports: List[Dict[str, Any]] = []
    try:
        baselines = load_baselines(args.baseline, gates)
        for directory in args.corpus:
            index, audio = corpus_io.load_corpus(directory)
            run = run_corpus(index, audio, args.only, args.timeout)
            rule = gates["per_source"].get(index["corpus"])
            base = baselines.get(rule["baseline"]) if rule else None
            report = corpus_report.build(gates, run["index"], run["results"], base)
            reports.append(report)
            print(format_report(report))
    except CorpusError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        args.json.write_text(json.dumps(reports, indent=1) + "\n", encoding="utf-8")
    return exit_code(reports)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
