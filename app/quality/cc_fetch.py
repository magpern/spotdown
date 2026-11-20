"""Fetches the CC-licensed audio of the catalog onto the owner's machine (not run by CI or agents).

    python -m app.quality.cc_fetch --dest DIR

Writes ``DIR/audio/<id>.<ext>`` and ``DIR/corpus.json`` (read by ``python -m app.quality.corpus``).
``DIR`` must be outside this repository. Each file is downloaded over https with a size cap, and
kept only when its size and sha256 equal the catalog's; an existing file that matches is not
downloaded again. The download function is a parameter so tests never open a connection: this
module is the only place in the quality harness that is meant to reach the network, and only when
the owner runs it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List

from . import cc_catalog
from .corpus_calc import CorpusError
from .corpus_io import REPO

CATALOG = REPO / "quality" / "fixtures" / "cc" / "catalog.json"
Download = Callable[[str, int], bytes]


class HttpsOnly(urllib.request.HTTPRedirectHandler):
    """A redirect may not leave https (no downgrade to http, no other scheme)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        if not newurl.lower().startswith("https://"):
            raise CorpusError(f"{req.full_url}: redirect to a non-https URL refused")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url: str, limit: int) -> bytes:  # pragma: no cover - the owner's network call
    if not url.lower().startswith("https://"):
        raise CorpusError(f"{url}: only https downloads are allowed")
    opener = urllib.request.build_opener(HttpsOnly)
    with opener.open(url, timeout=60) as resp:
        data = resp.read(limit + 1)
    if len(data) > limit:
        raise CorpusError(f"{url}: larger than the catalog says")
    return data


def _matches(data: bytes, track: Dict[str, Any]) -> bool:
    return len(data) == track["bytes"] and hashlib.sha256(data).hexdigest() == track["sha256"]


def fetch(tracks: List[Dict[str, Any]], dest: Path, fetcher: Download = download) -> List[str]:
    """Downloads what is missing or wrong; returns the ids fetched. CorpusError on a mismatch."""
    base = dest.resolve()
    if base == REPO or REPO in base.parents:
        raise CorpusError(
            "the destination must be outside the repository (audio is never committed)"
        )
    (base / "audio").mkdir(parents=True, exist_ok=True)
    fetched: List[str] = []
    for t in tracks:
        target = base / "audio" / f"{t['id']}.{t['extension']}"
        if target.is_file() and _matches(target.read_bytes(), t):
            continue
        data = fetcher(t["download_url"], t["bytes"])
        if not _matches(data, t):
            raise CorpusError(f"{t['id']}: the download does not match the catalog size and sha256")
        target.write_bytes(data)
        fetched.append(t["id"])
    index = cc_catalog.corpus_index(tracks)
    (base / "corpus.json").write_text(json.dumps(index, indent=1) + "\n", encoding="utf-8")
    return fetched


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--dest", type=Path, required=True)
    args = ap.parse_args(argv)
    try:
        tracks = cc_catalog.validate(json.loads(CATALOG.read_text(encoding="utf-8")))
        done = fetch(tracks, args.dest)
    except (CorpusError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"{len(tracks)} tracks in the catalog, {len(done)} downloaded to {args.dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
