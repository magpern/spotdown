"""Operator takedown of one public source: ``python -m app.service.takedown youtube:<video id>``.

Removes every global analysis, source link, alignment and match record held for that source and
prints counts only. A command for the operator on the host, not an endpoint: nothing here is
reachable over HTTP. Safe to run again (a second run reports zeros). Scoped (per-caller) data is
never touched; it goes with its scope (``DELETE /v2/scopes/{cache_scope}``).
"""

from __future__ import annotations

import sys
from typing import List, Optional

from ..config import Settings
from .retention_policy import parse_takedown_ref
from .store import Store


def run(source: str, store: Store) -> Optional[str]:
    """The counts line for ``source``, or None when it is not a global source reference."""
    parsed = parse_takedown_ref(source)
    if parsed is None:
        return None
    counts = store.takedown(*parsed)
    return " ".join(f"{name}={counts[name]}" for name in sorted(counts))


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -m app.service.takedown <kind>:<ref>  (for example youtube:<id>)")
        return 2
    settings = Settings()
    line = run(args[0], Store(settings.service_db_path, settings.global_reference_ttl_s))
    if line is None:
        print("not a global source reference (expected youtube:<video id>)")
        return 2
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
