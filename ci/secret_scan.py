#!/usr/bin/env python3
"""Repository secret scan - CI gate (offline, dependency-free).

Proves "no credential material is committed" without downloading an external
scanner, so the gate runs in any CI.

What is scanned
---------------
Every file git would commit: tracked files plus untracked files that are not
ignored (`git ls-files --cached --others --exclude-standard`). Ignored files
(`.env`, `node_modules`, build output) are never committed, so they are out
of scope. Without git the scanner walks the tree, pruning the same build and
dependency directories. Test code IS scanned.

Severity
--------
* FATAL - any high-confidence token format (private key, AWS/GitHub/Slack/
  Stripe/Google key, JWT, Fernet key) anywhere, and credential assignments
  in shipping paths (application code, scripts, deployment, CI, frontend).
* WARN  - credential assignments in tests and local scratch (`_*.py`, ...):
  tests legitimately assign throwaway passwords. Printed, never fatal.
* An unreadable file is FATAL: a scan that cannot read a file has not proved
  it clean.

False positives
---------------
Placeholder words are checked against the matched VALUE only, never the rest
of the line, so a comment or a type annotation cannot hide a real secret. A
deliberate fixture can carry `secret-scan: allow` on its own line.

Exit code 0 = clean, 1 = FATAL findings.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SELF_REL = "ci/secret_scan.py"

PRUNE_DIR_NAMES = {
    ".git", ".venv", "venv", "node_modules", ".next", "out", "build", "dist",
    "__pycache__", ".pytest_cache", ".hypothesis", "htmlcov", "coverage", "_artifacts",
}
SCAN_SUFFIXES = (
    ".py", ".ts", ".tsx", ".js", ".cjs", ".mjs", ".json", ".yml", ".yaml",
    ".sh", ".ps1", ".toml", ".ini", ".cfg", ".conf", ".md", ".txt", ".example",
    ".template", ".sample", ".env",
)
SCAN_NAMES = ("Dockerfile", "Procfile")
#: Lock files are machine-generated integrity hashes, not credentials.
SKIP_NAMES = ("pnpm-lock.yaml", "package-lock.json", "yarn.lock", "poetry.lock")

#: Shipping/committed-configuration prefixes: credential assignments are FATAL.
FATAL_PREFIXES = (
    "backend/app/", "backend/scripts/", "backend/alembic/", "deployment/",
    "ci/", "frontend/src/", ".github/", "render.yaml", "docker-compose",
)

#: Placeholder words that mark a VALUE as not a real credential.
PLACEHOLDER_WORDS = (
    "changeme", "change-me", "change_me", "example", "your-", "your_", "placeholder",
    "dummy", "fake", "notreal", "not-a-real", "replace-me", "replace_me", "xxx",
    "redacted", "<", "${", "$(", "{{", "%(",
)
ALLOW_PRAGMA = "secret-scan: allow"

#: High-confidence token formats: FATAL wherever they appear.
TOKEN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("pem private key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY-----")),
    ("aws access key id", re.compile(r"\b((?:AKIA|ASIA)[0-9A-Z]{16})\b")),
    ("github token", re.compile(r"\b((?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{60,})\b")),
    ("slack token", re.compile(r"\b(xox[abposr]-[A-Za-z0-9-]{10,})\b")),
    ("slack webhook", re.compile(r"(https://hooks\.slack\.com/services/T[A-Z0-9]{6,}/B[A-Z0-9]{6,}/[A-Za-z0-9]{20,})")),
    ("stripe live key", re.compile(r"\b((?:sk|rk)_live_[A-Za-z0-9]{16,})\b")),
    ("google api key", re.compile(r"\b(AIza[0-9A-Za-z_\-]{35})\b")),
    ("jwt", re.compile(r"\b(eyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,})\b")),
    ("fernet-style key", re.compile(r"[\"']([A-Za-z0-9_\-]{43}=)[\"']")),
]

#: Any identifier ENDING in a credential word (db_password, API_KEY,
#: client_secret, auth_token...) assigned a quoted literal of 8+ characters.
ASSIGNMENT = re.compile(
    r"(?i)[A-Za-z0-9_\-]*(?:password|passwd|passhash|pass_hash|secret|"
    r"api_?key|apikey|access_?key|private_?key|client_?secret|"
    r"(?<!error_)token)"
    r"[\"']?\s*[:=]\s*[\"']([^\"'\s]{8,})[\"']"
)


@dataclass(frozen=True)
class Finding:
    severity: str
    path: str
    line: int
    label: str
    snippet: str


def _mask(snippet: str) -> str:
    """Never echo a candidate secret into CI logs."""
    return re.sub(r"[\"'][^\"']{4,}[\"']", '"***"', snippet).strip()[:160]


def _is_placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(word in lowered for word in PLACEHOLDER_WORDS)


def scan_line(line: str) -> list[tuple[str, bool]]:
    """(label, high_confidence) for each credential found on `line`."""
    if ALLOW_PRAGMA in line:
        return []
    out: list[tuple[str, bool]] = []
    for label, pattern in TOKEN_PATTERNS:
        for m in pattern.finditer(line):
            value = m.group(1) if m.groups() else m.group(0)
            if label == "pem private key" or not _is_placeholder(value):
                out.append((label, True))
                break
    if not out:
        for m in ASSIGNMENT.finditer(line):
            if not _is_placeholder(m.group(1)):
                out.append(("credential assignment", False))
                break
    return out


def _is_test_or_scratch(rel: str) -> bool:
    parts = rel.split("/")
    return "tests" in parts or "test" in parts or parts[-1].startswith("_")


def severity_for(rel: str, high_confidence: bool) -> str:
    if high_confidence:
        return "FATAL"
    if _is_test_or_scratch(rel):
        return "WARN"
    if rel.startswith(FATAL_PREFIXES) or rel.endswith(".example"):
        return "FATAL"
    parts = rel.split("/")
    # Committed backend root-level scripts (diagnose_usage.py, ...).
    if len(parts) == 2 and parts[0] == "backend" and parts[1].endswith(".py"):
        return "FATAL"
    return "WARN"


def _wanted(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    if name in SKIP_NAMES or rel == SELF_REL:
        return False
    if any(part in PRUNE_DIR_NAMES for part in rel.split("/")[:-1]):
        return False
    return name in SCAN_NAMES or name.startswith(".env") or name.endswith(SCAN_SUFFIXES)


def candidate_files(root: Path) -> list[str]:
    """Repo-relative POSIX paths git would commit (falls back to a walk)."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=root, capture_output=True, check=True,
        ).stdout.decode("utf-8", "replace")
        files = [p for p in out.split("\0") if p]
    except (OSError, subprocess.CalledProcessError):
        files = []
        for dirpath, dirs, names in os.walk(root):
            dirs[:] = sorted(d for d in dirs if d not in PRUNE_DIR_NAMES)
            for name in sorted(names):
                files.append((Path(dirpath) / name).relative_to(root).as_posix())
    return sorted({f for f in files if _wanted(f)})


