# pyright: reportPrivateImportUsage=false
# (mutagen.id3 re-exports its frame classes without __all__; the imports are valid.)
"""The ID3 title and artists of a finished ``POST /v2/audio`` mp3. Nothing is fetched: there is no
cover art. Best-effort: a tagging problem never fails an otherwise good conversion."""

from __future__ import annotations

from pathlib import Path
from typing import List


def tag_mp3(path: Path, title: str, artists: List[str]) -> None:
    try:
        from mutagen.id3 import ID3, TIT2, TPE1

        try:
            audio = ID3(path)
        except Exception:  # noqa: BLE001 - a file without a tag yet
            audio = ID3()
        audio.setall("TIT2", [TIT2(encoding=3, text=title)])
        if artists:
            audio.setall("TPE1", [TPE1(encoding=3, text=", ".join(artists))])
        audio.save(path)
    except Exception:  # noqa: BLE001 - tags are best-effort
        pass
