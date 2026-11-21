"""Section labels from the segment structure: intro, verse, chorus, bridge, outro, breakdown (pure).

The labels are a heuristic, never a measurement: they read the segments the structure analysis
found (energy, repetition group, position) and the detected drops, nothing else. The rules:

* **chorus**: a segment that starts at a drop, or that belongs to the chorus group (and is not
  much quieter than the loudest segment). The chorus group is the repetition group with the
  highest mean energy when it is clearly louder than the other middle segments (``PEAK_MARGIN``).
  When the energy barely varies (a steady track) the loudest group is no evidence, and the
  fallback is repetition: the single most repeated group, if it covers at most half of the content
  time (``REPEAT_MAX_SHARE``); with a tie, or a group that is most of the track, there is no
  chorus group and only drops start choruses, so a track is never mostly chorus.
* **intro** and **outro**: the first and the last content segment when they are not a chorus.
* **breakdown**: a middle segment far quieter than the chorus level.
* **bridge**: a middle segment straight after a chorus, with another chorus still to come.
* **verse**: every other middle segment (including a build into a drop, the pre-chorus).

Every label carries a confidence from how many independent cues agree. The rules and the synthetic
truth labels were written by the same author on the same three section templates, so the label
gates (``label_accuracy``, ``label_overconfidence``) are regression checks, not a measurement of
label quality. ``GATE_PASSED`` therefore stays False and no confidence exceeds ``UNGATED_CAP``
until held-out evidence exists (CC-licensed fixtures or the owner corpus, spotdown#20); a consumer
that only trusts confident features then ignores the labels.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

LABELS = ("intro", "verse", "chorus", "bridge", "outro", "breakdown")

# Stays False until held-out evidence exists (spotdown#20): the synthetic corpus is the corpus the
# rules were written on, so passing label_accuracy and label_overconfidence there does not unlock
# a higher confidence. tests/test_label_calc.py pins it to False.
GATE_PASSED = False
UNGATED_CAP = 0.4  # the most confidence a label carries before its gate passes
GATED_CAP = 0.75  # the most a label may claim once held-out evidence exists (not used yet)
DROP_SNAP_S = 1.0  # a segment this close to a drop starts at it
BREAKDOWN_RATIO = 0.25  # a middle segment this much below the chorus level is a breakdown
PEAK_GROUP_RATIO = 0.8  # a member of the peak group at least this loud is a chorus
# The peak group must be this much louder (0..1 scale) than the other middle segments:
PEAK_MARGIN = 0.1
# The repetition fallback is no chorus group when it covers more of the content time than this:
REPEAT_MAX_SHARE = 0.5
SHORT_SEGMENT_S = 4.0  # shorter segments carry less evidence

# Rule confidence before the cap: deliberately conservative priors (more cues agreeing, more
# confidence), not measured hit rates; the in-sample accuracy is higher (about 0.9). Held-out
# evidence recalibrates them. See docs/quality-harness.md.
BASE_CONFIDENCE = {
    "chorus": 0.8,
    "intro": 0.7,
    "outro": 0.7,
    "breakdown": 0.7,
    "bridge": 0.5,
    "verse": 0.5,
}


def content_indices(
    segments: Sequence[Dict[str, Any]], content_start: float, content_end: float
) -> List[int]:
    """Segments at least half inside the content range (silence at the file's ends is not music)."""
    found = []
    for i, s in enumerate(segments):
        length = s["end"] - s["start"]
        inside = min(s["end"], content_end) - max(s["start"], content_start)
        if length > 0 and inside >= 0.5 * length:
            found.append(i)
    return found


def _peak_group(segments: Sequence[Dict[str, Any]], idx: Sequence[int]) -> Optional[int]:
    """The repetition group with the highest mean energy among groups that repeat."""
    by_group: Dict[int, List[float]] = {}
    for i in idx:
        by_group.setdefault(segments[i].get("group", 0), []).append(segments[i]["energy_struct"])
    repeated = {g: sum(v) / len(v) for g, v in by_group.items() if len(v) >= 2}
    return max(repeated, key=lambda g: repeated[g]) if repeated else None


def _clear_peak(segments: Sequence[Dict[str, Any]], idx: Sequence[int]) -> Optional[int]:
    """The peak group when it is louder than the other middle segments by ``PEAK_MARGIN``.

    The first and the last content segment are left out of the comparison (they are the intro and
    the outro unless they are choruses); with no other middle segment there is nothing to
    compare with and no clear peak."""
    peak = _peak_group(segments, idx)
    if peak is None:
        return None
    members = [segments[i]["energy_struct"] for i in idx if segments[i].get("group", 0) == peak]
    others = [
        segments[i]["energy_struct"] for i in idx[1:-1] if segments[i].get("group", 0) != peak
    ]
    if not others or sum(members) / len(members) - sum(others) / len(others) < PEAK_MARGIN:
        return None
    return peak


def _repeated_group(segments: Sequence[Dict[str, Any]], idx: Sequence[int]) -> Optional[int]:
    """The one group with the most members (at least two), when it covers at most
    ``REPEAT_MAX_SHARE`` of the content time; ``None`` on a tie or when it is most of the track."""
    count: Dict[int, int] = {}
    span: Dict[int, float] = {}
    for i in idx:
        g = segments[i].get("group", 0)
        count[g] = count.get(g, 0) + 1
        span[g] = span.get(g, 0.0) + segments[i]["end"] - segments[i]["start"]
    most = max(count.values())
    best = [g for g, n in count.items() if n == most]
    total = sum(span.values())
    if most < 2 or len(best) != 1 or span[best[0]] > REPEAT_MAX_SHARE * total:
        return None
    return best[0]


def _chorus_group(segments: Sequence[Dict[str, Any]], idx: Sequence[int]) -> Optional[int]:
    """The loudest repeating group when clearly louder, else the repetition fallback."""
    peak = _clear_peak(segments, idx)
    return peak if peak is not None else _repeated_group(segments, idx)


def _choruses(
    segments: Sequence[Dict[str, Any]], idx: Sequence[int], drops: Sequence[float]
) -> Dict[int, float]:
    """``{segment index: confidence}`` for every chorus."""
    peak = _chorus_group(segments, idx)
    loudest = max((segments[i]["energy_struct"] for i in idx), default=0.0)
    found: Dict[int, float] = {}
    for i in idx:
        s = segments[i]
        at_drop = any(abs(s["start"] - d) <= DROP_SNAP_S for d in drops)
        in_peak = peak is not None and s.get("group", 0) == peak
        loud = s["energy_struct"] >= PEAK_GROUP_RATIO * loudest
        if at_drop and in_peak:
            found[i] = BASE_CONFIDENCE["chorus"]
        elif at_drop or (in_peak and loud):
            found[i] = BASE_CONFIDENCE["chorus"] - 0.2
    return found


def label_segments(
    segments: Sequence[Dict[str, Any]],
    drops: Sequence[float],
    content_start: float,
    content_end: float,
) -> List[Dict[str, Any]]:
    """``[{"index", "label", "confidence"}]`` for the segments inside the content range.

    A single content segment has no structure to label and gives nothing.
    """
    idx = content_indices(segments, content_start, content_end)
    if len(idx) < 2:
        return []
    choruses = _choruses(segments, idx, drops)
    level = (
        sum(segments[i]["energy_struct"] for i in choruses) / len(choruses)
        if choruses
        else max(segments[i]["energy_struct"] for i in idx)
    )
    out: List[Dict[str, Any]] = []
    for pos, i in enumerate(idx):
        seg = segments[i]
        if i in choruses:
            label, conf = "chorus", choruses[i]
        elif pos == 0:
            label, conf = "intro", BASE_CONFIDENCE["intro"]
        elif pos == len(idx) - 1:
            label, conf = "outro", BASE_CONFIDENCE["outro"]
        elif seg["energy_struct"] <= BREAKDOWN_RATIO * level:
            label, conf = "breakdown", BASE_CONFIDENCE["breakdown"]
        elif idx[pos - 1] in choruses and any(j in choruses for j in idx[pos + 1 :]):
            label, conf = "bridge", BASE_CONFIDENCE["bridge"]
        else:
            label, conf = "verse", BASE_CONFIDENCE["verse"]
        if seg["end"] - seg["start"] < SHORT_SEGMENT_S:
            conf *= 0.8
        cap = GATED_CAP if GATE_PASSED else UNGATED_CAP
        out.append({"index": i, "label": label, "confidence": round(min(conf, cap), 3)})
    return out
