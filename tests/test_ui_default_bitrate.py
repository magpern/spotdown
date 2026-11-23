"""The standalone UI preselects the configured DEFAULT_BITRATE (no network, no provider)."""

from __future__ import annotations

import pytest

from app import standalone


@pytest.mark.parametrize("bitrate", [320, 256, 128])
def test_listed_default_is_the_only_selected_option(bitrate: int) -> None:
    html = standalone._index_html(bitrate)
    assert html.count("<option selected>") == 1
    assert f"<option selected>{bitrate}</option>" in html


def test_default_outside_the_listed_choices_is_added_and_selected() -> None:
    html = standalone._index_html(224)
    assert "<option selected>224</option>" in html
    assert html.count("<option selected>") == 1
