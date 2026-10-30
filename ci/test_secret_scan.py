"""Tests for ci/secret_scan.py: positive and negative fixtures, severity and
coverage rules. Secret-shaped values are assembled at runtime so this file
never contains a literal credential."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import secret_scan as ss  # noqa: E402

AWS = "AKIA" + "Q2W3E4R5T6Y7U8I9"
PW = "Sup3rValue99xy"


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return tmp_path


def _scan(root: Path):
    return ss.scan(root, ss.candidate_files(root))


def test_self_test_passes():
    assert ss._self_test() == 0


def test_token_in_tests_is_still_fatal(tmp_path):
    root = _repo(tmp_path, {"backend/tests/test_x.py": f'KEY = "{AWS}"\n'})
    [f] = _scan(root)
    assert (f.severity, f.label, f.path) == ("FATAL", "aws access key id", "backend/tests/test_x.py")


def test_frontend_tests_are_scanned(tmp_path):
    root = _repo(tmp_path, {"frontend/tests/a.cjs": f'const token = "{PW}";\n'})
    assert [(f.severity, f.path) for f in _scan(root)] == [("WARN", "frontend/tests/a.cjs")]


def test_assignment_severity_follows_the_path(tmp_path):
    root = _repo(tmp_path, {
        "backend/app/x.py": f'db_password = "{PW}"\n',
        "backend/tests/test_x.py": f'db_password = "{PW}"\n',
        "docs/notes.md": f'password = "{PW}"\n',
        "backend/.env.example": f'SMTP_PASSWORD="{PW}"\n',
        "frontend/Dockerfile": f'ENV API_KEY="{PW}"\n',
    })
    got = {f.path: f.severity for f in _scan(root)}
    assert got == {
        "backend/app/x.py": "FATAL",
        "backend/tests/test_x.py": "WARN",
        "docs/notes.md": "WARN",
        "backend/.env.example": "FATAL",
        "frontend/Dockerfile": "WARN",
    }


def test_placeholder_words_elsewhere_on_the_line_do_not_hide_a_secret():
    for line in (
        f'api_key = "{PW}"  # see the example config',
        f'const t: Record<string, string> = {{ token: "{PW}" }}; // test',
        f'password: "{PW}", public: true',
    ):
        assert [label for label, _ in ss.scan_line(line)] == ["credential assignment"], line


@pytest.mark.parametrize("line", [
    'password = "<your password>"',
    'API_KEY = "${API_KEY}"',
    'SMTP_PASSWORD = "changeme-in-prod"',
    'token = "REDACTED_VALUE"',
    'ERROR_TOKEN = "device_quota_exceeded"',
    f'password = "{PW}"  # {ss.ALLOW_PRAGMA}',
])
def test_placeholders_are_not_flagged(line):
    assert ss.scan_line(line) == []


def test_ignored_build_and_dependency_dirs_are_pruned(tmp_path):
    root = _repo(tmp_path, {
        "frontend/node_modules/pkg/index.js": f'k = "{AWS}"\n',
        "frontend/.next/server.js": f'k = "{AWS}"\n',
        "frontend/pnpm-lock.yaml": f"integrity: {AWS}\n",
    })
    assert _scan(root) == []


def test_unreadable_file_is_fatal(tmp_path, monkeypatch):
    root = _repo(tmp_path, {"backend/app/x.py": "ok = 1\n"})
    real = Path.read_bytes

    def boom(self):
        if self.name == "x.py":
            raise PermissionError("denied")
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", boom)
    [f] = _scan(root)
    assert (f.severity, f.label) == ("FATAL", "unreadable file")


def test_findings_never_echo_the_value(tmp_path):
    root = _repo(tmp_path, {"backend/app/x.py": f'db_password = "{PW}"\n'})
    [f] = _scan(root)
    assert PW not in f.snippet
