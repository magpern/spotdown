"""The standalone UI's waveform view (spotdown#48): the pure model through tests/ui_harness.js, the
renderer's escaping and legend, and the assets (a fixed list, absent in service mode, no network).
No browser, no audio, no network."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List

import pytest

from app import standalone
from app.service import analysis_doc

from .service_helpers import AUTH, canned_core, client_for, service_settings

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
HARNESS = Path(__file__).with_name("ui_harness.js")
WAVEFORM_FILES = ["waveform_model.js", "waveform_view.js", "waveform_player.js"]
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")


def _doc() -> Dict[str, Any]:
    """40 s synthetic document: intro 0-20 (heuristic), chorus 20-40, build-up 2-20, drop at 20,
    beats every 0.5 s from 0.4 s and a downbeat every 2 s."""
    doc = analysis_doc.build(
        core=canned_core(40),
        probe={"codec": "mp3", "sample_rate_hz": 22050, "channels": 1},
        components={},
        sha256="c" * 64,
        source={"kind": "upload"},
        job_id="j",
        analysis_id="an_w",
        timings_s={},
        created_at="2026-11-21T10:00:00Z",
    )
    doc["grid"]["downbeats"] = {
        "times_s": [round(0.4 + 2 * i, 3) for i in range(20)],
        "confidence": 0.6,
    }
    return doc


def _call(mode_calls: List[Dict[str, Any]]) -> List[Any]:
    run = subprocess.run(
        ["node", str(HARNESS), str(STATIC), "call"],
        input=json.dumps(mode_calls),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(run.stdout)


def _one(fn: str, *args: Any) -> Any:
    return _call([{"fn": fn, "args": list(args)}])[0]


@needs_node
def test_peaks_of_a_known_buffer() -> None:
    assert _one("wfPeaks", [[0.1, -0.5, 0.2, 0.9]], 2) == [0.5, 0.9]
    assert _one("wfPeaks", [[0.1, -0.5], [0.0, 0.7]], 1) == [0.7]  # largest over channels
    assert _one("wfPeaks", [[0.25]], 3) == [0.25, 0.25, 0.25]  # more buckets than samples
    assert _one("wfPeaks", [[]], 2) == [0, 0]


@needs_node
def test_time_to_pixel_mapping_at_two_zoom_levels() -> None:
    whole, zoomed = {"t0": 0, "t1": 100, "w": 1000}, {"t0": 40, "t1": 60, "w": 1000}
    assert _one("wfX", whole, 50) == 500 and _one("wfX", whole, 12.5) == 125
    assert _one("wfX", zoomed, 50) == 500 and _one("wfX", zoomed, 45) == 250
    assert _one("wfX", zoomed, 40) == 0 and _one("wfX", zoomed, 30) == -500  # off-window: negative
    assert _one("wfT", whole, 125) == 12.5 and _one("wfT", zoomed, 250) == 45
    assert _one("wfT", zoomed, _one("wfX", zoomed, 52.3)) == pytest.approx(52.3)


@needs_node
def test_columns_follow_the_peaks_at_two_zoom_levels() -> None:
    peaks = [0, 1, 0.5, 0.25]  # a bucket per second of a 4 s track; the loudest scales to 1
    assert _one("wfColumns", peaks, 4, {"t0": 0, "t1": 4, "w": 4}) == [0, 1, 0.5, 0.25]
    assert _one("wfColumns", peaks, 4, {"t0": 1, "t1": 3, "w": 2}) == [1, 0.5]
    assert _one("wfColumns", peaks, 4, {"t0": 0, "t1": 4, "w": 2}) == [
        1,
        0.5,
    ]  # a column takes its max
    assert _one("wfColumns", peaks, 4, {"t0": 3, "t1": 7, "w": 4}) == [
        0.25,
        0,
        0,
        0,
    ]  # past the end


BEATS = [round(0.5 * i, 3) for i in range(600)]  # 300 s at 120 BPM
DOWN = BEATS[::4]


@needs_node
def test_beats_are_thinned_then_hidden_when_zoomed_out() -> None:
    zoomed = _one("wfBeatTicks", BEATS, DOWN, {"t0": 0, "t1": 30, "w": 800})  # 26 px/s
    assert len(zoomed["downbeats"]) == 16 and not zoomed["hidden"]
    assert len(zoomed["beats"]) == 61 - 16  # a downbeat is not drawn twice
    bars_only = _one(
        "wfBeatTicks", BEATS, DOWN, {"t0": 0, "t1": 300, "w": 800}
    )  # beat 1.3 px, bar 5.3 px
    assert (
        bars_only["beats"] == [] and len(bars_only["downbeats"]) == 150 and not bars_only["hidden"]
    )
    hidden = _one("wfBeatTicks", BEATS, DOWN, {"t0": 0, "t1": 300, "w": 400})  # bar 2.7 px
    assert hidden == {"beats": [], "downbeats": [], "hidden": True}
    no_bars = _one("wfBeatTicks", BEATS, None, {"t0": 0, "t1": 300, "w": 800})
    assert no_bars["hidden"] and no_bars["beats"] == []


@needs_node
def test_marker_positions_from_a_fixture_document() -> None:
    doc = {"document": _doc()}
    whole = _one("(d) => wfMarkers(buildModel(d), { t0: 0, t1: 40, w: 800 })", doc)
    intro, chorus = whole["sections"]
    assert (intro["x"], intro["w"], intro["label"], intro["heuristic"]) == (0, 400, "intro", True)
    assert (chorus["x"], chorus["w"], chorus["label"]) == (400, 400, "chorus")
    assert [d["x"] for d in whole["drops"]] == [400]
    assert [(b["x"], b["w"]) for b in whole["buildUps"]] == [(40, 360)]
    assert len(whole["downbeats"]) == 20 and whole["downbeats"][0] == 8  # 0.4 s
    assert len(whole["beats"]) == 79 - 20 and not whole["beatsHidden"]
    zoom = _one("(d) => wfMarkers(buildModel(d), { t0: 10, t1: 30, w: 800 })", doc)
    assert [(g["x"], g["w"]) for g in zoom["sections"]] == [(0, 400), (400, 400)]  # clipped
    assert [d["x"] for d in zoom["drops"]] == [400]
    assert [(b["x"], b["w"]) for b in zoom["buildUps"]] == [(0, 400)]  # 2-20 s clipped to 10-20
    late = _one("(d) => wfMarkers(buildModel(d), { t0: 30, t1: 40, w: 800 })", doc)
    assert (
        [g["x"] for g in late["sections"]] == [0] and late["drops"] == [] and late["buildUps"] == []
    )


@needs_node
def test_only_drops_and_build_ups_are_read_from_the_events() -> None:
    doc = _doc()
    doc["events"].append(
        {
            "kind": "breakdown",
            "start_s": 25.0,
            "end_s": 30.0,
            "strength": 0.5,
            "confidence": 0.5,
            "method": "estimated",
        }
    )
    doc["events"][0].pop("end_s", None)  # a build-up without an end is a thin mark, not a crash
    out = _one("(d) => wfMarkers(buildModel(d), { t0: 0, t1: 40, w: 800 })", {"document": doc})
    assert len(out["drops"]) == 1 and len(out["buildUps"]) == 1 and out["buildUps"][0]["w"] == 2


@needs_node
def test_click_schedule_marks_downbeats_strong() -> None:
    clicks = _one("wfClicksBetween", [0.4, 0.9, 1.4, 1.9, 2.4], [0.4, 2.4], 0.5, 2.4)
    assert clicks == [
        {"t": 0.9, "strong": False},
        {"t": 1.4, "strong": False},
        {"t": 1.9, "strong": False},
    ]
    assert _one("wfClicksBetween", [0.4, 0.9, 2.4], [0.4, 2.4], 0, 1) == [
        {"t": 0.4, "strong": True},
        {"t": 0.9, "strong": False},
    ]


@needs_node
def test_the_svg_escapes_document_text_and_the_legend_says_build_up_into_a_drop() -> None:
    doc = _doc()
    doc["labels"][0]["label"] = '<img src=x onerror="alert(1)">'
    run = subprocess.run(
        ["node", str(HARNESS), str(STATIC), "waveform"],
        input=json.dumps([{"document": doc}]),
        capture_output=True,
        text=True,
        check=True,
    )
    svg = json.loads(run.stdout)[0]
    assert "<img" not in svg and "&lt;img" in svg
    assert "build-up into a drop" in svg and "pre-chorus" not in svg.lower()
    assert not re.search(r"undefined|NaN", svg)
    assert 'id="wfHead"' in svg and "hatched = heuristic label" in svg


def test_the_waveform_assets_are_served_only_from_the_fixed_list(
    tmp_path: Path, monkeypatch
) -> None:
    from .test_ui_fields import _client

    on_disk = {p.name for p in STATIC.glob("waveform_*.js")}
    assert on_disk == set(WAVEFORM_FILES) and on_disk <= set(standalone.UI_ASSETS)
    with _client(tmp_path, monkeypatch) as client:
        page = client.get("/").text
        for name in WAVEFORM_FILES:
            assert f'src="{name}"' in page
            res = client.get(f"/{name}")
            assert res.status_code == 200 and res.content == (STATIC / name).read_bytes()
        assert client.get("/waveform_other.js").status_code == 404
        assert client.get("/..%2Fapp%2Fmain.py").status_code in (400, 404)


def test_the_waveform_assets_are_absent_in_service_mode(tmp_path: Path) -> None:
    client = client_for(service_settings(tmp_path))
    for name in WAVEFORM_FILES:
        for headers in ({}, AUTH):
            assert client.get(f"/{name}", headers=headers).status_code == 404


def test_the_page_stays_self_contained() -> None:
    """No external script, font or request: only the page's own files and the track's download."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"(src|href)=\"(https?:)?//", html)
    for name in WAVEFORM_FILES:
        js = (STATIC / name).read_text(encoding="utf-8")
        assert not re.search(r"https?://|XMLHttpRequest|WebSocket|sendBeacon|import\(", js), name
        assert "fetch(" not in js, (
            name
        )  # the player gets bytes from the page's loader, never fetches
    assert "@import" not in (STATIC / "ui.css").read_text(encoding="utf-8")


@needs_node
def test_an_undecodable_or_missing_audio_never_blocks_the_page() -> None:
    doc = _doc()
    cases = [
        {"ac": "none", "loader": "bytes"},  # no Web Audio
        {"ac": "ok", "loader": "null"},  # the page has no audio (track not converted here)
        {"ac": "ok", "loader": "throw"},  # the download failed
        {"ac": "ok", "loader": "bytes"},  # decodeAudioData rejects
    ]
    run = subprocess.run(
        ["node", str(HARNESS), str(STATIC), "fallback"],
        input=json.dumps([{"document": doc, **c} for c in cases]),
        capture_output=True,
        text=True,
        check=True,
    )
    results = json.loads(run.stdout)
    expected = ["no Web Audio", "no audio for the track", "download failed", "unable to decode"]
    for res, why in zip(results, expected):
        assert "No waveform" in res["host"] and why in res["host"], res
        assert "timeline below is unaffected" in res["host"] and res["playDisabled"] is True
        assert "<svg" not in res["host"]
