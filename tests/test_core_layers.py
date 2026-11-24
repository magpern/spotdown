"""The layer split: the core analyser knows no source, no reference clock and no service; the
alignment layer and the other calculation modules are pure."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

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
        "service/explicit_calc.py",
        "service/mp3_argv.py",
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
