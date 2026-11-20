"""Reads an external corpus and a baseline results file from the owner's machine.

The corpus directory holds ``corpus.json`` (see ``corpus_calc``) and the audio. It must lie outside
this repository, so the audio can never be committed, and every audio path must resolve to a
regular file inside the corpus directory (no ``..``, no symlink out of it).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from . import baseline_calc, corpus_calc
from .corpus_calc import CorpusError

REPO = Path(__file__).resolve().parents[2]


def load_corpus(directory: Path) -> Tuple[Dict[str, Any], Dict[str, Path]]:
    """``(normalised index, {track id: audio path})`` or CorpusError."""
    base = directory.resolve()
    if base == REPO or REPO in base.parents:
        raise CorpusError(
            "the corpus directory must be outside the repository (audio is never committed)"
        )
    try:
        raw = json.loads((base / "corpus.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CorpusError(f"{directory}: cannot read corpus.json ({exc})") from None
    index = corpus_calc.validate_index(raw)
    audio: Dict[str, Path] = {}
    for track in index["tracks"]:
        path = (base / track["audio"]).resolve()
        if base not in path.parents or not path.is_file():
            raise CorpusError(f"{track['id']}: audio must be a file inside the corpus directory")
        audio[track["id"]] = path
    return index, audio


def load_baseline(path: Path) -> Tuple[str, Dict[str, Dict[str, Any]]]:
    """The path the file describes and its per-track entries."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CorpusError(f"{path}: cannot read the baseline results ({exc})") from None
    return doc.get("source", ""), baseline_calc.validate_baseline(doc)


def ids(index: Dict[str, Any]) -> List[str]:
    return [t["id"] for t in index["tracks"]]
