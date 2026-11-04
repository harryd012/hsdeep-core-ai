"""
Time-based one-time passwords (RFC 6238 over RFC 4226 HOTP), for
authenticator-app MFA (enterprise security programme S2).

Standard parameters every authenticator app uses: HMAC-SHA1, 30-second
steps, 6 digits. `verify` accepts the previous and next step for clock drift
and returns the matched step, so the caller can refuse a code that was
already used (replay).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

STEP_SECONDS = 30
DIGITS = 6


def new_secret() -> str:
    """A random 160-bit secret, base32 without padding (what apps expect)."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret_b32: str) -> bytes:
    s = secret_b32.strip().replace(" ", "").upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def hotp(secret_b32: str, counter: int, digits: int = DIGITS, digest=hashlib.sha1) -> str:
    mac = hmac.new(_key(secret_b32), struct.pack(">Q", counter), digest).digest()
    offset = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def current_step(at: float | None = None) -> int:
    return int((time.time() if at is None else at) // STEP_SECONDS)


def verify(secret_b32: str, code: str, *, at: float | None = None, window: int = 1) -> int | None:
    """The time step the code belongs to (within ±window steps), or None."""
    code = (code or "").strip().replace(" ", "")
    if len(code) != DIGITS or not code.isdigit():
        return None
    now = current_step(at)
    for step in range(now - window, now + window + 1):
        if hmac.compare_digest(hotp(secret_b32, step), code):
            return step
    return None


def provisioning_uri(secret_b32: str, *, account: str, issuer: str = "HSDEEP CORE AI") -> str:
    """otpauth:// URI that authenticator apps read from a QR code."""
    label = quote(f"{issuer}:{account}")
    query = urlencode({"secret": secret_b32, "issuer": issuer, "algorithm": "SHA1", "digits": DIGITS, "period": STEP_SECONDS})
    return f"otpauth://totp/{label}?{query}"
