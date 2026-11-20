"""Fixture I/O: write the committed truth manifest and render a spec to a temporary WAV.

The audio is never committed: it is rendered on demand from the spec into a directory the caller
deletes. Run ``python -m app.quality.fixtures_io --write`` after changing a spec.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import soundfile as sf

from .specs import SPECS
from .synth import SR, render

ROOT = Path(__file__).resolve().parents[2] / "quality"
MANIFEST = ROOT / "fixtures" / "synthetic" / "manifest.json"
MANIFEST_VERSION = 1


def build_manifest() -> Dict[str, Any]:
    tracks: List[Dict[str, Any]] = []
    for spec in SPECS:
        _, truth = render(spec)
        tracks.append({"id": spec["id"], "spec": spec, "truth": truth})
    return {"manifest_version": MANIFEST_VERSION, "sample_rate_hz": SR, "tracks": tracks}


def load_manifest() -> Dict[str, Any]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def render_to(spec: Dict[str, Any], directory: Path) -> Path:
    y, _ = render(spec)
    path = directory / f"{spec['id']}.wav"
    sf.write(str(path), y, SR, subtype="PCM_16")
    return path


def main(argv: List[str]) -> int:
    text = json.dumps(build_manifest(), indent=1, sort_keys=True) + "\n"
    if "--write" in argv:
        MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST.write_text(text, encoding="utf-8")
        return 0
    return 0 if MANIFEST.read_text(encoding="utf-8") == text else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
