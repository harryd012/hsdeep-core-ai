#!/usr/bin/env python3
"""Frontend ESLint ratchet gate (Phase 9A).

The repository carries PRE-EXISTING lint debt in application source (recorded in
``ci/eslint_baseline.json``): React/Next rules such as setState-in-effect,
prefer-const, unescaped entities and no-explicit-any in files that Phase 9A did
not touch. Clearing that debt means editing unrelated UI code, which this phase
explicitly must not do.

The risk this gate removes is a *regression*: no NEW lint error may be
introduced. It runs ESLint with a JSON report, sums errors per scope, and fails
when a scope exceeds its recorded baseline. Warnings are recorded but not gated
(they are unused-variable noise in tooling scripts).

Usage:
    python ci/lint_ratchet.py            # gate (CI)
    python ci/lint_ratchet.py --update   # re-record the baseline (deliberate)
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FRONTEND = REPO / "frontend"
BASELINE = Path(__file__).with_name("eslint_baseline.json")
SCOPES = {"src": ["src"], "repo": ["."]}


def _eslint_command(targets: list[str]) -> list[str]:
    """Resolve npx without shell=True.

    On Windows `npx` is a .cmd shim: running it through cmd.exe (shell=True)
    does not yield captured stdout, so the executable is resolved explicitly
    (shutil.which is PATHEXT-aware) and invoked directly. Works unchanged on
    Linux CI, where `npx` is a normal binary.
    """
    exe = shutil.which("npx") or ("npx.cmd" if sys.platform == "win32" else "npx")
    return [exe, "eslint", *targets, "-f", "json"]


def _eslint(targets: list[str]) -> dict[str, int]:
    proc = subprocess.run(
        _eslint_command(targets),
        cwd=FRONTEND,
        capture_output=True,
        text=True,
        # ESLint's JSON report can contain non-ASCII bytes (file paths, rule
        # messages). Without an explicit encoding, Windows decodes with cp1252
        # and the reader thread dies with UnicodeDecodeError, yielding no stdout.
        encoding="utf-8",
        errors="replace",
    )
    raw = (proc.stdout or "").strip()
    start = raw.find("[")
    if start < 0:
        print(f"eslint produced no JSON report (exit {proc.returncode})")
        print((proc.stderr or "")[-800:])
        return {"errors": -1, "warnings": -1}
    report = json.loads(raw[start:])
    return {
        "errors": sum(int(f.get("errorCount", 0)) for f in report),
        "warnings": sum(int(f.get("warningCount", 0)) for f in report),
    }


def main() -> int:
    update = "--update" in sys.argv
    measured = {name: _eslint(targets) for name, targets in SCOPES.items()}

    if update:
        BASELINE.write_text(json.dumps(measured, indent=2) + "\n", encoding="utf-8")
        print(f"baseline updated: {measured}")
        return 0

    if not BASELINE.exists():
        print(f"missing baseline file {BASELINE} - run with --update once, deliberately")
        return 1

    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    failures: list[str] = []
    for name, counts in measured.items():
        allowed = baseline.get(name, {}).get("errors", 0)
        print(
            f"{name}: {counts['errors']} error(s) / {counts['warnings']} warning(s) "
            f"(baseline {allowed} error(s))"
        )
        if counts["errors"] < 0:
            failures.append(f"{name}: eslint could not run")
        elif counts["errors"] > allowed:
            failures.append(
                f"{name}: {counts['errors'] - allowed} NEW lint error(s) "
                f"({counts['errors']} > baseline {allowed})"
            )

    if failures:
        print("\nLINT RATCHET FAILED:")
        for failure in failures:
            print("  -", failure)
        return 1
    print("\nLINT RATCHET PASSED: no new lint errors.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
