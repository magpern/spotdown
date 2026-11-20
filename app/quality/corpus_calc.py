"""The external corpus format and its checks (pure): what a corpus index and a truth record may say.

A corpus is one directory per source (``cc``, ``spotify``, ``studio``, ``mix``) with a
``corpus.json`` index and the audio beside it. The audio stays on the machine that has it; the
index holds only relative file names, the annotated truth and, for CC tracks, provenance. Truth uses
the shape of the synthetic manifest: ``bpm``, ``beats``, ``boundaries``, ``drops``, ``edges``
(seconds
on the file's clock), ``four_on_the_floor`` and, for a mix, ``songs`` (``start_s``, ``end_s``,
``bpm``). A key that is absent is *not annotated* (its metric is not measured for that track); an
empty list is an annotation (there is nothing to find). Unknown keys are refused, so a typo cannot
silently drop an annotation.
"""

from __future__ import annotations

import math
from pathlib import PurePosixPath
from typing import Any, Dict, List, Sequence

CORPUS_VERSION = 1
CORPORA = ("cc", "spotify", "studio", "mix")
LICENCES = frozenset({"CC0-1.0", "CC-BY-3.0", "CC-BY-4.0"})
TIMES = ("beats", "boundaries", "drops", "edges")
TRUTH_KEYS = frozenset({*TIMES, "bpm", "four_on_the_floor", "songs", "duration_s"})
TRACK_KEYS = frozenset(
    {
        "id",
        "audio",
        "truth",
        "licence",
        "provenance",
        "reference",
        "same_recording",
        "true_offset_s",
    }
)


class CorpusError(ValueError):
    """The index or a truth record is malformed; the message names the track and the field."""


def _number(value: Any, where: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise CorpusError(f"{where}: not a finite number")
    if value < 0 or (positive and value == 0):
        raise CorpusError(f"{where}: must be {'above' if positive else 'at least'} 0")
    return float(value)


def validate_truth(truth: Dict[str, Any], where: str) -> Dict[str, Any]:
    """The truth record with every time list sorted and numbers as floats."""
    if not isinstance(truth, dict):
        raise CorpusError(f"{where}: truth must be an object")
    unknown = sorted(set(truth) - TRUTH_KEYS)
    if unknown:
        raise CorpusError(f"{where}: unknown truth keys {unknown}")
    out: Dict[str, Any] = {}
    for key in TIMES:
        if key in truth:
            if not isinstance(truth[key], list):
                raise CorpusError(f"{where}.{key}: must be a list of seconds")
            out[key] = sorted(_number(v, f"{where}.{key}") for v in truth[key])
    for key in ("bpm", "duration_s"):
        if key in truth:
            out[key] = _number(truth[key], f"{where}.{key}", positive=True)
    if "four_on_the_floor" in truth:
        if not isinstance(truth["four_on_the_floor"], bool):
            raise CorpusError(f"{where}.four_on_the_floor: must be true or false")
        out["four_on_the_floor"] = truth["four_on_the_floor"]
    if "songs" in truth:
        songs = truth["songs"]
        if not isinstance(songs, list):
            raise CorpusError(f"{where}.songs: must be a list")
        out["songs"] = [_song(s, f"{where}.songs[{i}]") for i, s in enumerate(songs)]
    if "duration_s" in out:
        late = [t for k in TIMES for t in out.get(k, []) if t > out["duration_s"] + 1.0]
        if late:
            raise CorpusError(f"{where}: a time lies after duration_s")
    return out


def _song(song: Any, where: str) -> Dict[str, float]:
    if not isinstance(song, dict) or set(song) != {"start_s", "end_s", "bpm"}:
        raise CorpusError(f"{where}: needs exactly start_s, end_s and bpm")
    start = _number(song["start_s"], f"{where}.start_s")
    end = _number(song["end_s"], f"{where}.end_s")
    if end <= start:
        raise CorpusError(f"{where}: end_s must be after start_s")
    return {"start_s": start, "end_s": end, "bpm": _number(song["bpm"], where, positive=True)}


def _safe_audio(name: Any, where: str) -> str:
    path = PurePosixPath(str(name).replace("\\", "/"))
    if not name or path.is_absolute() or ".." in path.parts or ":" in str(name):
        raise CorpusError(f"{where}.audio: must be a relative path inside the corpus directory")
    return str(path)


def validate_index(index: Dict[str, Any]) -> Dict[str, Any]:
    """A normalised index (truth validated, ids unique, paths relative), or CorpusError."""
    if index.get("corpus_version") != CORPUS_VERSION:
        raise CorpusError(f"corpus_version must be {CORPUS_VERSION}")
    corpus = index.get("corpus")
    if corpus not in CORPORA:
        raise CorpusError(f"corpus must be one of {list(CORPORA)}")
    tracks: List[Dict[str, Any]] = []
    seen = set()
    for i, raw in enumerate(index.get("tracks", [])):
        tid = raw.get("id")
        where = f"tracks[{i}]({tid})"
        if not isinstance(tid, str) or not tid or tid in seen:
            raise CorpusError(f"{where}: id must be a unique non-empty string")
        seen.add(tid)
        unknown = sorted(set(raw) - TRACK_KEYS)
        if unknown:
            raise CorpusError(f"{where}: unknown keys {unknown}")
        track = dict(raw, audio=_safe_audio(raw.get("audio"), where))
        track["truth"] = validate_truth(raw.get("truth", {}), f"{where}.truth")
        if corpus == "cc":
            _check_cc(track, where)
        if "same_recording" in track and not isinstance(track["same_recording"], bool):
            raise CorpusError(f"{where}.same_recording: must be true or false")
        tracks.append(track)
    return {"corpus_version": CORPUS_VERSION, "corpus": corpus, "tracks": tracks}


def _check_cc(track: Dict[str, Any], where: str) -> None:
    if track.get("licence") not in LICENCES:
        raise CorpusError(f"{where}: licence must be one of {sorted(LICENCES)}")
    prov = track.get("provenance") or {}
    if not all(isinstance(prov.get(k), str) and prov[k] for k in ("title", "artist", "source_url")):
        raise CorpusError(f"{where}: provenance needs title, artist and source_url")


def annotated(truth: Dict[str, Any], keys: Sequence[str]) -> bool:
    """Whether every required annotation is present (an empty list counts)."""
    return all(k in truth for k in keys)


def coverage(
    tracks: Sequence[Dict[str, Any]], rule: Dict[str, Any], measured_ok: Sequence[bool]
) -> Dict[str, Any]:
    """Size and annotation coverage against the corpus rule of the gates file. ``measured_ok``
    says, per track, whether its job produced a document (failed jobs still count as tracks)."""
    keys = rule["annotated_keys"]
    n_annotated = sum(1 for t in tracks if annotated(t["truth"], keys))
    return {
        "tracks": len(tracks),
        "annotated": n_annotated,
        "min_tracks": rule["min_tracks"],
        "min_annotated": rule["min_annotated"],
        "jobs_ok": sum(1 for ok in measured_ok if ok),
        "sufficient": len(tracks) >= rule["min_tracks"] and n_annotated >= rule["min_annotated"],
    }
