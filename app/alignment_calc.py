"""Alignment layer (pure): how a file's clock maps to a reference clock by a simple offset.

The core analyser never sees a reference; this layer compares its file-clock result with a
reference duration. "spotify" is one reference kind among others.
"""

from __future__ import annotations

from typing import Optional, Tuple

# A reference may trim edge silence; if the music content matches within this many seconds the
# timeline is treated as alignable by a simple offset.
ALIGN_TOLERANCE_S = 1.5


def offset_hypotheses(
    file_duration_s: float,
    content_start_s: float,
    content_duration_s: float,
    reference_duration_s: Optional[float],
) -> Tuple[Optional[bool], float, Optional[float]]:
    """Return ``(matches, offset_to_subtract, residual)`` for a reference duration.

    Two hypotheses: the file length already matches the reference (offset 0), or only the
    trimmed content does (offset = leading silence). ``matches`` is None without a reference.
    """
    if not reference_duration_s:
        return None, content_start_s, None
    delta_file = file_duration_s - reference_duration_s
    delta_content = content_duration_s - reference_duration_s
    if abs(delta_file) <= ALIGN_TOLERANCE_S:
        return True, 0.0, delta_file
    if abs(delta_content) <= ALIGN_TOLERANCE_S:
        return True, content_start_s, delta_content
    return False, content_start_s, delta_content
