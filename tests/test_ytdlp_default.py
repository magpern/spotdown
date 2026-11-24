"""YouTube sourcing is on by default (owner, 2026-11-24, spotdown#86); ``false`` turns it off."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings

ROOT = Path(__file__).resolve().parent.parent


def test_the_code_default_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALLOW_YTDLP_SOURCING", raising=False)
    assert Settings(_env_file=None).allow_ytdlp_sourcing is True  # type: ignore[call-arg]


@pytest.mark.parametrize("value", ["false", "0", "off"])
def test_the_operator_can_turn_it_off(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("ALLOW_YTDLP_SOURCING", value)
    assert Settings(_env_file=None).allow_ytdlp_sourcing is False  # type: ignore[call-arg]


def test_the_examples_show_the_default() -> None:
    lines = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    assert "ALLOW_YTDLP_SOURCING=true" in lines
    deploy = (ROOT / "deploy" / "spotdown.env.example").read_text(encoding="utf-8").splitlines()
    assert "ALLOW_YTDLP_SOURCING=true" in deploy  # live and explicit, not commented out


UI_KEY = "ui-caller-key-0d4b8f2a6c1e3957a2b3c4d5e6f7a8b9"


@pytest.mark.parametrize("who", ["classbeat", "ui"])
def test_off_disables_every_youtube_route_for_both_caller_keys(tmp_path: Path, who: str) -> None:
    """With the flag off, both caller keys (#82) get ``source_disabled`` from match, including a
    title-only track (#76), and from audio; the faked yt-dlp is never called."""
    from .audio_helpers import AudioStack
    from .service_helpers import TEST_KEY

    ui_file = tmp_path / "ui_key"
    ui_file.write_text(UI_KEY + "\n", encoding="utf-8")
    stack = AudioStack(tmp_path, allow_ytdlp_sourcing=False, ui_service_key_file=ui_file)
    auth = f"Bearer {TEST_KEY if who == 'classbeat' else UI_KEY}"
    for res in (
        stack.match(
            [{"client_ref": "t1", "title": "Blinding Lights"}], headers={"Authorization": auth}
        ),
        stack.request(Authorization=auth),
    ):
        assert res.status_code == 422, res.text
        assert res.json()["error"]["code"] == "source_disabled"
    assert stack.world.calls == []


class _FakeYoutubeDL:
    """Stands in for ``yt_dlp.YoutubeDL``: records every use and returns no candidates."""

    calls: list = []

    def __init__(self, opts: object) -> None:
        _FakeYoutubeDL.calls.append(("init", opts))

    def __enter__(self) -> "_FakeYoutubeDL":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def extract_info(self, query: str, download: bool = False) -> dict:
        _FakeYoutubeDL.calls.append(("extract_info", query))
        return {"entries": []}


@pytest.mark.parametrize("on", [False, True])
def test_the_standalone_source_obeys_the_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, on: bool
) -> None:
    """``app.sources.source_from_spotify`` (standalone mode, ``app/jobs.py``): off raises
    ``SourceError`` before yt-dlp is touched; on reaches the faked yt-dlp (the control that shows
    the fake is wired, so the off case can fail)."""
    import sys
    import types

    from app import sources
    from app.schemas import TrackMeta

    settings = Settings(
        data_dir=tmp_path / "data",
        redis_url="redis://127.0.0.1:1/0",
        allow_ytdlp_sourcing=on,
    )
    monkeypatch.setattr(sources, "get_settings", lambda: settings)
    fake = types.ModuleType("yt_dlp")
    fake.YoutubeDL = _FakeYoutubeDL  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "yt_dlp", fake)
    monkeypatch.setattr(_FakeYoutubeDL, "calls", [])
    meta = TrackMeta(id="t1", title="Blinding Lights", artists=["The Weeknd"], duration_s=200)

    with pytest.raises(sources.SourceError) as err:
        sources.source_from_spotify(meta, tmp_path, "t1")
    if on:
        assert "No source found" in str(err.value)
        assert [c[0] for c in _FakeYoutubeDL.calls] == ["init", "extract_info"]
    else:
        assert "ALLOW_YTDLP_SOURCING=false" in str(err.value)
        assert _FakeYoutubeDL.calls == []
