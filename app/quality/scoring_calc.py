"""Scores one analysis-v1 document against one truth record, and aggregates a corpus (pure).

A document is read through the public contract only (``tempo``, ``grid``, ``structure``,
``events``, ``signal``), so the same scoring applies to any analyser that emits analysis-v1.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from . import labels_calc as lab
from . import metrics_calc as m
from . import phrases_calc as ph


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
    spans = lab.estimated_spans(doc)
    c0, c1 = truth["content_start_s"], truth["content_end_s"]
    phrase_hits = ph.match(truth["phrases"], ph.estimated(doc), tol["phrase_tol_beats"] * beat_s)
    return {
        "id": truth.get("id"),
        "corpus": truth.get("corpus", "baseline"),
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
        "label_frames": list(lab.frame_counts(truth["labels"], spans, c0, c1)),
        "label_segments": lab.segment_outcomes(truth["labels"], spans, c0, c1),
        "phrase_hits": phrase_hits,
        "phrase_truth": len(truth["phrases"]),
    }


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def aggregate(records: Sequence[Dict[str, Any]]) -> Dict[str, float]:
    """Corpus-level metrics, keyed as the ``quality_gates`` file names them.

    The plan section 7 gates and the label gates are measured on the baseline fixtures only; the
    fixtures of an enhancement corpus (``corpus`` other than ``baseline``, for example the phrase
    fixtures) enter only their own gate, so adding them never moves a baseline floor."""
    everything = records
    n = len(everything)
    records = [r for r in everything if r["corpus"] == "baseline"]
    four = [r["beat_f"] for r in records if r["four_on_the_floor"]]
    tp = sum(r["drops"][0] for r in records)
    fp = sum(r["drops"][1] for r in records)
    fn = sum(r["drops"][2] for r in records)
    edge = [h for r in records for h in r["edge_hits"]]
    b_conf = [c for r in records for c in r["boundary_conf"]]
    b_ok = [c for r in records for c in r["boundary_correct"]]
    frames = [r["label_frames"] for r in records]
    segs = [o for r in records for o in r["label_segments"]]
    p_out = [o for r in everything for o in r["phrase_hits"]]
    p_tp = sum(1 for _, ok in p_out if ok)
    p_truth = sum(r["phrase_truth"] for r in everything)
    p_prec = p_tp / len(p_out) if p_out else 1.0
    p_rec = p_tp / p_truth if p_truth else 1.0
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
        "label_accuracy": _mean([hit / total for hit, total in frames if total]),
        "ece_labels_reported": m.ece([c for c, _ in segs], [ok for _, ok in segs]),
        "phrase_precision": p_prec,
        "phrase_recall": p_rec,
        "phrase_f": m.f_measure(p_prec, p_rec),
        "phrase_overconfidence": m.ece(
            [c for c, _ in p_out], [ok for _, ok in p_out], over_only=True
        ),
        "label_overconfidence": m.ece([c for c, _ in segs], [ok for _, ok in segs], over_only=True),
    }
    return {k: round(v, 4) for k, v in out.items()}


def worst_tracks(
    records: Sequence[Dict[str, Any]], key: str, limit: int = 5
) -> List[Dict[str, Optional[Any]]]:
    ranked = sorted(records, key=lambda r: r[key])[:limit]
    return [{"id": r["id"], key: round(float(r[key]), 3)} for r in ranked]
