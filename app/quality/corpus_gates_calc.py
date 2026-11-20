"""Gate verdicts for one external corpus (pure): pass, FAIL or not measured, never a silent pass.

Each corpus (one source) is judged alone against its own gate set from ``quality_gates.json``
(``external.gate_sets``); nothing is pooled across corpora, so a weak source cannot hide behind a
strong one. A gate whose metric could not be measured (no annotation, too few five-minute tracks)
is ``not_measured``; a corpus smaller than its rule is ``insufficient``. The verdict is ``FAIL``
when
any gate fails, otherwise ``incomplete`` when anything is not measured or insufficient, and
``pass`` only when everything is measured and passes. The hard gates are always measured.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .gates_calc import HARD, validate

PASS, FAIL, NOT_MEASURED = "pass", "FAIL", "not_measured"


def threshold(gates: Dict[str, Any], name: str) -> Dict[str, Any]:
    return gates["external"]["extra"].get(name) or gates["signal"][name]


def _verdict(rule: Dict[str, Any], value: Optional[float]) -> str:
    if value is None:
        return NOT_MEASURED
    ok = value >= rule["value"] if rule["op"] == ">=" else value <= rule["value"]
    return PASS if ok else FAIL


def evaluate_corpus(
    gates: Dict[str, Any],
    corpus: str,
    metrics: Dict[str, Optional[float]],
    coverage: Dict[str, Any],
) -> Dict[str, Any]:
    validate(gates)
    rows: List[Dict[str, Any]] = []
    for name in gates["external"]["gate_sets"][corpus]:
        rule = threshold(gates, name)
        value = metrics.get(name)
        rows.append(
            {
                "gate": name,
                "op": rule["op"],
                "threshold": rule["value"],
                "value": value,
                "n": metrics.get(f"n_{name}"),
                "status": _verdict(rule, value),
            }
        )
    for name in HARD:
        value = metrics.get(name)
        rows.append(
            {
                "gate": name,
                "op": "==",
                "threshold": 0,
                "value": value,
                "n": metrics.get(f"n_{name}"),
                "status": NOT_MEASURED if value is None else (PASS if value == 0 else FAIL),
            }
        )
    failed = [r["gate"] for r in rows if r["status"] == FAIL]
    unmeasured = [r["gate"] for r in rows if r["status"] == NOT_MEASURED]
    if failed:
        verdict = FAIL
    elif unmeasured or not coverage["sufficient"]:
        verdict = "incomplete"
    else:
        verdict = PASS
    return {
        "corpus": corpus,
        "verdict": verdict,
        "coverage": coverage,
        "failed": failed,
        "not_measured": unmeasured,
        "gates": rows,
    }
