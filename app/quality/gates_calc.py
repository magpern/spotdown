"""Evaluates measured metrics against the versioned ``quality_gates.json`` (pure)."""

from __future__ import annotations

from typing import Any, Dict, List

HARD = ("false_exact", "audio_kept_after_job")


class GatesError(ValueError):
    """The gates file is malformed or tries to relax a hard gate."""


def validate(gates: Dict[str, Any]) -> None:
    """Hard gates are zero, always: a file that says otherwise is refused, never evaluated."""
    hard = gates.get("hard", {})
    for name in HARD:
        if hard.get(name) != 0:
            raise GatesError(f"hard gate {name} must be 0 and is never relaxed")
    for name, rule in gates["signal"].items():
        if rule["op"] not in (">=", "<="):
            raise GatesError(f"gate {name}: unknown operator {rule['op']!r}")


def evaluate(gates: Dict[str, Any], metrics: Dict[str, float]) -> List[Dict[str, Any]]:
    """One row per gate: the metric, the rule, the measured value and whether it passes.

    A gate whose metric is missing fails: a gate never passes by not being measured.
    """
    validate(gates)
    rows: List[Dict[str, Any]] = []
    for name, rule in gates["signal"].items():
        value = metrics.get(name)
        if value is None:
            ok = False
        elif rule["op"] == ">=":
            ok = value >= rule["value"]
        else:
            ok = value <= rule["value"]
        rows.append(
            {"gate": name, "op": rule["op"], "threshold": rule["value"], "value": value, "pass": ok}
        )
    for name in HARD:
        value = metrics.get(name)
        rows.append({"gate": name, "op": "==", "threshold": 0, "value": value, "pass": value == 0})
    return rows


def failures(rows: List[Dict[str, Any]]) -> List[str]:
    return [r["gate"] for r in rows if not r["pass"]]
