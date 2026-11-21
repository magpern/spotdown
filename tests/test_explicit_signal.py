"""``content.explicit`` through the service (spotdown#36), with a faked yt-dlp and no network: the
caller's flag of the analysis request and of the match that chose a video, and the video's age
limit, are carried into the analysis as evidence. Report only: no flag stops, refuses or changes
a job."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from app.service.store import Store

from .cache_helpers import Stack
from .service_helpers import SCOPE, assert_valid
from .youtube_fakes import YoutubeStack, entry

VID = "dQw4w9WgXcQ"
TRACK = {
    "client_ref": "c",
    "title": "Blinding Lights",
    "artists": ["The Weeknd"],
    "duration_s": 12.0,
}


def _explicit(stack: Stack, res: Any, scope: Optional[str]) -> Dict[str, Any]:
    if isinstance(stack, YoutubeStack):
        analysis_id = stack.done_analysis_id(res, scope)
    else:
        analysis_id = stack.analysis_id(res, scope or SCOPE)
    doc = stack.get_analysis(analysis_id, scope).json()
    assert_valid("analysis-v1", doc)
    return doc["content"]["explicit"]


@pytest.mark.parametrize(
    "flag,value,evidence",
    [
        (None, "unknown", []),  # an upload carries nothing
        (True, "yes", [{"source": "caller", "value": True}]),
        (False, "no", [{"source": "caller", "value": False}]),
    ],
)
def test_an_upload_reports_only_the_callers_flag(tmp_path: Path, flag, value, evidence) -> None:
    stack = Stack(tmp_path)
    upload_id = stack.upload(stack.audio("a.wav")).json()["upload_id"]
    body: Dict[str, Any] = {"cache_scope": SCOPE, "upload_id": upload_id}
    if flag is not None:
        body["explicit"] = flag
    found = _explicit(stack, stack.post(body), SCOPE)
    assert (found["value"], found["evidence"], found["method"]) == (value, evidence, "metadata")


@pytest.mark.parametrize("bad", ["true", 1, 0, "yes", [True]])
def test_a_flag_that_is_not_a_json_bool_is_refused(tmp_path: Path, bad: Any) -> None:
    stack = YoutubeStack(tmp_path)
    res = stack.post({"source_ref": f"youtube:{VID}", "explicit": bad})
    assert res.status_code == 422 and res.json()["error"]["code"] == "invalid_request"
    assert stack.match([{**TRACK, "explicit": bad}]).status_code == 422
    assert stack.world.calls == []


@pytest.fixture()
def yt(tmp_path: Path) -> YoutubeStack:
    stack = YoutubeStack(tmp_path)
    stack.world.entries = [entry(VID, "Blinding Lights", "The Weeknd - Topic", 12.3)]
    return stack


def _age(stack: YoutubeStack, age_limit: Any) -> None:
    stack.world.infos[VID] = {"id": VID, "duration": 12.0, "age_limit": age_limit}


def test_an_age_restricted_video_is_yes_and_is_still_analysed(yt: YoutubeStack) -> None:
    _age(yt, 18)
    found = _explicit(yt, yt.post({"source_ref": f"youtube:{VID}"}), None)
    assert found == {
        "value": "yes",
        "confidence": 0.6,
        "method": "metadata",
        "evidence": [{"source": "youtube_age_limit", "value": 18}],
    }
    assert yt.world.downloads == [VID]


@pytest.mark.parametrize("age_limit", [0, 17, None, "18", True])
def test_no_age_restriction_is_no_evidence(yt: YoutubeStack, age_limit: Any) -> None:
    _age(yt, age_limit)
    assert _explicit(yt, yt.post({"source_ref": f"youtube:{VID}"}), None)["value"] == "unknown"


def test_the_match_flag_and_the_age_limit_conflict_to_yes(yt: YoutubeStack) -> None:
    _age(yt, 18)
    assert yt.match([{**TRACK, "explicit": False}], scope=SCOPE).json()["results"][0]["best"]
    found = _explicit(yt, yt.post({"source_ref": f"youtube:{VID}", "explicit": True}), None)
    assert found["value"] == "yes" and found["confidence"] == 0.5
    assert found["evidence"] == [
        {"source": "caller", "value": True},
        {"source": "caller_match", "value": False},
        {"source": "youtube_age_limit", "value": 18},
    ]


def test_the_latest_match_wins_for_the_flag_and_the_search_is_not_repeated(
    yt: YoutubeStack,
) -> None:
    yt.match([{**TRACK, "explicit": True}])
    yt.match([TRACK])  # the latest match that chose the video sent no flag
    assert len(yt.world.searches) == 1  # the flag is not part of the source-match cache key
    assert yt.store.choice_explicit(VID, yt.store.clock()) is None
    yt.match([{**TRACK, "explicit": False}])
    found = _explicit(yt, yt.post({"source_ref": f"youtube:{VID}"}), None)
    assert (found["value"], found["evidence"]) == (
        "no",
        [{"source": "caller_match", "value": False}],
    )


def test_an_expired_match_gives_no_flag(yt: YoutubeStack) -> None:
    yt.match([{**TRACK, "explicit": True}])
    later = yt.store.clock() + yt.store.global_ttl_s + 1
    assert yt.store.choice_explicit(VID, later) is None


def test_old_job_and_match_tables_gain_the_column(tmp_path: Path) -> None:
    path = tmp_path / "svc.sqlite"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            "CREATE TABLE jobs (job_id TEXT PRIMARY KEY, cache_scope TEXT, client_ref TEXT,"
            " source_kind TEXT NOT NULL, source_ref TEXT, upload_id TEXT, fetch_url TEXT,"
            " status TEXT NOT NULL, outcome TEXT, failure_kind TEXT, retry_after_s REAL,"
            " analysis_id TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL);"
            "CREATE TABLE match_choices (video_id TEXT PRIMARY KEY, record TEXT NOT NULL,"
            " duration_s REAL NOT NULL, created_at REAL NOT NULL);"
        )
    store = Store(path)
    Store(path)  # reopening is a no-op
    store.add_job({"job_id": "job_1", "source_kind": "upload", "explicit": True})
    assert store.get_job("job_1")["explicit"] is True  # type: ignore[index]
    store.add_job({"job_id": "job_2", "source_kind": "upload"})
    assert store.get_job("job_2")["explicit"] is None  # type: ignore[index]
    store.put_choice(VID, {"chosen": VID}, 12.0, 100.0, False)
    assert store.choice_explicit(VID, 101.0) is False
