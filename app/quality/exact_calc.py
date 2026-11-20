"""The hard gate ``false_exact`` (pure): an ``exact`` claim the case is not entitled to.

classbeat plan section 2: a track is ``exact`` only when the analysed file *is* the played
recording (an upload that is played, a SwedeBeat studio track, the mix that is played), never for a
YouTube match of a Spotify track, however confident. spotdown reports evidence and never emits a
timing class, so the gate counts the claims a document could make and refuses the ones the case
does not earn:

* a claim is the string ``exact`` anywhere in the document (a ``timing_class`` or any other key);
  an ``alignment`` object alone is evidence, not a claim (it was the placeholder's definition);
* the claim is entitled when the corpus is one whose files are the played recording
  (``EXACT_CORPORA``), the truth says the file is the played recording (``same_recording``), and,
  when the document carries an alignment, its offset is within ``OFFSET_TOL_S`` of the true offset
  (the 50 ms of the plan's beat-level equivalence test).

Anything else, including an unknown corpus, is a false exact. Overreach on a reference that is not
the same recording is reported separately: durations cannot tell two recordings apart, so spotdown
never promotes an alignment on its own.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

OFFSET_TOL_S = 0.05
EXACT_CORPORA = frozenset({"baseline", "phrases", "meter", "synthetic", "cc", "studio", "mix"})
OVERREACH_CONFIDENCE = 0.9


def exact_claims(node: Any, path: str = "") -> List[str]:
    """The paths of every ``exact`` value in a document (none today: matching never claims it)."""
    if isinstance(node, dict):
        return [p for k, v in node.items() for p in exact_claims(v, f"{path}/{k}")]
    if isinstance(node, list):
        return [p for i, v in enumerate(node) for p in exact_claims(v, f"{path}/{i}")]
    return [path or "/"] if node == "exact" else []


def offset_error_s(doc: Dict[str, Any], true_offset_s: Optional[float]) -> Optional[float]:
    """|claimed offset - true offset|, or None without an alignment or a true offset."""
    alignment = doc.get("alignment")
    if not alignment or true_offset_s is None:
        return None
    return abs(float(alignment["offset_s"]) - true_offset_s)


def entitled(case: Dict[str, Any]) -> bool:
    """Whether this case may claim ``exact`` (see the module docstring)."""
    if case["corpus"] not in EXACT_CORPORA or case.get("same_recording", True) is not True:
        return False
    err = offset_error_s(case["doc"], case.get("true_offset_s"))
    return err is None or err <= OFFSET_TOL_S


def false_exact(cases: Sequence[Dict[str, Any]]) -> int:
    """Cases whose document claims ``exact`` without being entitled to it."""
    return sum(1 for c in cases if exact_claims(c["doc"]) and not entitled(c))


def alignment_overreach(cases: Sequence[Dict[str, Any]]) -> int:
    """Reported only: ok alignments of a file that is not the reference recording, with a
    confidence a consumer could mistake for identity."""
    return sum(
        1
        for c in cases
        if c.get("same_recording", True) is False
        and (c["doc"].get("alignment") or {}).get("confidence", 0.0) >= OVERREACH_CONFIDENCE
    )


def offset_hits(cases: Sequence[Dict[str, Any]]) -> List[bool]:
    """For each alignment case of the same recording, whether the offset is within the tolerance."""
    out: List[bool] = []
    for c in cases:
        if c.get("same_recording", True) is not True:
            continue
        err = offset_error_s(c["doc"], c.get("true_offset_s"))
        if err is not None:
            out.append(err <= OFFSET_TOL_S)
    return out
