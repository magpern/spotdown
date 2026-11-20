"""The CC catalog and its fetch: provenance and annotations committed, audio never, no network."""

from __future__ import annotations

import copy
import hashlib
import json
import urllib.request
from pathlib import Path
from typing import Any, Dict

import pytest

from app.quality import cc_catalog, cc_fetch
from app.quality.corpus_calc import CorpusError
from app.quality.corpus_io import load_corpus

DATA = b"RIFF0000WAVEfmt "
TRUTH = {"bpm": 120, "beats": [1, 2], "boundaries": [30], "drops": [], "edges": [0, 60]}


def entry(**more: Any) -> Dict[str, Any]:
    e: Dict[str, Any] = {
        "id": "song-1",
        "title": "Song",
        "artist": "Artist",
        "licence": "CC-BY-4.0",
        "source_url": "https://example.org/song",
        "download_url": "https://example.org/song.wav",
        "sha256": hashlib.sha256(DATA).hexdigest(),
        "bytes": len(DATA),
        "extension": "wav",
        "truth": TRUTH,
    }
    e.update(more)
    return e


def catalog(*tracks: Dict[str, Any]) -> Dict[str, Any]:
    return {"catalog_version": 1, "tracks": list(tracks)}


def test_the_committed_catalog_is_valid_and_carries_no_audio() -> None:
    tracks = cc_catalog.validate(json.loads(cc_fetch.CATALOG.read_text(encoding="utf-8")))
    assert len(tracks) == len(set(t["id"] for t in tracks))  # empty until the owner adds tracks


@pytest.mark.parametrize(
    "bad",
    [
        {"licence": "CC-BY-NC-4.0"},
        {"licence": "all rights reserved"},
        {"download_url": "http://example.org/a.wav"},
        {"download_url": "https://user:pw@example.org/a.wav"},
        {"sha256": "abc"},
        {"bytes": 31 * 1024 * 1024},
        {"extension": "flac"},
        {"id": "Has Space"},
        {"title": ""},
        {"truth": {"bpm": 120}},
    ],
)
def test_a_bad_catalog_entry_is_refused(bad: Dict[str, Any]) -> None:
    with pytest.raises(CorpusError):
        cc_catalog.validate(catalog(entry(**bad)))


def test_ids_must_be_unique() -> None:
    with pytest.raises(CorpusError):
        cc_catalog.validate(catalog(entry(), entry()))


def test_fetch_writes_audio_and_an_index_the_runner_can_load(tmp_path: Path) -> None:
    tracks = cc_catalog.validate(catalog(entry()))
    calls = []

    def fetcher(url: str, limit: int) -> bytes:
        calls.append((url, limit))
        return DATA

    assert cc_fetch.fetch(tracks, tmp_path, fetcher) == ["song-1"]
    index, audio = load_corpus(tmp_path)
    assert index["corpus"] == "cc" and audio["song-1"].read_bytes() == DATA
    assert index["tracks"][0]["provenance"]["title"] == "Song"
    assert cc_fetch.fetch(tracks, tmp_path, fetcher) == [] and len(calls) == 1  # already there


def test_a_download_that_does_not_match_is_refused_and_not_kept(tmp_path: Path) -> None:
    tracks = cc_catalog.validate(catalog(entry()))
    with pytest.raises(CorpusError):
        cc_fetch.fetch(tracks, tmp_path, lambda url, limit: DATA + b"x")
    assert not list((tmp_path / "audio").iterdir())


def test_fetch_refuses_a_destination_inside_the_repository() -> None:
    tracks = cc_catalog.validate(catalog(entry()))
    with pytest.raises(CorpusError):
        cc_fetch.fetch(tracks, cc_fetch.REPO / "quality" / "fixtures" / "cc", lambda u, n: DATA)


def test_a_redirect_may_not_leave_https() -> None:
    req = urllib.request.Request("https://example.org/a")
    handler = cc_fetch.HttpsOnly()
    with pytest.raises(CorpusError):
        handler.redirect_request(req, None, 302, "Found", {}, "http://example.org/b")  # type: ignore[arg-type]


def test_the_catalog_helper_does_not_mutate_its_input() -> None:
    c = catalog(entry())
    before = copy.deepcopy(c)
    cc_catalog.validate(c)
    assert c == before
