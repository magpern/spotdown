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
    assert "#ALLOW_YTDLP_SOURCING=true" in deploy
