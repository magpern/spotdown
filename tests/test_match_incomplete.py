"""``POST /v2/match`` without ``duration_s`` or ``artists`` (spotdown#76): accepted, scored on what
the request has and never on the missing field, never ``confident``; a full request is scored
exactly as before."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest

from app.youtube import match_calc
from app.youtube import normalise_policy as norm

from .service_helpers import assert_valid
from .youtube_fakes import YoutubeStack, entry

TOPIC = "dQw4w9WgXcQ"
LYRICS = "lyricsvid01"


def track(**kw: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"client_ref": "t1", "title": "Blinding Lights"}
    out.update(kw)
    return out


@pytest.fixture()
def stack(tmp_path: Path) -> YoutubeStack:
    s = YoutubeStack(tmp_path)
    s.world.entries = [
        entry(LYRICS, "The Weeknd - Blinding Lights (Lyrics)", "Lyrics Hub", 201.0),
        entry(TOPIC, "Blinding Lights", "The Weeknd - Topic", 200.4),
    ]
    return s


def _result(stack: YoutubeStack, body: Dict[str, Any]) -> Dict[str, Any]:
    res = stack.match([body])
    assert res.status_code == 200, res.text
    (r,) = res.json()["results"]
    assert_valid("match-v1", r)
    return r


def test_no_duration_is_accepted_and_matched_without_a_duration_score(stack) -> None:
    r = _result(stack, track(artists=["The Weeknd"]))
    assert r["status"] == "uncertain"  # never confident with a missing field
    assert r["best"]["source"] == f"youtube:{TOPIC}"
    assert r["best"]["band"] == "uncertain"
    assert r["best"]["recording_match_confidence"] == match_calc.CAP_INCOMPLETE
    assert r["best"]["duration_delta_s"] is None and r["best"]["duration_check"] == "unknown"
    # No match record without a requested duration: a later analysis uses the video's metadata.
    with stack.store._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM match_choices").fetchone()[0] == 0


def test_no_artists_and_no_duration_is_accepted_and_matched(stack) -> None:
    r = _result(stack, track())
    assert r["status"] == "uncertain"
    assert r["best"]["duration_check"] == "unknown"
    assert r["best"]["recording_match_confidence"] <= match_calc.CAP_INCOMPLETE
    (search,) = stack.world.searches
    assert search.endswith(":blinding lights")  # the title alone, no artist


def test_no_artists_with_a_duration_is_scored_on_the_duration(stack) -> None:
    r = _result(stack, track(duration_s=200.0))
    assert r["status"] == "uncertain" and r["best"]["duration_check"] == "ok"
    far = _result(stack, track(client_ref="t2", duration_s=320.0))
    assert far["status"] == "not_found"  # a rejected duration still caps the score


def test_an_explicit_empty_artist_list_is_still_refused(stack) -> None:
    res = stack.match([track(artists=[], duration_s=200.0)])
    assert res.status_code == 422


WANTED = match_calc.Wanted("Blinding Lights", ("The Weeknd",), 200.0)
CAND = match_calc.Candidate(TOPIC, "Blinding Lights", "The Weeknd - Topic", 200.4, 0)


def test_a_full_request_scores_exactly_as_before() -> None:
    """The complete path is the old formula, unchanged."""
    s = match_calc.score(WANTED, CAND)
    dur = 1.0 - 0.1 * abs(0.4) / match_calc.OK_S
    assert s.confidence == round(min(1.0, 0.4 * 1.0 + 0.25 * 1.0 + 0.2 * 1.0 + 0.15 * dur), 3)
    assert (s.band, s.delta_s, s.check) == ("confident", 0.4, "ok")


def test_a_missing_duration_never_counts_as_a_mismatch() -> None:
    no_dur = match_calc.Wanted("Blinding Lights", ("The Weeknd",), None)
    s = match_calc.score(no_dur, CAND)
    assert (s.delta_s, s.check) == (None, "unknown")
    assert s.confidence == match_calc.CAP_INCOMPLETE  # title, artist, channel all perfect


def test_the_fingerprint_without_a_duration_is_its_own_key() -> None:
    def fp(**kw: Any) -> str:
        args: Dict[str, Any] = dict(
            title="t", artists=["a"], duration_s=200.0, isrc=None, matcher_version="v"
        )
        args.update(kw)
        return norm.fingerprint(**args)

    assert fp(duration_s=None) != fp() and fp(duration_s=None) != fp(duration_s=0.1)
    assert fp(duration_s=None) == fp(duration_s=None)
