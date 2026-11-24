"""``scripts/release-assets.sh`` (spotdown#66): the files a release carries and their SHA256SUMS.
Runs the script into temporary directories; nothing is uploaded."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Dict

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "release-assets.sh"
EXPECTED = {
    "compose.yaml": "deploy/compose.yaml",
    "spotdown.env.example": "deploy/spotdown.env.example",
}


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, timeout=30)


def _sums(path: Path) -> Dict[str, str]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        out[name] = digest
    return out


def test_list_names_the_deploy_files() -> None:
    result = _run("list")
    assert result.returncode == 0, result.stderr
    pairs = {}
    for line in result.stdout.splitlines():
        src, name = line.split(" ", 1)
        pairs[name] = src
    assert pairs == EXPECTED


def test_build_copies_byte_for_byte_and_checksums_every_asset(tmp_path: Path) -> None:
    out = tmp_path / "assets"
    result = _run("build", str(out))
    assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in out.iterdir()) == sorted([*EXPECTED, "SHA256SUMS"])
    sums = _sums(out / "SHA256SUMS")
    assert set(sums) == set(EXPECTED)
    for name, src in EXPECTED.items():
        data = (out / name).read_bytes()
        assert data == (ROOT / src).read_bytes(), name
        assert sums[name] == hashlib.sha256(data).hexdigest(), name
    verify = subprocess.run(
        ["sha256sum", "-c", "SHA256SUMS"], cwd=out, capture_output=True, text=True, timeout=30
    )
    assert verify.returncode == 0, verify.stdout + verify.stderr


def test_build_refuses_a_directory_that_is_not_empty(tmp_path: Path) -> None:
    (tmp_path / "stray").write_text("x", encoding="utf-8")
    result = _run("build", str(tmp_path))
    assert result.returncode == 1
    assert "is not empty" in result.stderr
    assert sorted(p.name for p in tmp_path.iterdir()) == ["stray"]


@pytest.mark.parametrize("args", [(), ("publish",)])
def test_an_unknown_command_is_a_usage_error(args: tuple) -> None:
    assert _run(*args).returncode == 2
