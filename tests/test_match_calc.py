"""The recording matcher on synthesised search metadata (classbeat plan §2): bands, version
markers, Topic and official channels, ISRC, the two duration checks and the decision."""

from __future__ import annotations

from typing import Any, Dict, Optional

import pytest

from app.youtube import match_calc as calc
from app.youtube import normalise_policy as norm

WANT = calc.Wanted("Blinding Lights", ("The Weeknd",), 200.0, "USUG11904206")
WANT_NO_ISRC = calc.Wanted("Blinding Lights", ("The Weeknd",), 200.0)
VID = "aaaaaaaaaaa"


def cand(title: str, channel: str = "Some Uploader", duration: float = 200.5, **kw: Any):
    return calc.Candidate(VID, title, channel, duration, rank=0, **kw)


# (title, channel, duration, extra, wanted, expected band or "never_confident")
HELLO = calc.Wanted("Hello", ("Adele",), 295.0)
LOVE_STORY = calc.Wanted("Love Story", ("Taylor Swift",), 235.0)

CASES = [
    # other versions named by a qualifier, and a longer title, from the release channel
    ("Hello (Club Mix)", "Adele - Topic", 295.0, {}, HELLO, "never_confident"),
    ("Hello (VIP Edit)", "Adele - Topic", 295.0, {}, HELLO, "never_confident"),
    ("Hello (Piano Version)", "Adele - Topic", 295.0, {}, HELLO, "never_confident"),
    ("Hello (Lofi Bootleg)", "Adele - Topic", 295.0, {}, HELLO, "never_confident"),
    ("Hello Goodbye", "Adele - Topic", 295.0, {}, HELLO, "never_confident"),
    (
        "Love Story (Taylor's Version)",
        "Taylor Swift - Topic",
        235.0,
        {},
        LOVE_STORY,
        "never_confident",
    ),
    ("Love Story (Re-Recorded)", "Taylor Swift - Topic", 235.0, {}, LOVE_STORY, "never_confident"),
    # qualifiers that name the release itself stay confident
    ("Hello (Original Mix)", "Adele - Topic", 295.0, {}, HELLO, "confident"),
    ("Hello (Remastered Version)", "Adele - Topic", 295.0, {}, HELLO, "confident"),
    ("Hello", "Adele - Topic", 295.0, {}, HELLO, "confident"),
    # the release master and the official channel are confident
    ("Blinding Lights", "The Weeknd - Topic", 200.4, {}, WANT_NO_ISRC, "confident"),
    (
        "The Weeknd - Blinding Lights (Official Audio)",
        "TheWeekndVEVO",
        201.0,
        {"channel_verified": True},
        WANT_NO_ISRC,
        "confident",
    ),
    # a VEVO name without verification is just a name
    (
        "The Weeknd - Blinding Lights (Official Audio)",
        "TheWeekndVEVO",
        201.0,
        {},
        WANT_NO_ISRC,
        "uncertain",
    ),
    (
        "The Weeknd - Blinding Lights (Official Video)",
        "The Weeknd",
        199.0,
        {"channel_verified": True},
        WANT_NO_ISRC,
        "confident",
    ),
    # an equal ISRC lifts a plain upload
    (
        "The Weeknd - Blinding Lights",
        "Lyrics Hub",
        200.5,
        {"isrc": "USUG11904206"},
        WANT,
        "confident",
    ),
    # a channel merely named after the artist is not official
    (
        "The Weeknd - Blinding Lights (Official Video)",
        "The Weeknd",
        200.0,
        {},
        WANT_NO_ISRC,
        "uncertain",
    ),
    # the right song on a random channel is only uncertain: no channel signal
    ("The Weeknd - Blinding Lights (Lyrics)", "Lyrics Hub", 200.5, {}, WANT_NO_ISRC, "uncertain"),
    # a different ISRC is never confident, even from a Topic channel
    (
        "Blinding Lights",
        "The Weeknd - Topic",
        200.4,
        {"isrc": "USUG12000001"},
        WANT,
        "never_confident",
    ),
    # wrong versions are never confident, whatever the channel or ISRC
    ("Blinding Lights (Live at the Grammys)", "The Weeknd", 200.2, {}, WANT_NO_ISRC, "not_same"),
    (
        "Blinding Lights (Chromatics Remix)",
        "The Weeknd - Topic",
        200.0,
        {},
        WANT_NO_ISRC,
        "not_same",
    ),
    (
        "The Weeknd - Blinding Lights (Sped Up)",
        "TheWeekndVEVO",
        200.0,
        {},
        WANT_NO_ISRC,
        "not_same",
    ),
    (
        "Blinding Lights - Slowed + Reverb",
        "The Weeknd - Topic",
        201.0,
        {},
        WANT_NO_ISRC,
        "not_same",
    ),
    ("Blinding Lights (Karaoke Version)", "Sing King", 200.0, {}, WANT_NO_ISRC, "not_same"),
    ("Blinding Lights (Instrumental)", "The Weeknd - Topic", 200.0, {}, WANT_NO_ISRC, "not_same"),
    ("Blinding Lights (Acoustic Cover)", "Some Singer", 200.0, {}, WANT_NO_ISRC, "not_same"),
    ("Blinding Lights (Nightcore)", "NightcoreReality", 200.0, {}, WANT_NO_ISRC, "not_same"),
    ("Blinding Lights (Extended Mix)", "The Weeknd - Topic", 200.0, {}, WANT_NO_ISRC, "not_same"),
    ("Blinding Lights (Radio Edit)", "The Weeknd - Topic", 199.0, {}, WANT_NO_ISRC, "not_same"),
    ("Blinding Lights 8D Audio", "8D Tunes", 200.0, {}, WANT_NO_ISRC, "not_same"),
    (
        "The Weeknd - Blinding Lights (Live)",
        "Lyrics Hub",
        200.5,
        {"isrc": "USUG11904206"},
        WANT,
        "not_same",
    ),
    # a duration more than 5 s off is rejected, even for the release master
    ("Blinding Lights", "The Weeknd - Topic", 206.0, {}, WANT_NO_ISRC, "not_same"),
    # another song by the artist
    ("Save Your Tears", "The Weeknd - Topic", 200.0, {}, WANT_NO_ISRC, "not_same"),
]


