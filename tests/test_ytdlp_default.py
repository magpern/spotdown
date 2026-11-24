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
