"""The layer split: the core analyser knows no source, no reference clock and no service; the
alignment layer is pure; the standalone v1 analysis keeps its shape."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app import alignment_calc
from app.analyze import analyze_track

from .service_helpers import write_wav

APP = Path(__file__).resolve().parent.parent / "app"


def _imports(path: Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            found.add(("." * node.level) + (node.module or ""))
    return found


@pytest.mark.parametrize("path", sorted((APP / "core").glob("*.py")), ids=lambda p: p.name)
def test_the_core_imports_no_source_service_or_io(path: Path) -> None:
    for name in _imports(path):
        assert not any(
            bad in name
            for bad in (
                "service",
                "spotify",
                "sources",
                "httpx",
                "db",
                "storage",
                "config",
                "yt_dlp",
            )
        ), name
    assert "spotify" not in path.read_text(encoding="utf-8").lower()


@pytest.mark.parametrize(
    "module",
    [
        "alignment_calc.py",
        "service/url_policy.py",
        "service/media_policy.py",
        "service/failure_policy.py",
        "service/analysis_doc.py",
        "service/match_policy.py",
        "youtube/normalise_policy.py",
        "youtube/match_calc.py",
        "youtube/error_policy.py",
    ],
)
def test_pure_modules_import_no_io(module: str) -> None:
    for name in _imports(APP / module):
        root = name.lstrip(".").split(".")[0]
        assert root not in {
            "httpx",
            "socket",
            "sqlite3",
            "subprocess",
            "fastapi",
            "librosa",
            "os",
            "yt_dlp",
        }, name
        assert "store" not in name and "config" not in name


@pytest.mark.parametrize(
    "file_s,start,content,ref,expected",
    [
        (200.0, 0.5, 199.0, None, (None, 0.5, None)),
        (200.0, 0.5, 199.0, 200.4, (True, 0.0, pytest.approx(-0.4))),
        (203.0, 2.5, 200.0, 200.0, (True, 2.5, pytest.approx(0.0))),
        (210.0, 2.0, 205.0, 200.0, (False, 2.0, pytest.approx(5.0))),
    ],
)
def test_offset_hypotheses(file_s, start, content, ref, expected) -> None:
    assert alignment_calc.offset_hypotheses(file_s, start, content, ref) == expected


def test_the_standalone_v1_analysis_keeps_its_shape(tmp_path: Path) -> None:
    result = analyze_track(write_wav(tmp_path / "a.wav", 12), "t1", 12)
    assert set(result) == {
        "track_id", "analyzer_version", "sr", "meter_assumed", "label_method",
        "analyzed_duration_s", "spotify_duration_s", "duration_delta_s", "content_start_s",
        "content_end_s", "content_duration_s", "leading_silence_s", "trailing_silence_s",
        "spotify_offset_s", "timeline_residual_s", "timeline_matches_spotify", "drops_spotify",
        "segments_aligned_to_spotify", "bpm", "bpm_candidates", "downbeat_phase", "beat_count",
        "beat_grid", "bars", "drops", "build_ups", "energy_curve", "segments",
    }  # fmt: skip
    assert result["analyzer_version"] == 6
    assert result["timeline_matches_spotify"] is True
    assert set(result["energy_curve"][0]) == {"t", "full", "percussive", "low"}