def scan(root: Path, files: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    for rel in files:
        path = root / rel
        if not path.exists():  # deleted in the working tree, still in the index
            continue
        try:
            raw = path.read_bytes()
        except OSError as exc:
            findings.append(Finding("FATAL", rel, 0, "unreadable file", type(exc).__name__))
            continue
        text = raw.decode("utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            for label, high in scan_line(line):
                findings.append(Finding(severity_for(rel, high), rel, lineno, label, _mask(line)))
    return findings


def main(root: Path = REPO) -> int:
    files = candidate_files(root)
    findings = scan(root, files)
    fatal = [f for f in findings if f.severity == "FATAL"]
    for f in findings:
        print(f"[{f.severity}] {f.path}:{f.line}: {f.label}: {f.snippet}")
    print(f"\nsecret scan: {len(files)} files, {len(fatal)} fatal, {len(findings) - len(fatal)} warning(s)")
    if fatal:
        print("SECRET SCAN FAILED: credential material or an unreadable file on a scanned path.")
        return 1
    print("SECRET SCAN PASSED: no credential material on shipping paths.")
    return 0


def _self_test() -> int:
    """Negative control: every detector fires on a real-looking value and stays
    quiet on placeholders. Values are assembled at runtime so this file itself
    never contains a secret-shaped literal. ci/test_secret_scan.py has more."""
    j = "eyJ" + "hbGciOiJIUzI1NiJ9" + ".eyJ" + "zdWIiOiIxIn0" + ".abcdefghijkl"
    positives = [
        ("AKIA" + "Q2W3E4R5T6Y7U8I9", "aws access key id"),
        ("ghp_" + "a" * 18 + "B" * 18, "github token"),
        ("xoxb-" + "1234567890-abcdefghij", "slack token"),
        ("sk_live_" + "4eC39HqLyjWDarjtT1zdp7dc", "stripe live key"),
        ("AIza" + "SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q", "google api key"),
        ("t = '" + j + "'", "jwt"),
        ("-----BEGIN " + "PRIVATE KEY-----", "pem private key"),
        ('db_password = "' + "Sup3rValue99xy" + '"', "credential assignment"),
        ('"apiKey": "' + "q8Fz2LmN0pRt" + '"', "credential assignment"),
        ('const x: Record<string, string> = { token: "' + "Zx9Qw8Er7Ty6" + '" }', "credential assignment"),
    ]
    negatives = [
        'QUOTA_ERROR_TOKEN = "device_quota_exceeded"',
        'POSTGRES_PASSWORD: "REPLACE_ME_PG_PASSWORD"',
        'api_key = "${API_KEY}"',
        'password = "<set in the secret manager>"',
        'AWS_ACCESS_KEY_ID = "AKIA' + 'IOSFODNN7EXAMPLE"',
        'password = "' + "Sup3rValue99xy" + '"  # ' + ALLOW_PRAGMA,
    ]
    failures = []
    for line, label in positives:
        if label not in [lbl for lbl, _ in scan_line(line)]:
            failures.append(f"{label!r} not detected")
    for line in negatives:
        if scan_line(line):
            failures.append(f"false positive on placeholder: {_mask(line)}")
    if failures:
        print("SELF-TEST FAILED:")
        for failure in failures:
            print("  -", failure)
        return 1
    print(f"SELF-TEST PASSED: {len(positives)} positives detected; {len(negatives)} placeholders excluded.")
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(_self_test())
    sys.exit(main())
