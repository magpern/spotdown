"""The ``youtube`` source of ``POST /v2/analyses`` with a faked yt-dlp: off by default, the
11-character id only, a global analysis shared by every scope, provenance that names no caller,
the decoded duration check and every failure status."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from .cache_helpers import Stack
from .service_helpers import AUTH, SCOPE, assert_valid
from .youtube_fakes import YoutubeStack, entry

VID = "dQw4w9WgXcQ"
SOURCE = f"youtube:{VID}"


@pytest.fixture()
def stack(tmp_path: Path) -> YoutubeStack:
    return YoutubeStack(tmp_path)


def analyse(stack: YoutubeStack, scope: Any = None, ref: str = SOURCE) -> Any:
    body: Dict[str, Any] = {"source_ref": ref}
    if scope is not None:
        body["cache_scope"] = scope
    return stack.post(body)


@pytest.mark.parametrize("scope", [None, SCOPE])
def test_the_source_is_off_by_default(tmp_path: Path, scope) -> None:
    off = Stack(tmp_path)
    body: Dict[str, Any] = {"source_ref": SOURCE, **({"cache_scope": scope} if scope else {})}
    res = off.post(body)
    assert res.status_code == 422 and res.json()["error"]["code"] == "source_disabled"
    assert off.dispatch.job_ids == []


@pytest.mark.parametrize(
    "ref,code",
    [
        ("youtube:short", "invalid_request"),
        ("youtube:dQw4w9WgXcQx", "invalid_request"),
        ("youtube:dQw4w9WgXcQ\n", "invalid_request"),
        ("youtube:dQw4w9WgXcQ\r\n", "invalid_request"),
        ("youtube:https://www.youtube.com/watch?v=dQw4w9WgXcQ", "invalid_request"),
        ("youtube:../../etc/pas", "invalid_request"),
        ("spotify:4uLU6hMCjMI75M1A2tKUQC", "invalid_request"),
        ("dQw4w9WgXcQ", "invalid_request"),
    ],
)
def test_the_reference_is_the_11_character_video_id_only(stack: YoutubeStack, ref, code) -> None:
    res = analyse(stack, ref=ref)
    assert res.status_code == 422 and res.json()["error"]["code"] == code
    assert stack.dispatch.job_ids == [] and stack.world.calls == []


def test_a_youtube_analysis_is_global_and_names_no_caller(stack: YoutubeStack) -> None:
    res = analyse(stack, "scope_secret")
    assert res.status_code == 202
    job_id = res.json()["job_id"]
    analysis_id = stack.done_analysis_id(res, "scope_secret")
    assert stack.world.downloads == [VID]
    with stack.store._conn() as conn:
        (row,) = conn.execute("SELECT cache_scope, doc FROM analyses").fetchall()
        (link,) = conn.execute("SELECT * FROM source_links").fetchall()
    assert row["cache_scope"] is None
    assert (link["source_kind"], link["source_ref"], link["scope_key"]) == ("youtube", VID, "")
    doc = json.loads(row["doc"])
    assert_valid("analysis-v1", doc)
    run = doc["provenance"]["run"]
    assert run["job_id"].startswith("run_") and run["job_id"] != job_id
    assert run["timings_s"] == {}
    assert doc["provenance"]["source"]["kind"] == "youtube"
    assert doc["provenance"]["source"]["ref"] == VID
    assert job_id not in row["doc"] and "scope_secret" not in row["doc"]
    assert stack.get_analysis(analysis_id, None).json() == doc


def test_a_cross_scope_request_is_a_hit_without_download(stack: YoutubeStack) -> None:
    first = stack.done_analysis_id(analyse(stack, "scope_a"), "scope_a")
    for scope in ("scope_b", None):
        res = analyse(stack, scope)
        assert res.status_code == 200 and res.json()["status"] == "done"
        assert res.json()["analysis_id"] == first
        assert stack.get_analysis(first, scope).status_code == 200
        assert stack.lookup(scope, source=SOURCE).json()["analysis_id"] == first
    assert stack.world.downloads == [VID]


def test_the_same_audio_under_another_id_reuses_the_global_analysis(stack: YoutubeStack) -> None:
    first = stack.done_analysis_id(analyse(stack))
    second = stack.done_analysis_id(analyse(stack, ref="youtube:otherid0001"))
    assert second == first and len(stack.analysed) == 1
    assert stack.lookup(None, source="youtube:otherid0001").json()["analysis_id"] == first


def test_a_reused_analysis_is_checked_against_the_other_ids_duration(stack: YoutubeStack) -> None:
    """Equal bytes under another video id reuse the global analysis only when its measured
    duration fits what that id was expected to be (here its metadata says 200 s, not 12 s)."""
    first = stack.done_analysis_id(analyse(stack))
    stack.world.infos["otherid0001"] = {"id": "otherid0001", "duration": 200.0}
    job = stack.job(analyse(stack, ref="youtube:otherid0001"))
    assert job["status"] == "failed" and job["outcome"] == "uncertain"
    assert "analysis_id" not in job and len(stack.analysed) == 1
    assert stack.lookup(None, source="youtube:otherid0001").status_code == 404
    assert stack.lookup(None, source=SOURCE).json()["analysis_id"] == first
    stack.world.infos["otherid0002"] = {"id": "otherid0002", "duration": 13.0}
    assert stack.done_analysis_id(analyse(stack, ref="youtube:otherid0002")) == first


def test_a_scope_deletion_leaves_the_global_analysis(stack: YoutubeStack) -> None:
    analysis_id = stack.done_analysis_id(analyse(stack, SCOPE), SCOPE)
    assert stack.client.delete(f"/v2/scopes/{SCOPE}", headers=AUTH).status_code == 204
    assert stack.get_analysis(analysis_id, "scope_b").status_code == 200
    assert stack.lookup(None, source=SOURCE).json()["analysis_id"] == analysis_id


def test_the_match_record_goes_into_provenance(stack: YoutubeStack) -> None:
    stack.world.entries = [entry(VID, "Blinding Lights", "The Weeknd - Topic", 12.3)]
    track = {"client_ref": "c", "title": "Blinding Lights", "artists": ["The Weeknd"],
             "duration_s": 12.0}  # fmt: skip
    stack.match([track], scope=SCOPE)
    doc = stack.get_analysis(stack.done_analysis_id(analyse(stack)), None).json()
    source = doc["provenance"]["source"]
    assert source["match"]["chosen"] == VID
    assert source["match"]["normalised_query"] == "the weeknd blinding lights"
    assert source["match"]["candidates"][0]["ref"] == VID
    assert source["decoded_duration_check"]["expected_from"] == "match"
    assert source["decoded_duration_check"]["result"] == "ok"
    assert_valid("analysis-v1", doc)


def test_without_a_match_the_metadata_duration_is_checked(stack: YoutubeStack) -> None:
    doc = stack.get_analysis(stack.done_analysis_id(analyse(stack)), None).json()
    check = doc["provenance"]["source"]["decoded_duration_check"]
    assert check["expected_from"] == "metadata" and check["expected_s"] == 12.0
    assert "match" not in doc["provenance"]["source"]


def test_a_decoded_duration_far_from_the_match_is_uncertain_and_kept_nowhere(stack) -> None:
    stack.world.entries = [entry(VID, "Blinding Lights", "The Weeknd - Topic", 200.0)]
    track = {"client_ref": "c", "title": "Blinding Lights", "artists": ["The Weeknd"],
             "duration_s": 200.0}  # fmt: skip
    stack.match([track])
    job = stack.job(analyse(stack))  # the fake audio decodes to 12 s, not 200 s
    assert job["status"] == "failed" and job["outcome"] == "uncertain"
    assert job["failure_kind"] is None and "analysis_id" not in job
    assert_valid("job-v1", job)
    with stack.store._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM analyses").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM source_links").fetchone()[0] == 0


@pytest.mark.parametrize(
    "message,outcome",
    [
        ("ERROR: [youtube] x: Video unavailable", "source_unavailable"),
        ("ERROR: [youtube] x: Sign in to confirm your age", "source_unavailable"),
        ("ERROR: [youtube] x: Sign in to confirm you're not a bot", "provider_error"),
        ("ERROR: HTTP Error 502: Bad Gateway", "provider_error"),
        ("ERROR: [youtube] x: Requested format is not available", "provider_error"),
        ("ERROR: HTTP Error 429: Too Many Requests", "rate_limited"),
    ],
)
def test_download_failures_end_the_job_with_their_status(stack, message, outcome) -> None:
    stack.world.download_error = message
    job = stack.job(analyse(stack, SCOPE), SCOPE)
    assert job["status"] == "failed" and job["outcome"] == outcome
    assert_valid("job-v1", job)
    assert ("retry_after_s" in job) == (outcome == "rate_limited")
    assert stack.analysed == []


def test_a_rate_limit_backs_off_later_downloads_and_matches(stack: YoutubeStack) -> None:
    stack.world.download_error = "ERROR: HTTP Error 429: Too Many Requests"
    analyse(stack)
    stack.world.download_error = None
    job = stack.job(analyse(stack, ref="youtube:otherid0001"))
    assert job["outcome"] == "rate_limited" and job["retry_after_s"] > 0
    track = {"client_ref": "c", "title": "x", "artists": ["y"], "duration_s": 100.0}
    (r,) = stack.match([track]).json()["results"]
    assert r["status"] == "rate_limited"
    assert stack.world.downloads == [] and stack.world.searches == []


def test_the_source_switched_off_after_queueing_refuses_in_the_worker(stack) -> None:
    stack.dispatch.then = None  # queue only
    res = analyse(stack)
    stack.settings.allow_ytdlp_sourcing = False
    stack._run(res.json()["job_id"])
    job = stack.job(res)
    assert job["outcome"] == "source_unavailable" and stack.world.calls == []


def test_no_audio_is_left_after_a_youtube_job(stack: YoutubeStack) -> None:
    from .service_helpers import audio_files

    stack.done_analysis_id(analyse(stack))
    stack.world.download_error = "ERROR: Video unavailable"
    stack.job(analyse(stack, ref="youtube:otherid0001"))
    assert audio_files(stack.settings) == []


@pytest.mark.parametrize(
    "query",
    [
        {"source": "youtube:dQw4w9WgXcQ\n"},
        {"sha256": "a" * 64 + "\n"},
    ],
)
def test_lookups_refuse_a_trailing_newline(stack: YoutubeStack, query) -> None:
    res = stack.lookup(SCOPE, **query)
    assert res.status_code == 422 and res.json()["error"]["code"] == "invalid_request"


def test_scopes_and_idempotency_keys_refuse_a_trailing_newline(stack: YoutubeStack) -> None:
    res = stack.post({"cache_scope": SCOPE + "\n", "source_ref": SOURCE})
    assert res.status_code == 422 and res.json()["error"]["code"] == "invalid_request"
    res = stack.post({"source_ref": SOURCE}, key="key-1\n")
    assert res.status_code == 422 and res.json()["error"]["code"] == "invalid_request"
    assert stack.dispatch.job_ids == []
