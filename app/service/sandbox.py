"""Runs the core analyser in a subprocess with rlimits on memory, CPU time and decoded length.

The worker process never decodes untrusted audio itself: a crash, a runaway decode or an
allocation storm ends the child, and the worker maps it to a failure reason and carries on.
The limits sit inside the container's own ceiling; there is no seccomp or Landlock layer
(classbeat plan §8).
"""

from __future__ import annotations

import json
import os
import resource
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from . import sandbox_child as child
from .failure_policy import JobFailure, Reason

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ENV_ALLOWLIST = ("PATH", "LANG", "LC_ALL", "NUMBA_CACHE_DIR")
_THREAD_VARS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMBA_NUM_THREADS")
RESULT_MAX_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class Limits:
    memory_bytes: int
    cpu_s: int
    wall_s: float
    decoded_s: float


def _apply_limits(limits: Limits) -> None:
    """Runs in the child between fork and exec, before any untrusted byte is read."""
    resource.setrlimit(resource.RLIMIT_AS, (limits.memory_bytes, limits.memory_bytes))
    resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_s, limits.cpu_s + 5))
    resource.setrlimit(resource.RLIMIT_FSIZE, (RESULT_MAX_BYTES, RESULT_MAX_BYTES))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _child_env(work: Path) -> Dict[str, str]:
    env = {k: os.environ[k] for k in _ENV_ALLOWLIST if k in os.environ}
    env.update({v: "1" for v in _THREAD_VARS})
    env.update({"HOME": str(work), "TMPDIR": str(work), "PYTHONDONTWRITEBYTECODE": "1"})
    return env


def _children_cpu_s() -> float:
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return usage.ru_utime + usage.ru_stime


def _reason_for(returncode: int, cpu_used_s: float, limits: Limits) -> Reason:
    if returncode == child.EXIT_DECODE:
        return Reason.DECODE
    if returncode == child.EXIT_TOO_LONG:
        return Reason.TOO_LONG
    if returncode == child.EXIT_MEMORY:
        return Reason.MEMORY
    if returncode == -signal.SIGXCPU:
        return Reason.CPU_LIMIT
    if returncode == -signal.SIGKILL:
        # The hard CPU limit kills with SIGKILL too; a kill well inside the CPU budget is the
        # kernel's (or the container's) out-of-memory killer.
        return Reason.CPU_LIMIT if cpu_used_s >= limits.cpu_s else Reason.MEMORY
    return Reason.ANALYSIS


def run_analysis(audio: Path, media: str, work: Path, limits: Limits) -> Dict[str, Any]:
    """The child's result (``core``, ``probe``, ``components``, ``timings_s``) or JobFailure."""
    result = work / "result.json"
    result.unlink(missing_ok=True)
    cpu_before = _children_cpu_s()
    cmd = [
        sys.executable,
        "-E",
        "-s",
        "-m",
        "app.service.sandbox_child",
        str(audio),
        str(result),
        repr(float(limits.decoded_s)),
        media,
    ]
    proc = subprocess.Popen(
        cmd,
        cwd=str(PROJECT_ROOT),
        env=_child_env(work),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        preexec_fn=lambda: _apply_limits(limits),  # noqa: PLW1509
        start_new_session=True,
        close_fds=True,
    )
    try:
        returncode = proc.wait(timeout=limits.wall_s)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        raise JobFailure(Reason.DEADLINE) from None
    finally:
        _kill_group(proc)
    if returncode != child.EXIT_OK:
        raise JobFailure(_reason_for(returncode, _children_cpu_s() - cpu_before, limits))
    try:
        return json.loads(result.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise JobFailure(Reason.ANALYSIS) from None
    finally:
        result.unlink(missing_ok=True)


def _kill_group(proc: subprocess.Popen) -> None:
    """Ends the child and anything it started (a decoder), if still running."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
