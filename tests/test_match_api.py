"""``POST /v2/match`` with a faked yt-dlp: schema-valid results in order, no download, the
source-match cache, the rate-limit backoff, every failure status and cached-analysis summaries."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest

from app.service import match_policy
from app.youtube import match_calc

from .cache_helpers import Stack
from .service_helpers import AUTH, assert_valid
from .youtube_fakes import YoutubeStack, entry

TOPIC = "dQw4w9WgXcQ"
LYRICS = "lyricsvid01"
LIVE = "livevideo01"


def track(ref: str = "t1", **kw: Any) -> Dict[str, Any]:
    return {"client_ref": ref, "title": "Blinding Lights", "artists": ["The Weeknd"],
            "duration_s": 200.0, **kw}  # fmt: skip


@pytest.fixture()
def stack(tmp_path: Path) -> YoutubeStack:
    s = YoutubeStack(tmp_path)
    s.world.entries = [
        entry(LIVE, "Blinding Lights (Live)", "The Weeknd", 200.0),
        entry(LYRICS, "The Weeknd - Blinding Lights (Lyrics)", "Lyrics Hub", 201.0),
        entry(TOPIC, "Blinding Lights", "The Weeknd - Topic", 200.4,
              url="https://www.youtube.com/watch?v=dQw4w9WgXcQ", thumbnails=[{"url": "x"}]),
    ]  # fmt: skip
    return s


def results(res: Any) -> list:
    assert res.status_code == 200, res.text
    assert set(res.json()) == {"results"}
    for r in res.json()["results"]:
        assert_valid("match-v1", r)
    return res.json()["results"]


def test_the_matcher_is_disabled_when_the_operator_turns_it_off(tmp_path: Path) -> None:
    off = Stack(tmp_path)
    res = off.client.post("/v2/match", headers=AUTH, json={"tracks": [track()]})
    assert res.status_code == 422 and res.json()["error"]["code"] == "source_disabled"


def test_a_match_returns_best_and_alternates_and_downloads_nothing(stack: YoutubeStack) -> None:
    (r,) = results(stack.match([track()]))
    assert r["status"] == "matched" and r["client_ref"] == "t1"
    assert r["best"]["source"] == f"youtube:{TOPIC}" and r["best"]["band"] == "confident"
    assert r["best"]["duration_check"] == "ok"
    assert [a["source"] for a in r["alternates"]] == [f"youtube:{LYRICS}"]
    assert r["matcher_version"] == match_calc.MATCHER_VERSION
    assert stack.world.downloads == [] and stack.dispatch.job_ids == [] and stack.analysed == []
    assert all(c[1].startswith("ytsearch") for c in stack.world.calls)
    assert "cached_analysis_id" not in r["best"]


def test_results_are_in_request_order_and_batches_are_bounded(stack: YoutubeStack) -> None:
    refs = [f"r{i}" for i in range(12)]
    got = results(stack.match([track(ref) for ref in refs]))
    assert [r["client_ref"] for r in got] == refs
    too_many = stack.match([track(f"r{i}") for i in range(51)])
    assert too_many.status_code == 422
    assert stack.match([]).status_code == 422


@pytest.mark.parametrize(
    "bad",
    [
        {"artists": []},
        {"duration_s": 0},
        {"title": ""},
        {"extra": "x"},
        {"client_ref": "x" * 129},
    ],
)
def test_invalid_tracks_are_refused(stack: YoutubeStack, bad: Dict[str, Any]) -> None:
    res = stack.match([track(**bad)])
    assert res.status_code == 422 and res.json()["error"]["code"] == "invalid_request"
    assert stack.world.calls == []


def test_the_source_match_cache_answers_without_searching(stack: YoutubeStack) -> None:
    first = results(stack.match([track()]))
    again = results(stack.match([track("t2", title="BLINDING LIGHTS (Official Video)")]))
    assert len(stack.world.searches) == 1
    assert again[0]["best"] == first[0]["best"]
    with stack.store._conn() as conn:
        (row,) = conn.execute("SELECT entries FROM match_cache").fetchall()
    assert "thumbnails" not in row["entries"] and "https://" not in row["entries"]


def test_misses_are_cached_for_a_shorter_time(stack: YoutubeStack) -> None:
    stack.world.entries = []
    (r,) = results(stack.match([track()]))
    assert r["status"] == "not_found" and "best" not in r and r["alternates"] == []
    with stack.store._conn() as conn:
        (row,) = conn.execute("SELECT expires_at FROM match_cache").fetchall()
    import time

    assert row["expires_at"] - time.time() <= match_policy.MISS_TTL_S + 1
    assert match_policy.MISS_TTL_S < match_policy.HIT_TTL_S
    assert match_policy.ttl_for(3) == match_policy.HIT_TTL_S


def test_only_wrong_versions_is_not_found_and_plain_uploads_are_uncertain(stack) -> None:
    stack.world.entries = [entry(LIVE, "Blinding Lights (Live)", "The Weeknd - Topic", 200.0)]
    (r,) = results(stack.match([track()]))
    assert r["status"] == "not_found"
    stack.world.entries = [entry(LYRICS, "The Weeknd - Blinding Lights", "Lyrics Hub", 230.0)]
    (r,) = results(stack.match([track(duration_s=230.0)]))  # another bucket: a fresh search
    assert r["status"] == "uncertain" and r["best"]["band"] == "uncertain"


@pytest.mark.parametrize(
    "message,status",
    [
        ("ERROR: Sign in to confirm you're not a bot", "provider_error"),
        ("ERROR: HTTP Error 503: Service Unavailable", "provider_error"),
        ("ERROR: HTTP Error 429: Too Many Requests", "rate_limited"),
    ],
)
def test_search_failures_are_per_track_statuses(stack: YoutubeStack, message, status) -> None:
    stack.world.search_error = message
    (r,) = results(stack.match([track()]))
    assert r["status"] == status and "best" not in r
    assert ("retry_after_s" in r) == (status == "rate_limited")
    with stack.store._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM match_cache").fetchone()[0] == 0


def test_a_rate_limit_backs_off_every_later_search(stack: YoutubeStack) -> None:
    stack.world.search_error = "ERROR: HTTP Error 429: Too Many Requests"
    stack.match([track("a")])
    stack.world.search_error = None
    (r,) = results(stack.match([track("b", title="Save Your Tears")]))
    assert r["status"] == "rate_limited" and 0 < r["retry_after_s"] <= 300
    assert len(stack.world.searches) == 1  # the second track never reached yt-dlp


def test_a_cached_analysis_is_reported_with_its_summary_in_any_scope(stack: YoutubeStack) -> None:
    res = stack.post({"source_ref": f"youtube:{TOPIC}"})
    analysis_id = stack.done_analysis_id(res)
    stack.world.calls.clear()
    for scope in (None, "scope_a", "scope_b"):
        (r,) = results(stack.match([track()], scope=scope))
        assert r["best"]["cached_analysis_id"] == analysis_id
        summary = r["best"]["cached_summary"]
        assert summary["bpm"] == 120.0 and summary["has_beats"] is True
        assert summary["analyzer_version"]
        assert all("cached_analysis_id" not in a for a in r["alternates"])
    assert stack.world.downloads == []


def test_a_scoped_analysis_is_never_reported_as_a_youtube_candidate(stack: YoutubeStack) -> None:
    stack.analysis_id(stack.analyse_bytes(stack.audio("a.wav")))
    (r,) = results(stack.match([track()], scope="scope_a"))
    assert "cached_analysis_id" not in r["best"]


def test_the_match_records_no_scope_or_caller_reference(stack: YoutubeStack) -> None:
    stack.match([track("secret-client-ref")], scope="scope_secret")
    with stack.store._conn() as conn:
        dump = "".join(
            str(tuple(row))
            for table in ("match_cache", "match_choices", "provider_backoff")
            for row in conn.execute(f"SELECT * FROM {table}")
        )
    assert "scope_secret" not in dump and "secret-client-ref" not in dump
    assert TOPIC in dump


def test_an_idempotency_key_replays_the_first_answer(stack: YoutubeStack) -> None:
    key = {"Idempotency-Key": "match-1"}
    first = stack.match([track()], headers=key)
    stack.world.entries = []
    again = stack.match([track()], headers=key)
    assert again.json() == first.json()
    other = stack.match([track("other")], headers=key)
    assert other.status_code == 409
    assert stack.match([track()], headers={"Idempotency-Key": "bad key!"}).status_code == 422


def test_a_bad_scope_is_refused(stack: YoutubeStack) -> None:
    res = stack.match([track()], scope="bad scope")
    assert res.status_code == 422 and stack.world.calls == []
