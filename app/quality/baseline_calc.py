"""Baseline comparison (pure): spotdown against today's path, per source, from derived results.

The baseline path is MOWL's sections (Spotify), ``mowl.music.analyze_track`` (studio tracks) or
``design/mixanalysis.py`` (mixes). The owner runs it where the audio is and keeps only a derived
results file (``baseline-v1``); this module never sees audio. Both sides are scored with the same
metrics on the same truth (``corpus_scoring_calc``), over the tracks where **both have a value**,
and each row of the source's rule (plan section 7) passes when spotdown is not worse than the
baseline by more than the tolerance (``higher``: at least baseline - tolerance; ``lower``: at most
baseline + tolerance). A row whose metric rests on fewer than ``baseline_min_tracks`` tracks is not
measured, never passed.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

from . import corpus_scoring_calc as sc
from . import metrics_calc as m
from .corpus_calc import CorpusError, _number

BASELINE_VERSION = 1
ENTRY_KEYS = frozenset({"bpm", "beats", "boundaries", "drops", "edges", "warnings", "songs"})
EPS = 1e-9


def validate_baseline(doc: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """The per-track entries of a ``baseline-v1`` file, or CorpusError."""
    if doc.get("baseline_version") != BASELINE_VERSION:
        raise CorpusError(f"baseline_version must be {BASELINE_VERSION}")
    if not isinstance(doc.get("source"), str) or not doc["source"]:
        raise CorpusError("baseline: source must name the baseline path")
    out: Dict[str, Dict[str, Any]] = {}
    for tid, entry in (doc.get("tracks") or {}).items():
        unknown = sorted(set(entry) - ENTRY_KEYS)
        if unknown:
            raise CorpusError(f"baseline {tid}: unknown keys {unknown}")
        for key in ("beats", "boundaries", "drops", "edges"):
            for t in entry.get(key) or []:
                _number(t, f"baseline {tid}.{key}")
        if "bpm" in entry:
            _number(entry["bpm"], f"baseline {tid}.bpm", positive=True)
        for s in entry.get("songs") or []:
            if set(s) != {"start_s", "end_s", "bpm"}:
                raise CorpusError(f"baseline {tid}: a song needs start_s, end_s and bpm")
        out[tid] = entry
    return out


def compare(
    rule: Dict[str, Any],
    tracks: Sequence[Dict[str, Any]],
    spotdown: Dict[str, Dict[str, Any]],
    baseline: Dict[str, Dict[str, Any]],
    tol: Dict[str, float],
) -> Dict[str, Any]:
    """Rows for one source. ``spotdown`` maps id to its estimate (only jobs that produced a
    document), ``baseline`` maps id to a baseline entry."""
    common = [t for t in tracks if t["id"] in spotdown and t["id"] in baseline]
    side_a, side_b = [], []
    agree: List[bool] = []
    for t in common:
        base_est = sc.estimate_from_baseline(baseline[t["id"]])
        side_a.append(sc.score_track(t["truth"], spotdown[t["id"]], tol))
        side_b.append(sc.score_track(t["truth"], base_est, tol))
        if base_est.get("bpm") and spotdown[t["id"]].get("bpm"):
            agree.append(
                m.bpm_errors(base_est["bpm"], spotdown[t["id"]]["bpm"], tol["bpm_tolerance"])[0]
            )
    mine, theirs = sc.aggregate(side_a), sc.aggregate(side_b)
    rows = [_row(spec, mine, theirs, tol["baseline_min_tracks"]) for spec in rule["rows"]]
    return {
        "baseline": rule["baseline"],
        "common_tracks": len(common),
        "missing_baseline": [
            t["id"] for t in tracks if t["id"] in spotdown and t["id"] not in baseline
        ],
        "rows": rows,
        "bpm_agreement_with_baseline_reported": round(sum(agree) / len(agree), 4)
        if agree
        else None,
    }


def _row(
    spec: Dict[str, Any], mine: Dict[str, Any], theirs: Dict[str, Any], min_n: float
) -> Dict[str, Any]:
    name, tol_v = spec["metric"], spec["tolerance"]
    a, b = mine.get(name), theirs.get(name)
    n = min(mine.get(f"n_{name}") or 0, theirs.get(f"n_{name}") or 0)
    if a is None or b is None or n < min_n:
        status = "not_measured"
    elif spec["direction"] == "higher":
        status = "pass" if a >= b - tol_v - EPS else "FAIL"
    else:
        status = "pass" if a <= b + tol_v + EPS else "FAIL"
    return {
        "metric": name,
        "direction": spec["direction"],
        "tolerance": tol_v,
        "spotdown": a,
        "baseline": b,
        "n": n,
        "status": status,
    }
