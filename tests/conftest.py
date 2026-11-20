from __future__ import annotations

from collections.abc import Iterator

import pytest

from . import netguard


@pytest.fixture(autouse=True)
def _no_network() -> Iterator[None]:
    """Every test runs with non-loopback connections and name lookups blocked."""
    with netguard.install():
        yield
