"""Scores downbeats and the meter against the truth of the arrangement (pure).

Downbeats are scored like beats (one-to-one within the beat tolerance, the first half second
trimmed). Counts are returned per track so the corpus pools them per corpus (baseline 4/4 and the
3/4 meter corpus apart, never one number): precision and recall come from the pooled hits. The
meter is right when the document claims exactly the true beats per bar; no claim is a miss.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

from . import metrics_calc as m


def estimated(doc: Dict[str, Any]) -> Dict[str, Any]:
    """``{"times", "confidence", "beats_per_bar", "meter_confidence"}`` (None when not claimed)."""
    grid = doc.get("grid", {})
    down = grid.get("downbeats") or {}
    meter = grid.get("meter") or {}
    return {
        "times": list(down.get("times_s", [])),
        "confidence": down.get("confidence", 0.0),
        "beats_per_bar": meter.get("beats_per_bar"),
        "meter_confidence": meter.get("confidence", 0.0),
    }


def score(truth: Dict[str, Any], doc: Dict[str, Any], tol_s: float) -> Dict[str, Any]:
    est = estimated(doc)
    ref = [t for t in truth["downbeats"] if t >= m.BEAT_TRIM_S]
    got = [t for t in est["times"] if t >= m.BEAT_TRIM_S]
    hits = m.match_count(ref, got, tol_s)
    outcomes = [
        (est["confidence"], any(abs(t - r) <= tol_s for r in ref)) for t in got
    ]  # each claimed downbeat with the list's confidence
    return {
        "downbeat_tp": hits,
        "downbeat_est": len(got),
        "downbeat_truth": len(ref),
        "downbeat_outcomes": outcomes,
        "meter_true": truth["beats_per_bar"],
        "meter_ok": est["beats_per_bar"] == truth["beats_per_bar"],
    }


def pooled(records: Sequence[Dict[str, Any]]) -> Dict[str, float]:
    """Precision, recall, F and one-sided overconfidence pooled over ``records``."""
    tp = sum(r["downbeat_tp"] for r in records)
    n_est = sum(r["downbeat_est"] for r in records)
    n_ref = sum(r["downbeat_truth"] for r in records)
    prec = tp / n_est if n_est else (1.0 if not n_ref else 0.0)
    rec = tp / n_ref if n_ref else 1.0
    out: List[Any] = [o for r in records for o in r["downbeat_outcomes"]]
    return {
        "precision": prec,
        "recall": rec,
        "f": m.f_measure(prec, rec),
        "overconfidence": m.ece([c for c, _ in out], [ok for _, ok in out], over_only=True),
    }


def meter_accuracy(records: Sequence[Dict[str, Any]], beats_per_bar: int) -> float:
    """The share of the tracks of one true meter whose estimated meter is right (1.0: none)."""
    of = [r for r in records if r["meter_true"] == beats_per_bar]
    return sum(1 for r in of if r["meter_ok"]) / len(of) if of else 1.0
