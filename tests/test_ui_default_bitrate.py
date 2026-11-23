"""The standalone UI preselects the configured DEFAULT_BITRATE (no network, no provider)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app import standalone
from app.main import create_app

from .standalone_helpers import generated_token, signed_in, standalone_settings


@pytest.mark.parametrize("bitrate", [320, 256, 128])
def test_listed_default_is_the_only_selected_option(bitrate: int) -> None:
    html = standalone._index_html(bitrate)
    assert html.count("<option selected>") == 1
    assert f"<option selected>{bitrate}</option>" in html


def test_default_outside_the_listed_choices_is_added_and_selected() -> None:
    html = standalone._index_html(224)
    assert "<option selected>224</option>" in html
    assert html.count("<option selected>") == 1


@pytest.mark.parametrize("bitrate", [320, 192])
def test_the_served_page_preselects_the_configured_default(
    tmp_path: Path, monkeypatch, bitrate: int
) -> None:
    settings = standalone_settings(tmp_path, default_bitrate=bitrate)
    monkeypatch.setattr(standalone, "get_settings", lambda: settings)
    with signed_in(create_app(settings), generated_token(settings)) as client:
        page = client.get("/")
    assert page.status_code == 200
    assert page.text.count("<option selected>") == 1
    assert f"<option selected>{bitrate}</option>" in page.text
