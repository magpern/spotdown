from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator

import pytest

from . import netguard

# Importing app.main builds the module-level app, which creates the standalone token in its
# data_dir (ADR 0003): point that at a temporary directory, never the checkout's ./data.
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="spotdown-test-data-")
os.environ.pop("STANDALONE_ACCESS_TOKEN_FILE", None)


@pytest.fixture(autouse=True)
def _no_network() -> Iterator[None]:
    """Every test runs with non-loopback connections and name lookups blocked."""
    with netguard.install():
        yield