@pytest.mark.parametrize("title,channel,duration,extra,wanted,expected", CASES)
def test_bands_on_fixture_search_results(title, channel, duration, extra, wanted, expected):
    scored = calc.score(wanted, cand(title, channel, duration, **extra))
    assert scored.band == calc.band_for(scored.confidence)
    if expected == "never_confident":
        assert scored.band != "confident", scored
    else:
        assert scored.band == expected, scored


def test_a_version_on_both_sides_is_the_same_version() -> None:
    live = calc.Wanted("Blinding Lights (Live)", ("The Weeknd",), 200.0)
    scored = calc.score(live, cand("Blinding Lights (Live)", "The Weeknd - Topic", 200.0))
    assert scored.band == "confident"
    assert scored.signals["version_mismatch"] == []


@pytest.mark.parametrize(
    "title,artist,candidate,mismatch",
    [
        ("Live Forever", "Oasis", "Oasis - Live Forever (Official Video)", []),
        ("Live Forever", "Oasis", "Oasis - Live Forever (Live at Knebworth)", ["live"]),
        ("Lightning Crashes", "Live", "Live - Lightning Crashes", []),
        ("Lightning Crashes", "Live", "Live - Lightning Crashes (Live at Woodstock)", ["live"]),
        ("Song 2", "Blur", "Blur - Song 2 (2012 Remaster)", []),
    ],
)
def test_markers_in_titles_and_artist_names(title, artist, candidate, mismatch) -> None:
    wanted = calc.Wanted(title, (artist,), 120.0)
    scored = calc.score(wanted, cand(candidate, "Uploader", 120.0))
    assert scored.signals["version_mismatch"] == mismatch


@pytest.mark.parametrize(
    "delta,check",
    [
        (0.0, "ok"),
        (2.0, "ok"),
        (-2.0, "ok"),
        (2.01, "tolerable"),
        (-5.0, "tolerable"),
        (5.01, "rejected"),
        (-30.0, "rejected"),
    ],
)
def test_the_duration_check_before_download(delta: float, check: str) -> None:
    assert calc.duration_check(delta) == check
    scored = calc.score(WANT_NO_ISRC, cand("Blinding Lights", "The Weeknd - Topic", 200 + delta))
    assert scored.check == check and scored.delta_s == pytest.approx(delta)


@pytest.mark.parametrize(
    "confidence,band",
    [
        (1.0, "confident"),
        (0.85, "confident"),
        (0.849, "uncertain"),
        (0.6, "uncertain"),
        (0.599, "not_same"),
        (0.0, "not_same"),
    ],
)
def test_band_edges(confidence: float, band: str) -> None:
    assert calc.band_for(confidence) == band


def _entry(i: int, title: str, channel: str, duration: Optional[float], **kw: Any) -> Dict:
    return {"id": f"vid{i:08d}", "title": title, "channel": channel, "duration": duration, **kw}


