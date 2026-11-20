"""The committed synthetic corpus: reproducible, big enough, audio-free, truth by arithmetic."""

from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path

from app.quality import fixtures_io, synth
from app.quality.specs import SPECS

REPO = Path(__file__).resolve().parents[1]


def test_the_manifest_is_exactly_what_the_code_generates() -> None:
    expected = json.dumps(fixtures_io.build_manifest(), indent=1, sort_keys=True) + "\n"
    assert fixtures_io.MANIFEST.read_text(encoding="utf-8") == expected


def test_at_least_twenty_fixtures_with_unique_ids() -> None:
    tracks = fixtures_io.load_manifest()["tracks"]
    ids = [t["id"] for t in tracks]
    assert len(ids) >= 20 and len(set(ids)) == len(ids)
    assert ids == [s["id"] for s in SPECS]


def test_rendering_is_deterministic() -> None:
    spec = SPECS[0]
    a, truth_a = synth.render(spec)
    b, truth_b = synth.render(spec)
    assert (a == b).all() and truth_a == truth_b


def test_truth_follows_the_spec() -> None:
    spec = SPECS[0]
    y, truth = synth.render(spec)
    assert truth["bpm"] == spec["bpm"]
    assert abs(len(y) / synth.SR - truth["duration_s"]) < 0.01
    beats = truth["beats"]
    gaps = {round(b - a, 3) for a, b in zip(beats, beats[1:])}
    assert round(60.0 / spec["bpm"], 3) in gaps  # longer gaps are sections with no pulse


def test_no_audio_is_committed_under_quality() -> None:
    out = subprocess.run(
        ["git", "ls-files", "quality"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.split()
    audio = (".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".opus", ".aiff")
    assert not [p for p in out if p.lower().endswith(audio)]
    on_disk = [p for p in (REPO / "quality").rglob("*") if p.suffix.lower() in audio]
    assert on_disk == []


def test_the_quality_modules_stay_small_and_the_core_stays_pure() -> None:
    banned = {"app.service", "app.db", "httpx", "sqlalchemy", "requests", "socket", "subprocess"}
    for path in [
        *(REPO / "app" / "core").glob("*_calc.py"),
        *(REPO / "app" / "quality").glob("*_calc.py"),
    ]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                assert not any(name == b or name.startswith(b + ".") for b in banned), path
    for path in (REPO / "app" / "quality").glob("*.py"):
        assert len(path.read_text(encoding="utf-8").splitlines()) <= 330, path
