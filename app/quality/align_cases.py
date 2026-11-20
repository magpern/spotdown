"""Alignment cases of the synthetic corpus: the evidence behind ``false_exact`` (spotdown#20).

A case is a fixture analysed with a reference whose relation to the file is known:

* ``trimmed``: the reference is the same recording with its leading silence cut, so the true offset
  is ``-content_start_s`` (``reference_time = file_time + offset_s``);
* ``untrimmed``: the same recording from the file's first sample, true offset 0;
* ``other``: another version 3 s longer, **not** the same recording, which durations alone cannot
  separate from the real one (reported as ``alignment_overreach_reported`` when its confidence is
  high; the gate never promotes an alignment on its own).

The fixtures chosen have real leading silence (``silence-edges-130``, ``long-intro-124``), the only
ones where the trimmed and untrimmed offsets differ.
"""

from __future__ import annotations

import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List

from . import track_job
from .fixtures_io import render_to

FIXTURES = ("silence-edges-130", "long-intro-124")
OTHER_EXTRA_S = 3.0


def build_cases(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The reference cases for the chosen fixtures present in ``entries`` (pure)."""
    cases: List[Dict[str, Any]] = []
    for entry in entries:
        if entry["id"] not in FIXTURES:
            continue
        truth = entry["truth"]
        content = truth["content_end_s"] - truth["content_start_s"]
        for name, duration, same, offset in (
            ("trimmed", content, True, -truth["content_start_s"]),
            ("untrimmed", truth["duration_s"], True, 0.0),
            ("other", truth["duration_s"] + OTHER_EXTRA_S, False, None),
        ):
            cases.append(
                {
                    "id": f"{entry['id']}+{name}",
                    "entry": entry,
                    "reference": {
                        "kind": "synthetic",
                        "id": name,
                        "duration_s": round(duration, 3),
                    },
                    "same_recording": same,
                    "true_offset_s": offset,
                }
            )
    return cases


def run_case(case: Dict[str, Any]) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="quality-render-") as tmp:
        audio = render_to(case["entry"]["spec"], Path(tmp))
        res = track_job.run_audio_job(case["id"], audio, case["reference"])
    corpus = case["entry"]["spec"].get("corpus", "baseline")
    return {
        "corpus": corpus,
        "doc": res["doc"] or {},
        "audio_left": res["audio_left"],
        "same_recording": case["same_recording"],
        "true_offset_s": case["true_offset_s"],
        "ok": res["ok"],
    }


def run_cases(entries: List[Dict[str, Any]], workers: int) -> List[Dict[str, Any]]:
    cases = build_cases(entries)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return list(pool.map(run_case, cases))
