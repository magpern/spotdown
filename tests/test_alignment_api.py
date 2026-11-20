"""The alignment layer through the v2 API and the worker (spotdown#11). Fakes only: the
analyser returns a canned 12 s result (content 0.4 to 11.5 s), yt-dlp is faked."""

from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from app.service.store import Store

from .cache_helpers import Stack
from .service_helpers import AUTH, SCOPE, assert_valid
from .youtube_fakes import YoutubeStack

REF = {"kind": "spotify", "id": "4uLU6hMCjMI75M1A2tKUQC", "duration_s": 12.0}
TRIMMED = {**REF, "duration_s": 11.1}
FAR = {**REF, "duration_s": 40.0}
VID = "dQw4w9WgXcQ"


@pytest.fixture()
def stack(tmp_path: Path) -> Stack:
    return Stack(tmp_path)


def analyse(stack: Stack, data: bytes, reference: Optional[Dict] = None, scope: str = SCOPE):
    upload_id = stack.upload(data, scope).json()["upload_id"]
    body: Dict[str, Any] = {"cache_scope": scope, "upload_id": upload_id}
    if reference is not None:
        body["reference"] = reference
    return stack.post(body)


def job(stack: Stack, res: Any, scope: Optional[str] = SCOPE) -> Dict[str, Any]:
    params = {"cache_scope": scope} if scope else {}
    got = stack.client.get(f"/v2/jobs/{res.json()['job_id']}", headers=AUTH, params=params)
    view = got.json()
    assert_valid("job-v1", view)
    return view


def core_part(doc: Dict[str, Any]) -> Dict[str, Any]:
    doc = copy.deepcopy(doc)
    for key in ("analysis_id", "alignment"):
        doc.pop(key, None)
    doc["provenance"].pop("alignment", None)
    doc["provenance"]["run"].pop("failure_kind", None)
    doc["provenance"]["run"].pop("job_id", None)
    return doc


def test_without_a_reference_there_is_no_alignment(stack: Stack) -> None:
    res = analyse(stack, stack.audio("a.wav"))
    view = job(stack, res)
    assert view["status"] == "done" and view["outcome"] is None and view["failure_kind"] is None
    doc = stack.get_analysis(view["analysis_id"]).json()
    assert "alignment" not in doc and "alignment" not in doc["provenance"]
    assert stack.rows("alignments") == []


def test_a_reference_gives_an_aligned_analysis_with_the_core_unchanged(stack: Stack) -> None:
    data = stack.audio("a.wav")
    plain = stack.get_analysis(stack.analysis_id(analyse(stack, data))).json()
    res = analyse(stack, data, TRIMMED)
    assert res.status_code == 200  # a cache hit: the alignment is computed at once
    view = job(stack, res)
    assert view["status"] == "done" and view["outcome"] is None
    assert view["analysis_id"] == res.json()["analysis_id"] != plain["analysis_id"]
    doc = stack.get_analysis(view["analysis_id"]).json()
    assert_valid("analysis-v1", doc)
    assert doc["alignment"]["offset_s"] == -0.4 and doc["alignment"]["residual_s"] == 0.0
    assert doc["alignment"]["reference"] == TRIMMED
    assert doc["provenance"]["alignment"]["chosen"] == "trimmed"
    assert core_part(doc) == core_part(plain)
    assert len(stack.analysed) == 1
    # The file-clock analysis itself is untouched and still what a lookup finds.
    assert stack.get_analysis(plain["analysis_id"]).json() == plain
    assert (
        stack.lookup(sha256=plain["provenance"]["audio"]["sha256"]).json()["analysis_id"]
        == (plain["analysis_id"])
    )


def test_a_queued_job_with_a_reference_is_aligned_by_the_worker(stack: Stack) -> None:
    res = analyse(stack, stack.audio("a.wav"), REF)
    assert res.status_code == 202
    view = job(stack, res)
    doc = stack.get_analysis(view["analysis_id"]).json()
    assert_valid("analysis-v1", doc)
    assert doc["alignment"]["offset_s"] == 0.0
    assert doc["provenance"]["alignment"]["chosen"] == "untrimmed"
    assert doc["provenance"]["run"]["job_id"] == res.json()["job_id"]
    (base,) = stack.rows("analyses")
    assert base["analysis_id"] != view["analysis_id"]
    assert "alignment" not in json.loads(base["doc"])


def test_the_alignment_is_cached_per_audio_reference_and_scope(stack: Stack) -> None:
    data = stack.audio("a.wav")
    first = job(stack, analyse(stack, data, REF))["analysis_id"]
    again = analyse(stack, data, REF)
    assert again.status_code == 200 and again.json()["analysis_id"] == first
    other = job(stack, analyse(stack, data, TRIMMED))["analysis_id"]
    assert other != first
    assert len(stack.rows("alignments")) == 2 and len(stack.analysed) == 1
    elsewhere = job(stack, analyse(stack, data, REF, scope="scope_b"), "scope_b")["analysis_id"]
    assert elsewhere != first
    assert stack.get_analysis(first, "scope_b").status_code == 404
    assert stack.get_analysis(elsewhere, SCOPE).status_code == 404
    assert stack.get_analysis(first, None).status_code == 404


