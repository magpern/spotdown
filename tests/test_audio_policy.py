"""The pure rules of the mp3 route: ids, text cleaning, file names and retention."""

from __future__ import annotations

import pytest

from app.service import audio_policy as policy


def test_new_ids_match_the_pattern_and_differ() -> None:
    ids = {policy.new_audio_id() for _ in range(50)}
    assert len(ids) == 50 and all(policy.is_audio_id(i) for i in ids)


@pytest.mark.parametrize(
    "value",
    ["", "au_", "au_" + "0" * 31, "au_" + "g" * 32, "au_" + "0" * 32 + "\n", "../au_x", "an_x"],
)
def test_malformed_ids_are_refused(value: str) -> None:
    assert not policy.is_audio_id(value)


def test_text_drops_control_characters_and_is_capped() -> None:
    assert policy.clean_text("a\x00b\x1b[31m‮c​d", 50) == "ab[31mcd"
    assert policy.clean_text(" a\r\n\tb c ", 50) == "a b c"
    assert policy.clean_text("x" * 500, policy.TITLE_MAX) == "x" * policy.TITLE_MAX
    assert policy.clean_text(None, 10) == ""


def test_artists_are_cleaned_capped_and_empty_ones_dropped() -> None:
    got = policy.clean_artists(["A", "\x00", " ", "y" * 300] + ["z"] * 20)
    assert got[0] == "A" and got[1] == "y" * policy.ARTIST_MAX
    assert len(got) <= policy.ARTISTS_MAX and "" not in got


@pytest.mark.parametrize(
    "title,artists,want",
    [
        ("Song", ["A", "B"], "A, B - Song.mp3"),
        ("", [], "youtube-dQw4w9WgXcQ.mp3"),
        ("日本", ["Ж"], "youtube-dQw4w9WgXcQ.mp3"),
        ("Café/..\\x", [], "Cafe x.mp3"),
        ('a"b;c=d', [], "a b c d.mp3"),
    ],
)
def test_file_names_are_safe_ascii(title: str, artists: list, want: str) -> None:
    assert policy.file_name(title, artists, "dQw4w9WgXcQ") == want


def test_file_names_are_capped() -> None:
    name = policy.file_name("t" * 500, [], "dQw4w9WgXcQ")
    assert len(name) == policy.FILENAME_MAX + len(".mp3")


def test_retention() -> None:
    assert policy.expires_at(100.0, 600) == 700.0
    assert policy.is_kept(700.0, 699.9) and not policy.is_kept(700.0, 700.0)
    assert policy.job_record_ttl_s(600, 86400) == 86400
    assert policy.job_record_ttl_s(172800, 86400) == 172800


def test_the_bitrates_are_the_issue_set() -> None:
    assert policy.BITRATES == (128, 192, 256, 320) and policy.DEFAULT_BITRATE == 320
