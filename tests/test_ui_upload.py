"""The standalone page's local-file input (ADR 0002): what it sends, that the waveform is drawn
from the picked file, and that a 401 goes to /login. Runs the page's scripts in node with a fake
fetch (tests/ui_upload_harness.js); no browser, no network."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict

import pytest

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")


def _run(status: int, probe: int = 200) -> Dict[str, Any]:
    harness = Path(__file__).with_name("ui_upload_harness.js")
    out = subprocess.run(
        ["node", str(harness), str(STATIC), str(status), str(probe)],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return json.loads(out.stdout)


def test_the_picked_file_is_sent_as_octet_stream_with_the_ui_header() -> None:
    got = _run(200)
    assert got["shown"] is True and got["card"] == "block"
    assert got["probe"]["url"] == "/api/v1/analyze-upload"
    assert got["probe"]["opts"]["headers"] == {"X-Spotdown-UI": "1"}
    post = got["post"]
    assert post["url"] == "/api/v1/analyze-upload" and post["method"] == "POST"
    assert post["headers"] == {"Content-Type": "application/octet-stream", "X-Spotdown-UI": "1"}
    assert post["credentials"] == "same-origin" and post["bodyIsFile"] is True
    assert "secret-name" not in json.dumps(post)  # no file name in the request
    assert got["rendered"] == 1 and got["login"] is None


def test_the_waveform_is_drawn_from_the_picked_file_not_from_the_server() -> None:
    got = _run(200)
    assert len(got["waveform"]) == 1 and got["waveform"][0]["same"] is True


def test_a_401_goes_to_login() -> None:
    got = _run(401)
    assert got["login"] == "/login" and got["waveform"] == [] and got["rendered"] == 0


@pytest.mark.parametrize("probe", [404, 401])
def test_the_input_stays_hidden_without_the_route(probe: int) -> None:
    got = _run(200, probe)
    assert got["shown"] is False and "card" not in got  # display never set


def test_the_input_is_hidden_until_the_route_answers() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'id="fileCard" class="card" style="display:none"' in html
    assert '<script src="upload.js"></script>' in html
