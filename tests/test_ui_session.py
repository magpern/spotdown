"""The UI's requests under the ADR 0003 session (spotdown#52 "UI"): same-origin, with
``X-Spotdown-UI`` and the JSON type, and a ``401`` sends the page to ``/login``."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")


def _run(status: int) -> dict:
    run = subprocess.run(
        ["node", str(Path(__file__).with_name("ui_session_harness.js")), str(STATIC), str(status)],
        capture_output=True, text=True, check=True, timeout=60,
    )  # fmt: skip
    return json.loads(run.stdout)


@needs_node
def test_every_request_is_same_origin_with_the_ui_header() -> None:
    out = _run(200)
    assert out["calls"].pop() == {"audio": "bytes"}  # the audio loader returned the body
    urls = [c["url"] for c in out["calls"]]
    assert [u.split("?")[0] for u in urls] == [
        "/api/Gettrack", "/api/v1/convert", "/api/v1/tasks/j", "/api/v1/analyze/t1",
        "/files/x.mp3",
    ]  # fmt: skip
    for call in out["calls"]:
        assert call["url"].startswith("/") and not call["url"].startswith("//")
        opts = call["opts"]
        assert opts["headers"]["X-Spotdown-UI"] == "1"
        assert opts["credentials"] == "same-origin" and opts["mode"] == "same-origin"
    convert = out["calls"][1]["opts"]
    assert convert["method"] == "POST"
    assert convert["headers"]["Content-Type"] == "application/json"
    assert json.loads(convert["body"])["spotify_url"].startswith("https://open.spotify.com/")
    assert out["navigations"] == []


@needs_node
def test_a_401_goes_to_login() -> None:
    out = _run(401)
    assert out["calls"].pop() == {"audio": "error"}  # the audio loader stops, too
    assert len(out["navigations"]) == 4 and set(out["navigations"]) == {"/login"}


def test_the_page_has_no_cross_origin_fallback() -> None:
    js = (STATIC / "ui.js").read_text(encoding="utf-8")
    assert "localhost:8000" not in js and "API_BASE" not in js
    assert js.count("fetch(") == 1  # every request goes through the one api() helper
