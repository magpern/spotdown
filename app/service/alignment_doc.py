"""The alignment layer's documents (docs/contract-v2.md, plan §2 and §4a). Pure: no I/O.

An aligned analysis is the file-clock analysis plus, for an ``ok`` alignment, the top-level
``alignment`` object and, always, the ``provenance.alignment`` record. Nothing else changes: the
core output stays on the file's clock and the caller applies ``offset_s``
(``reference_time = file_time + offset_s``). spotdown reports evidence only, never a timing class.

What the alignment cache stores per (scope, key) is an ``AlignedRow``: the aligned analysis id,
the id of the file-clock analysis it extends, and the two alignment parts.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Dict, Optional

from ..alignment_calc import ALIGNMENT_VERSION, align
from . import cache_policy

ALIGNMENT_FAILED = "alignment_failed"


@dataclass(frozen=True)
class AlignedRow:
    analysis_id: str
    base_analysis_id: str
    alignment: Optional[Dict[str, Any]]  # None when the alignment failed
    record: Dict[str, Any]

    @property
    def failed(self) -> bool:
        return self.record["result"] != "ok"

    @property
    def outcome(self) -> Optional[str]:
        """The done job's outcome and failure kind: ``alignment_failed``, or none."""
        return ALIGNMENT_FAILED if self.failed else None

    def as_doc(self) -> Dict[str, Any]:
        doc: Dict[str, Any] = {
            "analysis_id": self.analysis_id,
            "base_analysis_id": self.base_analysis_id,
            "record": self.record,
        }
        if self.alignment is not None:
            doc["alignment"] = self.alignment
        return doc

    @staticmethod
    def from_doc(doc: Dict[str, Any]) -> "AlignedRow":
        return AlignedRow(
            doc["analysis_id"], doc["base_analysis_id"], doc.get("alignment"), doc["record"]
        )


def _reference(reference: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "kind": reference["kind"],
        "id": reference["id"],
        "duration_s": round(float(reference["duration_s"]), 3),
    }


def cache_key(base_doc: Dict[str, Any], reference: Dict[str, Any]) -> str:
    """Audio identity + reference (kind, id, duration) + ``alignment_version``."""
    ref = _reference(reference)
    return cache_policy.alignment_key(
        sha256=base_doc["provenance"]["audio"]["sha256"],
        reference_kind=ref["kind"],
        reference_id=ref["id"],
        reference_duration_s=ref["duration_s"],
        alignment_version=ALIGNMENT_VERSION,
    )


def recording_match_confidence(base_doc: Dict[str, Any]) -> Optional[float]:
    """The score of the match candidate that chose this source, when a match chose it."""
    source = base_doc["provenance"]["source"]
    match = source.get("match")
    if not match or match.get("chosen") != source.get("ref"):
        return None
    for candidate in match.get("candidates", []):
        if candidate.get("ref") == match["chosen"]:
            return float(candidate["score"])
    return None


def build(base_doc: Dict[str, Any], reference: Dict[str, Any], analysis_id: str) -> AlignedRow:
    """Aligns a file-clock analysis to ``reference``; the row records every hypothesis tried."""
    audio = base_doc["provenance"]["audio"]
    ref = _reference(reference)
    result = align(
        audio["file_duration_s"],
        audio["content_start_s"],
        audio["content_end_s"],
        ref["duration_s"],
    )
    record: Dict[str, Any] = {
        "alignment_version": ALIGNMENT_VERSION,
        "reference": ref,
        "result": "ok" if result.ok else "failed",
        "hypotheses": [
            {"name": h.name, "offset_s": h.offset_s, "residual_s": h.residual_s}
            for h in result.hypotheses
        ],
    }
    alignment: Optional[Dict[str, Any]] = None
    if result.chosen is not None and result.confidence is not None:
        record.update(
            chosen=result.chosen.name,
            offset_s=result.chosen.offset_s,
            residual_s=result.chosen.residual_s,
            confidence=result.confidence,
        )
        alignment = {
            "reference": dict(ref),
            "offset_s": result.chosen.offset_s,
            "residual_s": result.chosen.residual_s,
            "confidence": result.confidence,
            "method": "estimated",
        }
        carried = recording_match_confidence(base_doc)
        if carried is not None:
            alignment["recording_match_confidence"] = carried
    return AlignedRow(analysis_id, base_doc["analysis_id"], alignment, record)


def compose(base_doc: Dict[str, Any], row: AlignedRow) -> Dict[str, Any]:
    """The aligned analysis as served: the base document, unchanged on the file's clock, with
    the alignment parts added. A failed alignment is recorded in the run's failure kind."""
    doc = copy.deepcopy(base_doc)
    doc["analysis_id"] = row.analysis_id
    doc["provenance"]["alignment"] = copy.deepcopy(row.record)
    if row.alignment is not None:
        doc["alignment"] = copy.deepcopy(row.alignment)
    else:
        doc["provenance"]["run"]["failure_kind"] = ALIGNMENT_FAILED
    return doc
