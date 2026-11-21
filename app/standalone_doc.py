"""The analysis-v1 document of a standalone analysis (the web UI's richer view of it).

The standalone v1 response keeps its flat shape; the document is that same analysis formatted by
the service's own builder (``app.service.analysis_doc``), so phrases, section labels with
confidence, events, tempo stability, fades and the alignment come from one place and never
diverge. Pure: no I/O. Nothing here changes an analysis result or the v2 contract.

The provenance describes the analysed signal (mono, at the analysis rate), not the stored file.
Its source kind is ``youtube`` because the standalone audio is a YouTube match downloaded by the
converter; the video id and the match record are not known here, so neither ``ref`` nor ``match``
is given.
"""

from __future__ import annotations

from importlib.metadata import version
from typing import Any, Dict, Optional

from .service import alignment_doc, analysis_doc

STANDALONE_ID = "an_standalone"


def _components() -> Dict[str, str]:
    out = {}
    for name in ("librosa", "numpy"):
        try:
            out[name] = version(name)
        except Exception:  # noqa: BLE001 - a missing version string never blocks the view
            pass
    return out


def standalone_document(
    core: Dict[str, Any],
    *,
    sha256: str,
    codec: str,
    sample_rate_hz: int,
    track_id: str,
    spotify_duration_s: Optional[float],
    created_at: str,
) -> Optional[Dict[str, Any]]:
    """The document, with ``alignment`` when a reference duration is known; ``None`` when the
    builder refuses (no tempo found), so the flat response is still served."""
    try:
        doc = analysis_doc.build(
            core=core,
            probe={"codec": codec, "sample_rate_hz": sample_rate_hz, "channels": 1},
            components=_components(),
            sha256=sha256,
            source={"kind": "youtube"},
            job_id="standalone",
            analysis_id=STANDALONE_ID,
            timings_s={},
            created_at=created_at,
        )
    except ValueError:
        return None
    if not spotify_duration_s or spotify_duration_s <= 0:
        return doc
    reference = {"kind": "spotify", "id": track_id, "duration_s": spotify_duration_s}
    row = alignment_doc.build(doc, reference, STANDALONE_ID + "_aligned")
    return alignment_doc.compose(doc, row)
