"""Loopback publishing and ignored forwarded headers (ADR 0003 item 6; spotdown#52
"Publishing"). Read from the files themselves; nothing is started."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent


def _services() -> Dict[str, List[str]]:
    """Each compose service's lines (a two-space-indented key under ``services:``)."""
    out: Dict[str, List[str]] = {}
    current = None
    for line in (ROOT / "docker-compose.yml").read_text(encoding="utf-8").splitlines():
        top = re.match(r"  ([a-z0-9_-]+):\s*$", line)
        if top:
            current = top.group(1)
            out[current] = []
        elif current and (line.startswith("    ") or not line.strip()):
            out[current].append(line)
        elif line.strip() and not line.startswith(" "):
            current = None
    return out


def _ports(lines: List[str]) -> List[str]:
    values, inside = [], False
    for line in lines:
        if re.match(r"    ports:\s*$", line):
            inside = True
        elif inside and re.match(r"      - ", line):
            values.append(line.split("- ", 1)[1].strip().strip("\"'"))
        elif inside and line.strip() and not line.lstrip().startswith("#"):
            inside = False
    return values


def test_compose_publishes_the_api_on_loopback_and_redis_not_at_all() -> None:
    services = _services()
    assert {"api", "redis", "worker"} <= set(services)
    api = _ports(services["api"])
    assert api and all(p.startswith("${SPOTDOWN_PUBLISH_HOST:-127.0.0.1}:") for p in api), api
    assert not any(re.match(r"\s+ports:", line) for line in services["redis"])
    assert not any(re.match(r"\s+ports:", line) for line in services["worker"])


def test_the_dockerfile_ignores_forwarded_headers() -> None:
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    cmd = [line for line in text.splitlines() if line.startswith("CMD ")]
    assert len(cmd) == 1
    argv = json.loads(cmd[0][4:])
    assert argv[:2] == ["uvicorn", "app.main:app"] and "--no-proxy-headers" in argv


def test_the_readme_runs_on_loopback_without_proxy_headers() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    runs = [line for line in text.splitlines() if re.match(r"\s*uvicorn app\.main:app", line)]
    assert runs
    for line in runs:
        assert "--host 127.0.0.1" in line and "--no-proxy-headers" in line, line


def test_the_app_never_reads_the_publish_host() -> None:
    for path in (ROOT / "app").rglob("*.py"):
        assert "SPOTDOWN_PUBLISH_HOST" not in path.read_text(encoding="utf-8"), path
