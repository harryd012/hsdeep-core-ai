#!/usr/bin/env python3
"""Round-trip the newest migration(s): downgrade, then upgrade to head.

`alembic downgrade -1` is ambiguous when head is a merge revision, so the
target is computed instead:

* ordinary head  -> its parent (same as `downgrade -1`);
* merge head     -> the nearest common ancestor of its parents, which
  reverts every migration on each merged line, then re-applies them.

Run from backend/ against a throwaway database (CI) only: the downgrade drops
the newest tables. Requires exactly one head (checked by quality_gates.sh).

    python ../ci/migration_roundtrip.py          # downgrade + upgrade
    python ../ci/migration_roundtrip.py --plan   # print the target only
"""
from __future__ import annotations

import subprocess
import sys

from alembic.config import Config
from alembic.script import ScriptDirectory


def _parents(script: ScriptDirectory, rev: str) -> tuple[str, ...]:
    down = script.get_revision(rev).down_revision
    if down is None:
        return ()
    return tuple(down) if isinstance(down, (tuple, list)) else (down,)


def _ancestors(script: ScriptDirectory, rev: str) -> list[str]:
    """`rev` and all its ancestors, nearest first (breadth-first)."""
    seen, order, queue = set(), [], [rev]
    while queue:
        cur = queue.pop(0)
        if cur in seen:
            continue
        seen.add(cur)
        order.append(cur)
        queue.extend(_parents(script, cur))
    return order


def target_for(script: ScriptDirectory, head: str) -> str:
    parents = _parents(script, head)
    if not parents:
        return "base"
    if len(parents) == 1:
        return parents[0]
    common = set(_ancestors(script, parents[0]))
    for p in parents[1:]:
        common &= set(_ancestors(script, p))
    for rev in _ancestors(script, parents[0]):
        if rev in common:
            return rev
    return "base"


def main() -> int:
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    heads = script.get_heads()
    if len(heads) != 1:
        print(f"expected exactly one head, found {heads}", file=sys.stderr)
        return 1
    target = target_for(script, heads[0])
    print(f"migration round trip: head {heads[0]} -> downgrade to {target} -> upgrade head")
    if "--plan" in sys.argv:
        return 0
    for args in (["downgrade", target], ["upgrade", "head"]):
        result = subprocess.run([sys.executable, "-m", "alembic", *args])
        if result.returncode != 0:
            return result.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
