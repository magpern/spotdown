"""Standalone v1 analysis (``GET /api/v1/analyze/{track_id}``) of a converted track.

The core analyser (``app.core``) produces beat/tempo, energy, drops, build-ups and segments on the
file's own clock; the alignment layer (``app.alignment_calc``) compares that clock with Spotify's
duration. This module formats both into the v1 response; the service mode does not use it.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import librosa

from .alignment_calc import ALIGN_TOLERANCE_S, offset_hypotheses
from .core import label_calc
from .core.analyser import ANALYZER_VERSION, SR, analyse_signal
from .core.signal_calc import tempo_candidates
from .standalone_doc import standalone_document

__all__ = [
    "ALIGN_TOLERANCE_S",
    "ANALYZER_VERSION",
    "V1_RESPONSE_VERSION",
    "analyze_track",
    "v1_response",
]

# Version of the standalone v1 response, next to the core ANALYZER_VERSION in the cache key
# (ADR 0001, Decision 2). Rows cached before it existed count as 1. Raise it with any change to the
# shape or the labels of this response; it never touches the v2 caches or analysis-v1.
V1_RESPONSE_VERSION = 2


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fill_labels(segments: list, core: dict) -> None:
    """Set ``segments[].label`` from ``label_calc``, the labeller of the document's ``labels``.

    A segment it leaves unlabelled (silence around the content, or a single content segment)
    reads ``unlabelled``, the same word the web UI uses for a segment without a label.
    """
    found = {
        x["index"]: x["label"]
        for x in label_calc.label_segments(
            segments, core["drops"], core["content_start_s"], core["content_end_s"]
        )
    }
    for i, seg in enumerate(segments):
        seg["label"] = found.get(i, "unlabelled")


def analyze_track(file_path: Path, track_id: str, spotify_duration_s: Optional[int] = None) -> dict:
    y, sr = librosa.load(str(file_path), sr=SR, mono=True)
    core = analyse_signal(y, int(sr))
    return v1_response(
        core,
        track_id=track_id,
        sha256=_sha256(file_path),
        codec=file_path.suffix.lstrip(".").lower() or "unknown",
        sample_rate_hz=int(sr),
        spotify_duration_s=spotify_duration_s,
    )


def v1_response(
    core: dict,
    *,
    track_id: str,
    sha256: str,
    codec: str,
    sample_rate_hz: int,
    spotify_duration_s: Optional[int],
) -> dict:
    """The v1 response of a core-analyser result on a signal at ``sample_rate_hz`` (also used by
    the standalone local-file route, whose core result comes from the sandbox)."""
    sr = sample_rate_hz
    duration = core["duration_s"]
    content_start = core["content_start_s"]
    content_end = core["content_end_s"]
    content_duration = max(content_end - content_start, 0.0)
    trailing_silence = max(duration - content_end, 0.0)
    segments = core["segments"]
    drops = core["drops"]
    fill_labels(segments, core)
    energy = core["energy"]

    matches, align_offset, residual = offset_hypotheses(
        duration, content_start, content_duration, spotify_duration_s
    )

    # Spotify-clock cue points / segment edges — only meaningful when aligned.
    drops_spotify = [round(max(d - align_offset, 0.0), 3) for d in drops] if matches else None
    if matches:
        for s in segments:
            s["start_spotify"] = round(max(s["start"] - align_offset, 0.0), 3)
            s["end_spotify"] = round(max(s["end"] - align_offset, 0.0), 3)

    energy_curve = [
        {"t": t, "full": a, "percussive": b, "low": c}
        for t, a, b, c in zip(energy["t"], energy["full"], energy["percussive"], energy["low"])
    ]
    bpm = core["bpm"]
    document = standalone_document(
        core,
        sha256=sha256,
        codec=codec,
        sample_rate_hz=sr,
        track_id=track_id,
        spotify_duration_s=spotify_duration_s,
        created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    return {
        "track_id": track_id,
        "analyzer_version": ANALYZER_VERSION,
        "sr": sr,
        "meter_assumed": f"{core['meter']['beats_per_bar']}/4" if core.get("meter") else "4/4",
        "label_method": "heuristic",
        "analyzed_duration_s": round(duration, 3),
        "spotify_duration_s": spotify_duration_s,
        "duration_delta_s": round(duration - spotify_duration_s, 3) if spotify_duration_s else None,
        "content_start_s": round(content_start, 3),
        "content_end_s": round(content_end, 3),
        "content_duration_s": round(content_duration, 3),
        "leading_silence_s": round(content_start, 3),
        "trailing_silence_s": round(trailing_silence, 3),
        # Subtract this from any file timestamp to get the Spotify-playback time.
        "spotify_offset_s": round(align_offset, 3),
        "timeline_residual_s": round(residual, 3) if residual is not None else None,
        "timeline_matches_spotify": matches,
        "drops_spotify": drops_spotify,
        "segments_aligned_to_spotify": bool(matches),
        "bpm": round(bpm, 1),
        "bpm_candidates": tempo_candidates(bpm),
        "downbeat_phase": core["downbeat_phase"],
        "beat_count": len(core["beat_times"]),
        "beat_grid": core["beat_times"],
        "bars": core["bars"],
        "drops": drops,
        "build_ups": core["build_ups"],
        "energy_curve": energy_curve,
        "segments": segments,
        # The same analysis as an analysis-v1 document (the web UI's detailed view); absent in
        # analyses cached before it existed, and null when the builder found no tempo.
        "document": document,
    }
