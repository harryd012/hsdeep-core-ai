"""
Secret and personal-data redaction (enterprise security programme S5).

`redact(text)` replaces credentials that commonly leak into log lines and AI
prompts: key=value secrets (password, token, api_key, secret, community …),
Authorization headers and bearer tokens, JWTs, private-key blocks, AWS access
keys, connection-string passwords and card numbers. Optional flags also mask
email addresses and IP addresses (personal / network data in AI prompts).

It is pattern based, so it lowers the risk of a leak rather than proving there
is none; code still must not log secrets in the first place.
"""
from __future__ import annotations

import re

MASK = "[REDACTED]"

_KEY_NAMES = (
    r"pass(?:word|wd|phrase|hash)?|secret|token|api[_-]?key|apikey|access[_-]?key|private[_-]?key|"
    r"client[_-]?secret|auth(?:orization)?|community|credential|session[_-]?id|cookie|pwd"
)

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # -----BEGIN ... PRIVATE KEY----- blocks.
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), MASK),
    # Authorization: Bearer xxx / Basic xxx
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), rf"\1 {MASK}"),
    # JWTs (three base64url segments, header starting with eyJ).
    (re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b"), MASK),
    # AWS access key ids.
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), MASK),
    # scheme://user:password@host
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^/\s:@]+):[^@\s/]+@"), rf"\1:{MASK}@"),
    # key=value, key: value, "key": "value"
    (re.compile(
        rf"""(?ix)
        (["']?\b(?:{_KEY_NAMES})\b["']?\s*[:=]\s*)   # the key and separator
        (?:"[^"]*"|'[^']*'|[^\s,;&}}\]]+)           # the value
        """
    ), rf"\1{MASK}"),
)

# Payment card numbers: 13-19 digits (optional separators) that pass the Luhn
# check, so timestamps and counters in log lines are left alone.
_CARD = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")


def _luhn_ok(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _card(match: re.Match[str]) -> str:
    digits = re.sub(r"[ -]", "", match.group(0))
    return "[CARD]" if _luhn_ok(digits) else match.group(0)

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_IPV4 = re.compile(r"\b(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}\b")
_IPV6 = re.compile(r"\b(?:[0-9A-Fa-f]{1,4}:){2,7}[0-9A-Fa-f]{1,4}\b")


def redact(text: str, *, emails: bool = False, ips: bool = False) -> str:
    if not text:
        return text
    out = text
    for pattern, replacement in _PATTERNS:
        out = pattern.sub(replacement, out)
    out = _CARD.sub(_card, out)
    if emails:
        out = _EMAIL.sub("[EMAIL]", out)
    if ips:
        out = _IPV4.sub("[IP]", out)
        out = _IPV6.sub("[IP]", out)
    return out


class RedactingFilter:
    """logging filter: scrubs secrets from every record's final message."""

    def filter(self, record) -> bool:  # noqa: A003 - logging API name
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a broken format string is logged as is
            return True
        cleaned = redact(message)
        if cleaned != message:
            record.msg = cleaned
            record.args = ()
        return True
