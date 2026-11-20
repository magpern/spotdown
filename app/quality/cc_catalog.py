"""The committed catalog of CC-licensed tracks (pure): provenance, licence and annotations.

``quality/fixtures/cc/catalog.json`` lists each track with its title, artist, licence, source page,
a direct download URL, the size and sha256 of the file, and its human annotation (the truth shape of
``corpus_calc``). Audio is never committed: ``cc_fetch`` downloads it on the owner's machine into a
directory outside the repository and writes the ``corpus.json`` that ``app.quality.corpus`` reads.
Only licences that allow analysis and derived annotations are accepted (CC0, CC BY).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List
from urllib.parse import urlsplit

from .corpus_calc import LICENCES, CorpusError, validate_truth

CATALOG_VERSION = 1
MAX_BYTES = 30 * 1024 * 1024  # "small": a catalogue track is not a mix
EXTENSIONS = ("wav", "mp3", "m4a")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
ANNOTATED = ("bpm", "beats", "boundaries", "drops", "edges")


def _https(url: Any, where: str) -> str:
    parts = urlsplit(url if isinstance(url, str) else "")
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise CorpusError(f"{where}: must be an https URL without credentials")
    return url


def validate(catalog: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The catalog's tracks with validated truth, or CorpusError."""
    if catalog.get("catalog_version") != CATALOG_VERSION:
        raise CorpusError(f"catalog_version must be {CATALOG_VERSION}")
    out: List[Dict[str, Any]] = []
    seen = set()
    for i, raw in enumerate(catalog.get("tracks", [])):
        tid = raw.get("id")
        where = f"catalog.tracks[{i}]({tid})"
        if not isinstance(tid, str) or not _ID.match(tid) or tid in seen:
            raise CorpusError(f"{where}: id must be unique lowercase letters, digits, - or _")
        seen.add(tid)
        if raw.get("licence") not in LICENCES:
            raise CorpusError(f"{where}: licence must be one of {sorted(LICENCES)}")
        for key in ("title", "artist"):
            if not isinstance(raw.get(key), str) or not raw[key]:
                raise CorpusError(f"{where}: {key} is required")
        _https(raw.get("source_url"), f"{where}.source_url")
        _https(raw.get("download_url"), f"{where}.download_url")
        if not _SHA.match(str(raw.get("sha256", ""))):
            raise CorpusError(f"{where}: sha256 must be 64 lowercase hex digits")
        size = raw.get("bytes")
        if not isinstance(size, int) or not 0 < size <= MAX_BYTES:
            raise CorpusError(f"{where}: bytes must be between 1 and {MAX_BYTES}")
        if raw.get("extension") not in EXTENSIONS:
            raise CorpusError(f"{where}: extension must be one of {list(EXTENSIONS)}")
        truth = validate_truth(raw.get("truth", {}), f"{where}.truth")
        missing = [k for k in ANNOTATED if k not in truth]
        if missing:
            raise CorpusError(f"{where}: a catalogue track is fully annotated; missing {missing}")
        out.append(dict(raw, truth=truth))
    return out


def corpus_index(tracks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The ``corpus.json`` for the files ``cc_fetch`` wrote (``audio/<id>.<extension>``)."""
    return {
        "corpus_version": 1,
        "corpus": "cc",
        "tracks": [
            {
                "id": t["id"],
                "audio": f"audio/{t['id']}.{t['extension']}",
                "licence": t["licence"],
                "provenance": {
                    "title": t["title"],
                    "artist": t["artist"],
                    "source_url": t["source_url"],
                },
                "truth": t["truth"],
            }
            for t in tracks
        ],
    }
