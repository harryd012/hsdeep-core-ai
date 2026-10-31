#!/usr/bin/env python3
"""Runtime-dependency completeness gate (Phase 9A).

The production image installs ``backend/requirements.txt`` ONLY (dev tooling was
split out into requirements-dev.txt). A runtime import that is not declared there
would fail at container start - and since a full image build is not available in
every environment, the check is done statically and deterministically:

  * every third-party top-level module imported by ``app/`` must map to a
    declared distribution in requirements.txt;
  * import names are mapped to distribution names via an explicit alias table
    (that mapping is not derivable in general - e.g. ``jose`` -> ``python-jose``);
  * stdlib and first-party (``app``) modules are ignored.

Exits 0 when the runtime dependency set is complete, 1 otherwise (CI fails).
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP = REPO / "backend" / "app"
REQUIREMENTS = REPO / "backend" / "requirements.txt"

#: import name -> distribution name, for the packages this application uses.
ALIASES = {
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "sqlalchemy": "sqlalchemy",
    "alembic": "alembic",
    "asyncpg": "asyncpg",
    "redis": "redis",
    "pydantic": "pydantic",
    "pydantic_settings": "pydantic-settings",
    "jose": "python-jose",
    "passlib": "passlib",
    "multipart": "python-multipart",
    "httpx": "httpx",
    "apscheduler": "apscheduler",
    "cryptography": "cryptography",
    "pysnmp": "pysnmp",
    "asyncssh": "asyncssh",
    "winrm": "pywinrm",
    "azure": "azure-identity",
    "pyVmomi": "pyvmomi",
    "prometheus_client": "prometheus-client",
    "stripe": "stripe",
    "boto3": "boto3",
    "kubernetes": "kubernetes",
    "google": "google-cloud-compute",
    "dotenv": "python-dotenv",
    # Import-name aliases that differ from the distribution name:
    "email_validator": "email-validator",
    "pyVim": "pyvmomi",
    # starlette is a declared direct dependency (see requirements.txt).
    "starlette": "starlette",
    "requests": "requests",
}


def _declared() -> set[str]:
    names: set[str] = set()
    for raw in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name = re.split(r"[<>=!\[\s]", line, maxsplit=1)[0]
        names.add(name.strip().lower())
    return names


def _imported() -> set[str]:
    modules: set[str] = set()
    for path in APP.rglob("*.py"):
        try:
            # utf-8-sig: two app modules carry a UTF-8 BOM, which ast.parse
            # rejects ("invalid non-printable character U+FEFF") even though
            # Python itself imports them fine.
            source = path.read_text(encoding="utf-8-sig", errors="ignore")
            tree = ast.parse(source)
        except SyntaxError as exc:  # a broken module is its own problem
            print(f"WARNING: could not parse {path.relative_to(REPO)}: {exc}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules.add(node.module.split(".")[0])
    return {
        m
        for m in modules
        if m and m != "app" and m not in sys.stdlib_module_names
    }


def main() -> int:
    declared = _declared()
    imported = _imported()
    unknown = sorted(m for m in imported if m not in ALIASES)
    missing = sorted(
        ALIASES[m] for m in imported if m in ALIASES and ALIASES[m].lower() not in declared
    )

    print(f"declared distributions: {len(declared)}")
    print(f"third-party modules imported by app/: {len(imported)}")

    if unknown:
        print("\nUNMAPPED imports (add to ALIASES if they are real runtime deps):")
        for name in unknown:
            print("  -", name)
    if missing:
        print("\nMISSING from requirements.txt (would fail in the production image):")
        for name in missing:
            print("  -", name)

    if unknown or missing:
        print("\nRUNTIME DEPENDENCY CHECK FAILED")
        return 1
    print("\nRUNTIME DEPENDENCY CHECK PASSED: every runtime import is declared.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
