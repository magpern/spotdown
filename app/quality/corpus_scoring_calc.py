"""Scores one track of an external corpus and pools a corpus (pure).

Both the spotdown document and a baseline entry are first reduced to the same *estimate*
(``estimate_from_doc``, ``estimate_from_baseline``), so one scoring function judges both with the
same metrics and tolerances. A metric is computed only for a track that annotates what it needs;
the others carry ``None`` and never enter a pool. Every pool reports how many tracks it is over
(``n_<metric>``), so a metric resting on three tracks says so.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import metrics_calc as m

Estimate = Dict[str, Any]
Timeline = List[Tuple[float, float]]


def _timeline(doc: Dict[str, Any]) -> Timeline:
    tempo = doc["tempo"]
    changes = sorted((float(c["time_s"]), float(c["bpm"])) for c in tempo.get("change_points", []))
    return [(0.0, float(tempo["bpm"])), *changes]


def warning_count(doc: Dict[str, Any], min_bpm_confidence: float) -> int:
    """Quality warnings from the document itself: a doubtful tempo, no beat grid, or music that
    covers less than half of the file. (classbeat's own quality report is not available here.)"""
    sig = doc["signal"]
    content = sig["content_end_s"] - sig["content_start_s"]
    return (
        int(doc["tempo"]["confidence"] < min_bpm_confidence)
        + int(len(doc["grid"]["beats"]["times_s"]) < 4)
        + int(content < 0.5 * sig["duration_s"])
    )


def estimate_from_doc(doc: Dict[str, Any], tol: Dict[str, float]) -> Estimate:
    sig = doc["signal"]
    bounds = [
        (float(s["start_s"]), float(s["boundary_confidence"]))
        for s in doc["structure"]["segments"][1:]
    ]
    edges = [sig["content_start_s"], sig["content_end_s"]]
    if sig.get("fade_in"):
        edges.append(sig["fade_in"]["end_s"])
    if sig.get("fade_out"):
        edges.append(sig["fade_out"]["start_s"])
    return {
        "duration_s": float(sig["duration_s"]),
        "bpm": float(doc["tempo"]["bpm"]),
        "bpm_confidence": float(doc["tempo"]["confidence"]),
        "beats": [float(t) for t in doc["grid"]["beats"]["times_s"]],
        "boundaries": bounds,
        "drops": [float(e["start_s"]) for e in doc["events"] if e["kind"] == "drop"],
        "edges": [float(t) for t in edges],
        "marks": [t for t, _ in bounds] + [sig["content_start_s"], sig["content_end_s"]],
        "timeline": _timeline(doc),
        "warnings": warning_count(doc, tol["warn_bpm_confidence"]),
    }


def estimate_from_baseline(entry: Dict[str, Any]) -> Estimate:
    """A baseline entry (derived results of today's path) as an estimate; absent fields stay
    absent so they are never compared."""
    songs = entry.get("songs") or []
    timeline = sorted((float(s["start_s"]), float(s["bpm"])) for s in songs)
    if not timeline and entry.get("bpm"):
        timeline = [(0.0, float(entry["bpm"]))]
    return {
        "duration_s": None,
        "bpm": entry.get("bpm"),
        "bpm_confidence": None,
        "beats": entry.get("beats"),
        "boundaries": [(float(t), None) for t in entry["boundaries"]]
        if "boundaries" in entry
        else None,
        "drops": entry.get("drops"),
        "edges": entry.get("edges"),
        "marks": [t for s in songs for t in (s["start_s"], s["end_s"])] if songs else None,
        "timeline": timeline or None,
        "warnings": entry.get("warnings"),
    }


def bpm_at(timeline: Timeline, t: float) -> float:
    current = timeline[0][1]
    for start, bpm in timeline:
        if start <= t:
            current = bpm
    return current


def score_track(truth: Dict[str, Any], est: Estimate, tol: Dict[str, float]) -> Dict[str, Any]:
    """One record; every metric is None where truth or estimate lacks what it needs."""
    dur = truth.get("duration_s") or est.get("duration_s")
    ref_bpm = truth.get("bpm") or est.get("bpm")
    rec: Dict[str, Any] = {"four_on_the_floor": bool(truth.get("four_on_the_floor"))}
    if "bpm" in truth and est.get("bpm"):
        octave, exact = m.bpm_errors(truth["bpm"], est["bpm"], tol["bpm_tolerance"])
        rec.update(bpm_octave_ok=octave, bpm_exact_ok=exact, bpm_confidence=est["bpm_confidence"])
    if "beats" in truth and est.get("beats") is not None:
        rec["beat_f"] = m.beat_f(truth["beats"], est["beats"], tol["beat_tol_s"])
    if "boundaries" in truth and est.get("boundaries") is not None and dur:
        times = [t for t, _ in est["boundaries"]]
        rec["boundary_f"] = m.boundary_f(truth["boundaries"], times, dur, tol["boundary_tol_s"])
        rec["boundary_conf"] = [c for _, c in est["boundaries"] if c is not None]
        rec["boundary_correct"] = [
            any(abs(t - r) <= tol["boundary_tol_s"] for r in truth["boundaries"])
            for t, c in est["boundaries"]
            if c is not None
        ]
    if "edges" in truth and est.get("edges") is not None:
        rec["edge_hits"] = m.edge_hits(truth["edges"], est["edges"], tol["edge_tol_s"])
    if "drops" in truth and est.get("drops") is not None and ref_bpm:
        beat_s = 60.0 / ref_bpm
        rec["drops"] = list(
            m.drop_counts(truth["drops"], est["drops"], tol["drop_tol_beats"] * beat_s)
        )
    if "songs" in truth and est.get("marks") is not None and est.get("timeline"):
        rec.update(_songs(truth["songs"], est, tol["song_tol_s"], tol["bpm_tolerance"]))
    if est.get("warnings") is not None:
        rec["warned"] = est["warnings"] > 0
    return rec


def _songs(
    songs: Sequence[Dict[str, float]], est: Estimate, tol_s: float, bpm_tol: float
) -> Dict[str, Any]:
    marks = est["marks"]
    ends = [t for s in songs for t in (s["start_s"], s["end_s"])]
    return {
        "song_edge_hits": [any(abs(t - mk) <= tol_s for mk in marks) for t in ends],
        "song_bpm_hits": [
            m.bpm_errors(
                s["bpm"], bpm_at(est["timeline"], (s["start_s"] + s["end_s"]) / 2), bpm_tol
            )[0]
            for s in songs
        ],
    }


def _pool(records: Sequence[Dict[str, Any]], key: str) -> List[Any]:
    return [r[key] for r in records if r.get(key) is not None]


def _frac(values: Sequence[Any]) -> Optional[float]:
    return sum(float(v) for v in values) / len(values) if values else None


def aggregate(records: Sequence[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    """Corpus metrics named as the gates file names them; None where nothing was annotated."""
    four = [r["beat_f"] for r in records if r["four_on_the_floor"] and "beat_f" in r]
    edge = [h for r in records for h in r.get("edge_hits", [])]
    drops = _pool(records, "drops")
    tp, fp, fn = (sum(d[i] for d in drops) for i in range(3)) if drops else (0, 0, 0)
    bpm = [r for r in records if "bpm_octave_ok" in r]
    rated = [r for r in bpm if r.get("bpm_confidence") is not None]
    conf = [c for r in records for c in r.get("boundary_conf", [])]
    ok = [c for r in records for c in r.get("boundary_correct", [])]
    song_edges = [h for r in records for h in r.get("song_edge_hits", [])]
    song_bpm = [h for r in records for h in r.get("song_bpm_hits", [])]
    out: Dict[str, Optional[float]] = {
        "bpm_within_2pct_fraction": _frac([r["bpm_octave_ok"] for r in bpm]),
        "bpm_exact_octave_fraction": _frac([r["bpm_exact_ok"] for r in bpm]),
        "beat_f_four_on_the_floor": _frac(four),
        "beat_f_overall": _frac(_pool(records, "beat_f")),
        "boundary_f_3s": _frac(_pool(records, "boundary_f")),
        "edge_within_0_25s_fraction": _frac(edge),
        # Nothing to find and nothing claimed is not a measurement: no truth drop, no recall.
        "drop_precision": tp / (tp + fp) if tp + fp else (1.0 if tp + fn else None),
        "drop_recall": tp / (tp + fn) if tp + fn else None,
        "ece_bpm": m.ece([r["bpm_confidence"] for r in rated], [r["bpm_exact_ok"] for r in rated])
        if rated
        else None,
        "ece_boundaries": m.ece(conf, ok) if conf else None,
        "song_start_end_within_2s_fraction": _frac(song_edges),
        "song_bpm_within_2pct_fraction": _frac(song_bpm),
        "warning_fraction": _frac(_pool(records, "warned")),
    }
    counts = {
        "bpm_within_2pct_fraction": len(bpm),
        "beat_f_four_on_the_floor": len(four),
        "beat_f_overall": len(_pool(records, "beat_f")),
        "boundary_f_3s": len(_pool(records, "boundary_f")),
        "edge_within_0_25s_fraction": len(edge),
        "drop_recall": tp + fn,
        "ece_bpm": len(rated),
        "ece_boundaries": len(conf),
        "song_start_end_within_2s_fraction": len(song_edges),
        "song_bpm_within_2pct_fraction": len(song_bpm),
        "warning_fraction": len(_pool(records, "warned")),
    }
    out.update({f"n_{k}": float(v) for k, v in counts.items()})
    return {k: (round(v, 4) if v is not None else None) for k, v in out.items()}