def test_a_failed_alignment_is_a_done_job_with_a_file_clock_analysis(stack: Stack) -> None:
    res = analyse(stack, stack.audio("a.wav"), FAR)
    view = job(stack, res)
    assert view["status"] == "done"
    assert view["outcome"] == "alignment_failed" and view["failure_kind"] == "alignment_failed"
    doc = stack.get_analysis(view["analysis_id"]).json()
    assert_valid("analysis-v1", doc)
    assert "alignment" not in doc
    assert doc["provenance"]["alignment"]["result"] == "failed"
    assert doc["provenance"]["alignment"]["reference"] == FAR
    assert doc["provenance"]["run"]["failure_kind"] == "alignment_failed"
    hit = analyse(stack, stack.audio("a.wav"), FAR)  # the cached failure, on a hit
    assert hit.status_code == 200 and hit.json()["analysis_id"] == view["analysis_id"]
    assert job(stack, hit)["outcome"] == "alignment_failed"


def test_a_scope_deletion_removes_its_alignments(stack: Stack) -> None:
    aligned = job(stack, analyse(stack, stack.audio("a.wav"), REF))["analysis_id"]
    assert stack.client.delete(f"/v2/scopes/{SCOPE}", headers=AUTH).status_code == 204
    assert stack.rows("alignments") == []
    assert stack.get_analysis(aligned).status_code == 404


@pytest.mark.parametrize("duration", [0, -1, 7201])
def test_a_reference_needs_a_plausible_duration(stack: Stack, duration: float) -> None:
    upload_id = stack.upload(stack.audio("a.wav")).json()["upload_id"]
    body = {
        "cache_scope": SCOPE,
        "upload_id": upload_id,
        "reference": {**REF, "duration_s": duration},
    }
    assert stack.post(body).status_code == 422


def test_the_reference_is_part_of_the_idempotent_request(stack: Stack) -> None:
    data = stack.audio("a.wav")
    upload_id = stack.upload(data).json()["upload_id"]
    body = {"cache_scope": SCOPE, "upload_id": upload_id, "reference": REF}
    first = stack.post(body, key="k-1")
    assert stack.post(body, key="k-1").json() == first.json()
    assert stack.post({**body, "reference": TRIMMED}, key="k-1").status_code == 409


def test_a_youtube_alignment_is_global_like_its_analysis(tmp_path: Path) -> None:
    yt = YoutubeStack(tmp_path)
    res = yt.post({"source_ref": f"youtube:{VID}", "cache_scope": "scope_secret", "reference": REF})
    aligned = yt.done_analysis_id(res, "scope_secret")
    with yt.store._conn() as conn:
        (row,) = conn.execute("SELECT cache_scope, doc FROM alignments").fetchall()
    assert row["cache_scope"] == "" and "scope_secret" not in row["doc"]
    for scope in ("scope_b", None):
        doc = yt.get_analysis(aligned, scope).json()
        assert_valid("analysis-v1", doc)
        assert doc["alignment"]["reference"] == REF
        again = yt.post(
            {
                "source_ref": f"youtube:{VID}",
                "reference": REF,
                **({"cache_scope": scope} if scope else {}),
            }
        )
        assert again.status_code == 200 and again.json()["analysis_id"] == aligned
    assert yt.client.delete("/v2/scopes/scope_secret", headers=AUTH).status_code == 204
    assert yt.get_analysis(aligned, None).status_code == 200
    assert yt.world.downloads == [VID]


def test_an_alignment_of_an_older_analysis_is_not_served(stack: Stack) -> None:
    """A cached row for the same key but another file-clock analysis (another analyzer version)
    is replaced, never served as this analysis' alignment."""
    data = stack.audio("a.wav")
    first = job(stack, analyse(stack, data, REF))["analysis_id"]
    with stack.store._conn() as conn:
        (row,) = conn.execute("SELECT doc FROM alignments").fetchall()
        doc = json.loads(row["doc"])
        doc["base_analysis_id"] = "an_older"
        conn.execute("UPDATE alignments SET doc = ?", (json.dumps(doc),))
    again = analyse(stack, data, REF)
    assert again.status_code == 200 and again.json()["analysis_id"] != first
    assert stack.get_analysis(again.json()["analysis_id"]).json()["alignment"]["offset_s"] == 0.0


def test_an_old_database_gains_the_alignment_columns(tmp_path: Path) -> None:
    path = tmp_path / "svc.sqlite"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            "CREATE TABLE jobs (job_id TEXT PRIMARY KEY, cache_scope TEXT, client_ref TEXT,"
            " source_kind TEXT NOT NULL, source_ref TEXT, upload_id TEXT, fetch_url TEXT,"
            " status TEXT NOT NULL, outcome TEXT, failure_kind TEXT, retry_after_s REAL,"
            " analysis_id TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL);"
            "CREATE TABLE alignments (cache_scope TEXT NOT NULL, align_key TEXT NOT NULL,"
            " doc TEXT NOT NULL, created_at REAL NOT NULL, PRIMARY KEY (cache_scope, align_key));"
        )
    Store(path)
    Store(path)  # reopening is a no-op
    with sqlite3.connect(path) as conn:
        assert "reference" in {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
        assert "analysis_id" in {r[1] for r in conn.execute("PRAGMA table_info(alignments)")}


def test_a_worker_reuse_is_aligned_too(stack: Stack) -> None:
    """The url serves bytes already analysed in the scope: the worker reuses the analysis and
    aligns it without analysing again."""
    from .cache_helpers import URL

    data = stack.audio("a.wav")
    plain = stack.analysis_id(analyse(stack, data))
    stack.fetch_bytes = data
    res = stack.post({"cache_scope": SCOPE, "url": URL, "reference": TRIMMED})
    assert res.status_code == 202
    view = job(stack, res)
    assert view["analysis_id"] != plain and len(stack.analysed) == 1
    doc = stack.get_analysis(view["analysis_id"]).json()
    assert doc["alignment"]["offset_s"] == -0.4
    assert_valid("analysis-v1", doc)
