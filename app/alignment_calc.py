"""Alignment layer (pure): how a file's clock maps to a reference clock by a simple offset.

The core analyser never sees a reference; this layer compares its file-clock result with a
reference duration. "spotify" is one reference kind among others (``align``, spotdown#11).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

# ---- alignment (spotdown#11) -------------------------------------------------------------------
# Sign convention (docs/contract-v2.md): ``reference_time = file_time + offset_s``. A residual is
# how far the file's end misses the reference's end once the offset is applied.

ALIGNMENT_VERSION = "1"
# Above this residual no hypothesis maps the file onto the reference: the alignment failed. It
# mirrors the 5 s duration rejection of the matcher; between the caller's own tolerance and this
# bound spotdown still reports the evidence, and the caller decides (spotdown never emits a
# timing class).
MAX_RESIDUAL_S = 5.0
# Two hypotheses with offsets this close are the same mapping, so they never compete.
SAME_OFFSET_S = 0.05
# A runner-up within this many seconds of the best residual makes the choice ambiguous.
AMBIGUITY_S = 1.0


@dataclass(frozen=True)
class Hypothesis:
    name: str  # "untrimmed" (the file as decoded) or "trimmed" (the silence-trimmed content)
    offset_s: float
    residual_s: float


@dataclass(frozen=True)
class Alignment:
    hypotheses: Tuple[Hypothesis, ...]
    chosen: Optional[Hypothesis]  # None when the alignment failed
    confidence: Optional[float]

    @property
    def ok(self) -> bool:
        return self.chosen is not None


def _r(x: float) -> float:
    return round(float(x), 3) + 0.0  # + 0.0 turns -0.0 into 0.0


def hypotheses(
    file_duration_s: float, content_start_s: float, content_end_s: float, reference_s: float
) -> Tuple[Hypothesis, ...]:
    """The two mappings tried, untrimmed first: the reference starts at the file's start
    (offset 0), or at the content's start, its leading silence trimmed (offset -content_start)."""
    content = max(content_end_s - content_start_s, 0.0)
    return (
        Hypothesis("untrimmed", 0.0, _r(abs(file_duration_s - reference_s))),
        Hypothesis("trimmed", _r(-content_start_s), _r(abs(content - reference_s))),
    )


def confidence(best: Hypothesis, others: Sequence[Hypothesis]) -> float:
    """A first, documented estimate (calibrated later by the quality harness, spotdown#6): the
    fit falls linearly from 1 at a zero residual to 0 at ``MAX_RESIDUAL_S``, and is reduced
    when a different mapping fits almost as well."""
    fit = max(0.0, 1.0 - best.residual_s / MAX_RESIDUAL_S)
    rivals = [h for h in others if abs(h.offset_s - best.offset_s) > SAME_OFFSET_S]
    if rivals:
        gap = min(h.residual_s for h in rivals) - best.residual_s
        fit *= 0.5 + 0.5 * min(1.0, max(0.0, gap) / AMBIGUITY_S)
    return round(min(1.0, max(0.0, fit)), 3)


def align(
    file_duration_s: float, content_start_s: float, content_end_s: float, reference_s: float
) -> Alignment:
    """Chooses the hypothesis with the smallest residual (the untrimmed one on a tie). A
    reference without a positive duration, or a best residual above ``MAX_RESIDUAL_S``, fails."""
    tried = hypotheses(file_duration_s, content_start_s, content_end_s, reference_s)
    if not reference_s > 0:
        return Alignment(tried, None, None)
    best = min(tried, key=lambda h: h.residual_s)  # min keeps the first on a tie
    if best.residual_s > MAX_RESIDUAL_S:
        return Alignment(tried, None, None)
    return Alignment(tried, best, confidence(best, [h for h in tried if h is not best]))