def test_decide_ranks_best_and_at_most_two_plausible_alternates() -> None:
    entries = [
        _entry(1, "Blinding Lights (Live)", "The Weeknd", 200.0),
        _entry(2, "The Weeknd - Blinding Lights (Lyrics)", "Lyrics Hub", 201.0),
        _entry(3, "Blinding Lights", "The Weeknd - Topic", 200.3),
        _entry(4, "The Weeknd - Blinding Lights", "Fan Uploads", 203.0),
        _entry(5, "The Weeknd - Blinding Lights (Audio)", "Another Fan", 199.0),
        _entry(6, "Blinding Lights", "The Weeknd - Topic", None),  # no duration: dropped
        {"id": "https://evil.example/x", "title": "Blinding Lights", "duration": 200},
        "not a dict",
    ]
    candidates = [c for i, e in enumerate(entries) if (c := calc.candidate_from_entry(e, i))]
    assert len(candidates) == 5
    decision = calc.decide(WANT_NO_ISRC, candidates)
    assert decision.status == "matched"
    assert decision.best is not None and decision.best.candidate.video_id == "vid00000003"
    assert len(decision.alternates) == 2
    assert all(a.band != "not_same" for a in decision.alternates)
    assert "vid00000001" not in {a.candidate.video_id for a in decision.alternates}
    record = calc.match_record("the weeknd blinding lights", decision)
    assert record is not None and record["chosen"] == "vid00000003"
    assert record["matcher_version"] == calc.MATCHER_VERSION
    assert {c["ref"] for c in record["candidates"]} == {c.video_id for c in candidates}


def test_decide_statuses() -> None:
    assert calc.decide(WANT_NO_ISRC, []).status == "not_found"
    only_live = [cand("Blinding Lights (Live)", "The Weeknd - Topic", 200.0)]
    assert calc.decide(WANT_NO_ISRC, only_live).status == "not_found"
    plain = [cand("The Weeknd - Blinding Lights (Lyrics)", "Lyrics Hub", 200.0)]
    decided = calc.decide(WANT_NO_ISRC, plain)
    assert decided.status == "uncertain" and decided.best is not None


@pytest.mark.parametrize(
    "entry",
    [
        {"id": "short", "duration": 200},
        {"id": "aaaaaaaaaaa", "duration": None},
        {"id": "aaaaaaaaaaa", "duration": True},
        {"id": "aaaaaaaaaaa", "duration": 0},
        {"id": "aaaaaaaaaaa", "duration": 200, "live_status": "is_live"},
        {"id": "aaaaaaaaaa/", "duration": 200},
        {"id": "aaaaaaaaaaa\n", "duration": 200},
        {"url": "https://www.youtube.com/watch?v=aaaaaaaaaaa", "duration": 200},
    ],
)
def test_entries_that_cannot_be_validated_before_download_are_dropped(entry) -> None:
    assert calc.candidate_from_entry(entry, 0) is None


@pytest.mark.parametrize(
    "expected,file_s,content_s,result",
    [
        (200.0, 201.0, 199.0, "ok"),
        (200.0, 204.0, 199.5, "ok"),  # the silence-trimmed content is the closer hypothesis
        (200.0, 204.0, 196.0, "tolerable"),
        (200.0, 230.0, 222.0, "rejected"),
        (200.0, 30.0, 29.5, "rejected"),  # a preview, not the track
    ],
)
def test_the_duration_check_after_decode(expected, file_s, content_s, result) -> None:
    check = calc.decoded_duration_check(expected, "match", file_s, content_s)
    assert check["result"] == result
    assert check["file_delta_s"] == pytest.approx(file_s - expected)


def test_fingerprints_normalise_and_carry_the_matcher_version() -> None:
    def fp(**kw: Any) -> str:
        base = {
            "title": "Blinding Lights",
            "artists": ["The Weeknd"],
            "duration_s": 200.0,
            "isrc": None,
            "matcher_version": "m1",
        }
        return norm.fingerprint(**{**base, **kw})

    assert fp() == fp(title="BLINDING LIGHTS (Official Video)", artists=["the weeknd"])
    assert fp() == fp(duration_s=201.0)
    assert fp() != fp(duration_s=230.0)
    assert fp() != fp(title="Blinding Lights (Live)")
    assert fp() != fp(matcher_version="m2")
    assert fp(isrc="us-ug1-19-04206") == fp(isrc="USUG11904206", title="other")
    assert fp(isrc="not an isrc") == fp()
