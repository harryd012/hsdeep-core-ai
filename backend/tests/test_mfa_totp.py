"""TOTP against the RFC 6238 Appendix B test vectors (SHA-1, 8 digits) and the 6-digit verify path."""
from __future__ import annotations

import base64

from app.services.mfa import totp

RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()


def test_rfc6238_vectors():
    for t, expected in [(59, "94287082"), (1111111109, "07081804"), (1111111111, "14050471"),
                        (1234567890, "89005924"), (2000000000, "69279037")]:
        assert totp.hotp(RFC_SECRET, t // 30, digits=8) == expected


def test_verify_window_and_format():
    secret = totp.new_secret()
    at = 1_700_000_000
    code = totp.hotp(secret, totp.current_step(at))
    assert totp.verify(secret, code, at=at) == totp.current_step(at)
    assert totp.verify(secret, code, at=at + 30) == totp.current_step(at)  # one step of drift
    assert totp.verify(secret, code, at=at + 90) is None
    assert totp.verify(secret, "12345", at=at) is None and totp.verify(secret, "abcdef", at=at) is None
    uri = totp.provisioning_uri(secret, account="jane@corp.test")
    assert uri.startswith("otpauth://totp/HSDEEP%20CORE%20AI%3Ajane%40corp.test?") and f"secret={secret}" in uri
