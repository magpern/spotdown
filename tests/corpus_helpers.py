"""Hand-made analysis documents and corpus entries for the external-corpus tests."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

TOL: Dict[str, float] = {
    "bpm_tolerance": 0.02,
    "beat_tol_s": 0.07,
    "boundary_tol_s": 3.0,
    "edge_tol_s": 0.25,
    "drop_tol_beats": 1,
    "song_tol_s": 2.0,
    "warn_bpm_confidence": 0.5,
    "baseline_min_tracks": 5,
}


def make_doc(
    bpm: float = 120.0,
    conf: float = 0.9,
    beats: Optional[Sequence[float]] = None,
    bounds: Sequence[float] = (30.0, 60.0),
    bound_conf: float = 0.8,
    drops: Sequence[float] = (),
    duration: float = 100.0,
    content: Sequence[float] = (0.0, 100.0),
    changes: Sequence[Dict[str, float]] = (),
    alignment: Optional[Dict[str, Any]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    step = 60.0 / bpm
    grid = (
        list(beats)
        if beats is not None
        else [round(i * step, 4) for i in range(int(duration / step))]
    )
    starts = [0.0, *bounds]
    doc: Dict[str, Any] = {
        "signal": {
            "duration_s": duration,
            "content_start_s": content[0],
            "content_end_s": content[1],
            "fade_in": None,
            "fade_out": None,
        },
        "tempo": {"bpm": bpm, "confidence": conf, "change_points": list(changes)},
        "grid": {"beats": {"times_s": grid}},
        "structure": {
            "segments": [
                {"start_s": s, "boundary_confidence": bound_conf if i else 1.0}
                for i, s in enumerate(starts)
            ]
        },
        "events": [{"kind": "drop", "start_s": t} for t in drops],
    }
    if alignment:
        doc["alignment"] = alignment
    doc.update(extra or {})
    return doc


def truth_for(doc_beats: List[float], **more: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "bpm": 120.0,
        "beats": doc_beats,
        "boundaries": [30.0, 60.0],
        "drops": [],
        "edges": [0.0, 100.0],
        "duration_s": 100.0,
    }
    base.update(more)
    return base


def result(doc: Optional[Dict[str, Any]], **more: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "id": "x",
        "ok": doc is not None,
        "doc": doc,
        "audio_left": 0,
        "duration_s": 100.0 if doc else None,
        "work_s": 10.0,
        "wall_s": 11.0,
        "peak_gib": 0.5,
    }
    out.update(more)
    return out
