"""Scores one analysis-v1 document against one truth record, and aggregates a corpus (pure).

A document is read through the public contract only (``tempo``, ``grid``, ``structure``,
``events``, ``signal``), so the same scoring applies to any analyser that emits analysis-v1.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from . import metrics_calc as m


def _edges(doc: Dict[str, Any]) -> List[float]:
    """Every silence or fade edge the document claims, on the file's clock."""
    sig = doc["signal"]
    found = [sig["content_start_s"], sig["content_end_s"]]
    if sig.get("fade_in"):
        found.append(sig["fade_in"]["end_s"])
    if sig.get("fade_out"):
        found.append(sig["fade_out"]["start_s"])
    return [float(v) for v in found]


def _boundaries(doc: Dict[str, Any]) -> List[Dict[str, float]]:
    """Segment starts after the first, with their confidences."""
    segs = doc["structure"]["segments"]
    return [{"t": s["start_s"], "c": s["boundary_confidence"]} for s in segs[1:]]


def score_track(
    truth: Dict[str, Any], doc: Dict[str, Any], tol: Dict[str, float]
) -> Dict[str, Any]:
    dur = truth["duration_s"]
    bpm_octave, bpm_exact = m.bpm_errors(truth["bpm"], doc["tempo"]["bpm"], tol["bpm_tolerance"])
    est_bounds = _boundaries(doc)
    ref_bounds = truth["boundaries"]
    beat_s = 60.0 / truth["bpm"]
    drops = [e["start_s"] for e in doc["events"] if e["kind"] == "drop"]
    bound_hits = [
        any(abs(b["t"] - r) <= tol["boundary_tol_s"] for r in ref_bounds) for b in est_bounds
    ]
    return {
        "id": truth.get("id"),
        "four_on_the_floor": truth["four_on_the_floor"],
        "bpm_octave_ok": bpm_octave,
        "bpm_exact_ok": bpm_exact,
        "bpm_confidence": doc["tempo"]["confidence"],
        "beat_f": m.beat_f(truth["beats"], doc["grid"]["beats"]["times_s"], tol["beat_tol_s"]),
        "boundary_f": m.boundary_f(
            ref_bounds, [b["t"] for b in est_bounds], dur, tol["boundary_tol_s"]
        ),
        "boundary_f_report": m.boundary_f(
            ref_bounds, [b["t"] for b in est_bounds], dur, tol["boundary_report_tol_s"]
        ),
        "boundary_conf": [b["c"] for b in est_bounds],
        "boundary_correct": bound_hits,
        "edge_hits": m.edge_hits(truth["edges"], _edges(doc), tol["edge_tol_s"]),
        "drops": list(m.drop_counts(truth["drops"], drops, tol["drop_tol_beats"] * beat_s)),
    }


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def aggregate(records: Sequence[Dict[str, Any]]) -> Dict[str, float]:
    """Corpus-level metrics, keyed as the ``quality_gates`` file names them."""
    n = len(records)
    four = [r["beat_f"] for r in records if r["four_on_the_floor"]]
    tp = sum(r["drops"][0] for r in records)
    fp = sum(r["drops"][1] for r in records)
    fn = sum(r["drops"][2] for r in records)
    edge = [h for r in records for h in r["edge_hits"]]
    b_conf = [c for r in records for c in r["boundary_conf"]]
    b_ok = [c for r in records for c in r["boundary_correct"]]
    out = {
        "tracks": float(n),
        "bpm_within_2pct_fraction": _mean([float(r["bpm_octave_ok"]) for r in records]),
        "bpm_exact_octave_fraction": _mean([float(r["bpm_exact_ok"]) for r in records]),
        "beat_f_four_on_the_floor": _mean(four),
        "beat_f_overall": _mean([r["beat_f"] for r in records]),
        "boundary_f_3s": _mean([r["boundary_f"] for r in records]),
        "boundary_f_1_5s_reported": _mean([r["boundary_f_report"] for r in records]),
        "edge_within_0_25s_fraction": _mean([float(h) for h in edge]),
        "drop_precision": tp / (tp + fp) if tp + fp else 1.0,
        "drop_recall": tp / (tp + fn) if tp + fn else 1.0,
        "ece_bpm": m.ece(
            [r["bpm_confidence"] for r in records], [r["bpm_exact_ok"] for r in records]
        ),
        "ece_boundaries": m.ece(b_conf, b_ok),
    }
    return {k: round(v, 4) for k, v in out.items()}


def worst_tracks(
    records: Sequence[Dict[str, Any]], key: str, limit: int = 5
) -> List[Dict[str, Optional[Any]]]:
    ranked = sorted(records, key=lambda r: r[key])[:limit]
    return [{"id": r["id"], key: round(float(r[key]), 3)} for r in ranked]
