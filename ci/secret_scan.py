#!/usr/bin/env python3
"""Repository secret scan - Phase 9A CI gate (offline, dependency-free).

Proves "no credential material is committed" without downloading an external
scanner, so the gate runs in any CI.

Severity
--------
* FATAL - shipping paths: application code, scripts, deployment templates, CI
  config, frontend source, browser harnesses, and the backend root diagnostic
  scripts. A single finding fails the pipeline.
* WARN  - local scratch (`_*.py`, `_*.js`, ...) which is not part of the app or
  of any deployment. Printed, never fatal.

Deliberately NOT scanned: test fixtures (`tests/`), virtualenvs, node_modules,
build output and `.env*` files (never committed - `.env.example` IS scanned
because it is committed and must stay placeholder-only).

Exit code 0 = clean, 1 = FATAL findings.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()

SKIP_DIR_NAMES = {
    ".git", ".venv", "venv", "node_modules", ".next", "out", "build", "dist",
    "__pycache__", ".pytest_cache", ".hypothesis", "htmlcov", "coverage",
    "tests", "_artifacts",
}
SKIP_SUFFIXES = (
    ".log", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".pyc",
)
SCAN_SUFFIXES = (
    ".py", ".ts", ".tsx", ".js", ".cjs", ".mjs", ".json", ".yml", ".yaml",
    ".sh", ".ps1", ".example",
)

#: Shipping/committed-configuration prefixes -> a finding here is FATAL.
FATAL_PREFIXES = (
    "backend/app/", "backend/scripts/", "backend/alembic/", "deployment/",
    "ci/", "frontend/src/", "frontend/tests/", ".github/",
)

#: Obvious placeholders / fixture values - never a real credential.
ALLOW_MARKERS = (
    "changeme", "example", "your-", "placeholder", "dummy", "fake", "test",
    "public", "password123", "whatever123", "notreal", "replace-me", "replace_me", "xxx",
    "<", ">", "${", "$(", "{{", "server_default", "column_default",
)

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Any identifier ENDING in a credential word (db_password, API_KEY,
    # client_secret, auth_token...). A plain \b before the keyword would miss
    # every snake_case name (`db_password`: `_` is a word char), which is how
    # secrets are usually written - caught by the scanner's own self-test.
    ("credential assignment", re.compile(
        r"(?i)[A-Za-z0-9_\-]*(?:password|passwd|passhash|pass_hash|secret|"
        r"api_?key|apikey|access_?key|private_?key|client_?secret|"
        r"(?<!error_)token)"
        r"\s*[:=]\s*[\"']([^\"']{8,})[\"']"
    )),
    ("fernet-style key", re.compile(r"[\"']([A-Za-z0-9_\-]{43}=)[\"']")),
    ("pem private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("aws access key id", re.compile(r"\b(AKIA[0-9A-Z]{16})\b")),
    ("jwt-shaped string", re.compile(
        r"\b(eyJ[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,})\b"
    )),
]


def _mask(snippet: str) -> str:
    """Never echo a candidate secret into CI logs."""
    return re.sub(r"[\"'][^\"']{4,}[\"']", '"***"', snippet)


def _matched_labels(line: str) -> set[str]:
    """Return the pattern labels that flag `line`, after the allow-marker check."""
    lowered = line.lower()
    if any(marker in lowered for marker in ALLOW_MARKERS):
        return set()
    return {label for label, pattern in PATTERNS if pattern.search(line)}


def _is_skipped(rel: Path) -> bool:
    if any(part in SKIP_DIR_NAMES for part in rel.parts):
        return True
    name = rel.name
    if name.startswith(".env") and name != ".env.example":
        return True
    return rel.suffix.lower() in SKIP_SUFFIXES


def _severity(rel: str) -> str:
    if rel.startswith(FATAL_PREFIXES) or rel == "backend/.env.example":
        return "FATAL"
    parts = rel.split("/")
    # Committed backend root-level scripts (test_auth.py, diagnose_usage.py, ...)
    if (
        len(parts) == 2
        and parts[0] == "backend"
        and parts[1].endswith(".py")
        and not parts[1].startswith("_")
    ):
        return "FATAL"
    return "WARN"


def _walk_files():
    """Every file under REPO, never descending into SKIP_DIR_NAMES.

    Pruning during the walk (rather than filtering an rglob afterwards) keeps
    the scan fast and avoids Windows MAX_PATH failures deep in node_modules.
    """
    for root, dirs, files in os.walk(REPO):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIR_NAMES)
        for name in sorted(files):
            yield Path(root) / name


def main() -> int:
    findings: list[tuple[str, str, int, str, str]] = []
    for path in _walk_files():
        if not path.is_file() or path == SELF:
            continue
        if path.suffix.lower() not in SCAN_SUFFIXES:
            continue
        rel_path = path.relative_to(REPO)
        if _is_skipped(rel_path):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        rel = rel_path.as_posix()
        for lineno, line in enumerate(text.splitlines(), start=1):
            for label in _matched_labels(line):
                findings.append((_severity(rel), rel, lineno, label, _mask(line)))

    fatal = [f for f in findings if f[0] == "FATAL"]
    for severity, rel, lineno, label, snippet in findings:
        print(f"[{severity}] {rel}:{lineno}: {label}: {snippet}")
    print(f"\nsecret scan: {len(fatal)} fatal, {len(findings) - len(fatal)} warning(s)")
    if fatal:
        print("SECRET SCAN FAILED: credential material on a shipping path.")
        return 1
    print("SECRET SCAN PASSED: no credential material on shipping paths.")
    return 0


def _self_test() -> int:
    """Negative control: prove each pattern detects a real-looking secret.

    A scanner that never fires is worthless, so CI runs this too.
    """
    samples = [
        ('API_KEY = "AKIA1234567890ABCDEF"', "aws access key id"),
        ('db_password = "Sup3rValue99xy"', "credential assignment"),
        ('key = "Zm9vYmFyYmF6cXV1eDEyMzQ1Njc4OTBhYmNkZWZnaGk="', "fernet-style key"),
        ("-----BEGIN RSA PRIVATE KEY-----", "pem private key"),
        ('t = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefghij"', "jwt-shaped string"),
        ('auth_token = "RealS3cret!123"', "credential assignment"),
        ('secret_token = "AnotherS3cret!789"', "credential assignment"),
        ('POSTGRES_PASSWORD: "Sup3rS3cret!"', "credential assignment"),
    ]
    must_not_flag = [
        ('QUOTA_ERROR_TOKEN = "device_quota_exceeded"', "credential assignment"),
        ('POSTGRES_PASSWORD: "REPLACE_ME_PG_PASSWORD"', "credential assignment"),
    ]
    failures: list[str] = []
    for line, expected in samples:
        found = _matched_labels(line)
        if expected not in found:
            failures.append(f"{expected!r} not detected in {line!r} (found {sorted(found)})")
    for line, label in must_not_flag:
        if label in _matched_labels(line):
            failures.append(f"{label!r} FALSELY detected in {line!r}")
    if failures:
        print("SELF-TEST FAILED:")
        for failure in failures:
            print("  -", failure)
        return 1
    print(
        f"SELF-TEST PASSED: {len(samples)} positives detected; "
        f"{len(must_not_flag)} false positives correctly excluded."
    )
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(_self_test())
    sys.exit(main())
