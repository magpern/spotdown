"""Standalone v1 analysis (``GET /api/v1/analyze/{track_id}``) of a converted track.

The core analyser (``app.core``) produces beat/tempo, energy, drops, build-ups and segments on the
file's own clock; the alignment layer (``app.alignment_calc``) compares that clock with Spotify's
duration. This module formats both into the v1 response; the service mode does not use it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import librosa

from .alignment_calc import ALIGN_TOLERANCE_S, offset_hypotheses
from .core.analyser import ANALYZER_VERSION, SR, analyse_signal
from .core.signal_calc import tempo_candidates

__all__ = ["ALIGN_TOLERANCE_S", "ANALYZER_VERSION", "analyze_track"]


def analyze_track(file_path: Path, track_id: str, spotify_duration_s: Optional[int] = None) -> dict:
    y, sr = librosa.load(str(file_path), sr=SR, mono=True)
    core = analyse_signal(y, int(sr))
    duration = core["duration_s"]
    content_start = core["content_start_s"]
    content_end = core["content_end_s"]
    content_duration = max(content_end - content_start, 0.0)
    trailing_silence = max(duration - content_end, 0.0)
    segments = core["segments"]
    drops = core["drops"]
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
    }
