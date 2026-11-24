"""The UI page's scripts drive both flows through the UI server's ``/api`` routes only (ADR 0005):
same-origin, with the session cookie and ``X-Spotdown-UI``, never a backend URL or an
``Authorization`` header; every server string is rendered as text. The scripts run in a stubbed
DOM with a fake fetch (``ui_page_harness.js``). No browser, no network."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict

import pytest

from .helpers import ROOT, analysis_doc

STATIC = ROOT / "spotdown_ui" / "static"
HARNESS = Path(__file__).with_name("ui_page_harness.js")
HOSTILE = "<img src=x onerror=alert(1)>\"'&"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")


def _run(**extra: Any) -> Dict[str, Any]:
    stdin = json.dumps({"document": analysis_doc(), "title": HOSTILE, **extra})
    out = subprocess.run(["node", str(HARNESS), str(STATIC)], input=stdin, capture_output=True,
                         text=True, timeout=60)  # fmt: skip
    assert out.returncode == 0, out.stderr[-2000:]
    result = json.loads(out.stdout)
    assert "error" not in result, result.get("error")
    return result


@pytest.fixture(scope="module")
def run() -> Dict[str, Any]:
    return _run()


@pytest.mark.parametrize(
    "truncated,total,shown,absent",
    [(True, 250, "Showing 1 of 250 tracks", "may be incomplete"),
     (False, 1, None, "Showing"),
     (None, None, "The list may be incomplete: full playlists need the backend's Spotify "
                  "credentials.", "Spotify listed no more")],
)  # fmt: skip
def test_the_track_list_says_whether_it_is_complete(truncated, total, shown, absent) -> None:
    """``truncated`` is true (some left out), false (all listed) or null (unknown, #76)."""
    tracks = _run(truncated=truncated, total=total)["steps"]["tracks"]
    if shown:
        assert shown in tracks
    assert absent not in tracks


def test_a_track_without_duration_or_artists_is_matched_as_it_is() -> None:
    """Spotify's public pages may give no duration or artists (#76): the page sends none and no
    alignment reference, and never invents a value."""
    result = _run(bare=True)
    bodies = {c["url"]: json.loads(c["body"]) for c in result["calls"] if c["method"] == "POST"}
    assert bodies["/api/match"] == {"tracks": [{"title": "Bare"}]}
    assert "reference" not in bodies["/api/analyses"]
    assert "Audio analysis" in result["steps"]["linkAnalysis"]["analysis"]


def test_the_link_flow_calls_the_ui_routes_in_order(run) -> None:
    seen = [(c["method"], c["url"]) for c in run["calls"]]
    assert seen[:6] == [
        ("POST", "/api/spotify/resolve"), ("POST", "/api/match"), ("POST", "/api/analyses"),
        ("GET", "/api/jobs/job_1"), ("GET", "/api/jobs/job_1"), ("GET", "/api/analyses/an_1"),
    ]  # fmt: skip
    bodies = {c["url"]: json.loads(c["body"]) for c in run["calls"] if c["method"] == "POST"}
    assert bodies["/api/spotify/resolve"] == {"url": "https://open.spotify.com/playlist/x"}
    assert bodies["/api/match"]["tracks"][0]["duration_s"] == 213
    assert ("POST", "/api/audio") in seen and ("GET", "/api/audio-jobs/job_a") in seen
    assert ("GET", "/api/jobs/job_a") not in seen
    audio = [json.loads(c["body"]) for c in run["calls"] if c["url"] == "/api/audio"][0]
    assert audio["source_ref"] == "youtube:dQw4w9WgXcQ" and audio["bitrate"] == 192


def test_the_file_flow_uploads_then_analyses(run) -> None:
    seen = [(c["method"], c["url"]) for c in run["fileCalls"]]
    assert seen[:2] == [("PUT", "/api/uploads"), ("POST", "/api/analyses")]
    assert seen[-1] == ("GET", "/api/analyses/an_1")
    put = run["fileCalls"][0]
    assert put["body"] == "<picked file>"
    assert put["headers"]["Content-Type"] == "application/octet-stream"
    assert json.loads(run["fileCalls"][1]["body"]) == {"upload_id": "up_1"}
    assert "Audio analysis" in run["steps"]["fileAnalysis"]["analysis"]


def test_every_request_is_same_origin_with_the_ui_header_and_no_key(run) -> None:
    for call in run["calls"]:
        assert call["url"].startswith("/api/") and "//" not in call["url"], call
        assert call["credentials"] == "same-origin" and call["mode"] == "same-origin"
        assert call["headers"]["X-Spotdown-UI"] == "1"
        assert not any(k.lower() == "authorization" for k in call["headers"]), call
    assert run["navigations"] == []


def test_server_strings_are_rendered_as_text(run) -> None:
    steps = run["steps"]
    for html in (steps["tracks"], steps["match"]):
        assert "<img src=x" not in html and "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "Showing 1 of 250 tracks" in steps["tracks"]
    assert "Audio analysis" in steps["linkAnalysis"]["analysis"]
    assert 'href="/api/audio/au_1" download' in steps["download"]
