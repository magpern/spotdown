"""The service-mode UI (ADR 0004): the page analyses only through the authenticated v2 API, keeps
the service key in memory only, and renders hostile strings as text. The page's scripts run in a
stubbed DOM with a fake fetch (tests/ui_service_harness.js); the server side runs a real v2 job
with a faked analyser. No browser, no network."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict

import pytest

from app.service import idempotency_policy
from app.service.worker import execute_job

from .service_helpers import (
    AUTH,
    audio_files,
    client_for,
    fake_analyser_result,
    service_settings,
    wav_bytes,
)
from .test_ui_fields import _hand_doc

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
HARNESS = Path(__file__).with_name("ui_service_harness.js")
HOSTILE = "<img src=x onerror=alert(1)>\"'&<script>steal()</script>"
# A key with markup in it: wherever it leaked into the page it would show (raw or escaped).
KEY = 'k3y-"><svg onload=alert(1)>-' + "0123456789abcdef" * 2
BANNED_IN_SERVICE_JS = (
    "localStorage",
    "sessionStorage",
    "document.cookie",
    "console.",
    "location.",
    "history.",
    "indexedDB",
    "/api/",
    "/files",
    "/login",
)
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")


def _hostile_doc() -> Dict[str, Any]:
    doc = _hand_doc()
    doc["labels"] = [
        {"segment_id": s["id"], "label": HOSTILE, "confidence": 0.5, "method": HOSTILE}
        for s in doc["structure"]["segments"]
    ]
    doc["provenance"]["analyzer"]["name"] = HOSTILE
    doc["provenance"]["created_at"] = HOSTILE
    return doc


def _run(scenario: str, error: str = HOSTILE) -> Dict[str, Any]:
    payload = {"scenario": scenario, "key": KEY, "document": _hostile_doc(), "error": error}
    out = subprocess.run(
        ["node", str(HARNESS), str(STATIC)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return json.loads(out.stdout)


def _assert_key_kept_in_memory_only(out: Dict[str, Any]) -> None:
    traps = out["traps"]
    assert traps["storage"] == [] and traps["cookies"] == [] and traps["history"] == []
    assert traps["navigations"] == []
    assert out["keyInputAfter"] in ("", "0")  # the typed key left the input at once
    for kind, line in traps["logs"]:
        assert KEY not in line, kind
    for el_id, html, text, value in out["shown"]:
        for shown in (html, text, value):
            assert KEY not in shown and "svg onload" not in shown, el_id
    for call in out["calls"]:
        assert KEY not in call["url"] and KEY not in (call["body"] or "")
        others = {k: v for k, v in call["headers"].items() if k != "Authorization"}
        assert KEY not in json.dumps(others)


@needs_node
def test_the_page_analyses_through_v2_with_the_bearer_key() -> None:
    out = _run("flow")
    calls = out["calls"]
    assert [(c["method"], c["url"].split("?")[0]) for c in calls] == [
        ("GET", "/v2/jobs/job_keycheck"),
        ("PUT", "/v2/uploads"),
        ("POST", "/v2/analyses"),
        ("GET", "/v2/jobs/job_1"),
        ("GET", "/v2/jobs/job_1"),
        ("GET", "/v2/analyses/an_1"),
    ]
    for call in calls:
        assert call["headers"]["Authorization"] == f"Bearer {KEY}"  # the key, trimmed
        assert call["credentials"] == "omit" and call["mode"] == "same-origin"
        assert call["referrerPolicy"] == "no-referrer"
        assert call["url"].startswith("/v2/")
        if call["method"] != "POST":  # the POST names its scope in the body
            assert "cache_scope=spotdown_ui" in call["url"]
    put, post = calls[1], calls[2]
    assert put["body"] == "<picked file>"
    assert put["headers"]["Content-Type"] == "application/octet-stream"
    assert json.loads(post["body"]) == {"cache_scope": "spotdown_ui", "upload_id": "up_1"}
    for call in (put, post):
        key = call["headers"]["Idempotency-Key"]
        assert idempotency_policy.key_problem(key) is None
    assert put["headers"]["Idempotency-Key"] != post["headers"]["Idempotency-Key"]
    assert out["states"][1].startswith("Service key accepted")
    _assert_key_kept_in_memory_only(out)


@needs_node
def test_the_analysis_renders_hostile_strings_as_text_with_tag_and_timeline() -> None:
    out = _run("flow")
    page = out["analysis"]
    assert out["status"] == "Analysis ready."
    assert "<img src=x" not in page and "<script>" not in page and "onerror=alert(1)>" not in page
    assert "&lt;img src=x onerror=alert(1)&gt;" in page
    assert "Explicit" in page  # the explicit tag (spotdown#36)
    assert 'id="tlHost"' in page and 'id="wfCard"' in page  # the timeline and waveform
    assert "service analysis of a local file" in page
    assert "YouTube match" not in page


@needs_node
def test_without_a_key_the_page_shows_the_required_state_and_sends_nothing() -> None:
    out = _run("nokey")
    assert out["calls"] == []
    assert out["states"][0].startswith("Service key required")
    assert out["status"] == "Service key required: enter it above first."
    _assert_key_kept_in_memory_only(out)


@needs_node
def test_a_refused_key_is_forgotten_and_nothing_else_is_sent() -> None:
    out = _run("reject")
    assert [c["url"].split("?")[0] for c in out["calls"]] == ["/v2/jobs/job_keycheck"]
    assert out["fileCalls"] == 0 and out["keyVar"] is None
    assert out["states"][1].startswith("Service key refused")
    assert "Service key required" in out["status"]
    _assert_key_kept_in_memory_only(out)


@needs_node
@pytest.mark.parametrize("scenario", ["upload_error", "failed"])
def test_a_hostile_server_message_is_shown_as_text(scenario: str) -> None:
    out = _run(scenario)
    assert "<img" not in out["status"] and "<script>" not in out["status"]
    assert "&lt;img src=x onerror=alert(1)&gt;" in out["status"]
    assert out["status"].startswith("Analysis failed: ")
    assert out["analysis"] == ""
    _assert_key_kept_in_memory_only(out)


def test_the_service_script_never_stores_or_logs_the_key() -> None:
    js = (STATIC / "service_ui.js").read_text(encoding="utf-8")
    code = js.split("\n", 6)[-1]  # past the header comment
    for banned in BANNED_IN_SERVICE_JS:
        assert banned not in code, banned
    assert js.count("fetch(") == 1  # one helper, which always sends the bearer key


def test_a_service_ui_upload_is_analysed_and_its_audio_deleted(tmp_path: Path) -> None:
    """The flow the page drives, against the real v2 routes with the UI on: the key is required,
    the job ends done, and no audio is left (deleted after the job)."""
    settings = service_settings(tmp_path, spotdown_ui=True)
    holder: Dict[str, Any] = {}

    def dispatch(job_id: str) -> None:
        execute_job(
            job_id,
            settings,
            holder["client"].app.state.store,
            analyser=lambda *a: fake_analyser_result(),
        )

    client = client_for(settings, dispatch=dispatch)
    holder["client"] = client
    assert client.get("/").status_code == 200
    body = wav_bytes(tmp_path, 2.0)
    octet = {"Content-Type": "application/octet-stream"}
    url = "/v2/uploads?cache_scope=spotdown_ui"
    assert client.put(url, content=body, headers=octet).status_code == 401
    up = client.put(url, content=body, headers={**AUTH, **octet})
    assert up.status_code == 201, up.text
    upload_id = up.json()["upload_id"]
    post = client.post(
        "/v2/analyses", json={"cache_scope": "spotdown_ui", "upload_id": upload_id}, headers=AUTH
    )
    assert post.status_code == 202, post.text
    job = client.get(f"/v2/jobs/{post.json()['job_id']}?cache_scope=spotdown_ui", headers=AUTH)
    assert job.json()["status"] == "done", job.text
    analysis_id = job.json()["analysis_id"]
    doc = client.get(f"/v2/analyses/{analysis_id}?cache_scope=spotdown_ui", headers=AUTH)
    assert doc.status_code == 200 and doc.json()["schema_version"]
    assert client.get(f"/v2/analyses/{analysis_id}?cache_scope=spotdown_ui").status_code == 401
    assert audio_files(settings) == []
    assert client.get("/files/" + upload_id).status_code == 404


@pytest.mark.parametrize("name", ["service.html", "service_ui.js", "ui_common.js"])
def test_each_service_ui_file_stays_small(name: str) -> None:
    assert len((STATIC / name).read_text(encoding="utf-8").splitlines()) < 300
