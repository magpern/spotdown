"""Every test module imports without ``fcntl`` (simulated), so the suite still loads on Windows."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SCRIPT = r"""
import importlib, pathlib, sys
sys.modules["fcntl"] = None  # any import of fcntl now raises ImportError
import pytest
failed = []
for path in sorted(pathlib.Path("tests").glob("test_*.py")):
    try:
        importlib.import_module("tests." + path.stem)
    except pytest.skip.Exception:
        pass  # pytest.importorskip("fcntl"): the module skips, as on Windows
    except Exception as exc:
        failed.append(f"{path.name}: {type(exc).__name__}: {exc}")
print("\n".join(failed) if failed else "ok")
"""


def test_every_test_module_imports_without_fcntl(tmp_path: Path) -> None:
    env = {**os.environ, "DATA_DIR": str(tmp_path / "data"), "REDIS_URL": "redis://127.0.0.1:1/0"}
    out = subprocess.run(
        [sys.executable, "-c", SCRIPT],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().splitlines()[-1] == "ok", out.stdout[-2000:]
