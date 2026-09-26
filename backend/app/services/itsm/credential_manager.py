"""
ITSM Credential Manager (Phase 1).

Handles encryption and decryption of credentials stored in the
ITSMConnection.credentials_encrypted JSON column. This reuses the
existing SecretCrypto facade from app.core.security rather than
implementing its own crypto.
"""
from __future__ import annotations

import logging
from typing import Any

from app.core.security import SecretCrypto

logger = logging.getLogger(__name__)

_SENSITIVE_KEYS = frozenset({
    "oauth_client_secret",
    "oauth_refresh_token",
    "oauth_access_token",
    "password",
    "api_key",
    "personal_access_token",
    "client_key",
    "cert_passphrase",
    "webhook_secret",
})


class CredentialManager:
    def __init__(self, crypto: SecretCrypto | None = None) -> None:
        self._crypto = crypto or SecretCrypto()

    def encrypt(self, credentials: dict[str, Any]) -> dict[str, Any]:
        encrypted: dict[str, Any] = {}
        for key, value in credentials.items():
            if key in _SENSITIVE_KEYS and value is not None:
                encrypted[key] = self._crypto.encrypt(str(value))
            else:
                encrypted[key] = value
        return encrypted

    def decrypt(self, encrypted_credentials: dict[str, Any]) -> dict[str, Any]:
        decrypted: dict[str, Any] = {}
        for key, value in encrypted_credentials.items():
            if key in _SENSITIVE_KEYS and value is not None:
                decrypted[key] = self._crypto.decrypt(value)
            else:
                decrypted[key] = value
        return decrypted

    def encrypt_webhook_secret(self, secret: str | None) -> str | None:
        if secret is None:
            return None
        return self._crypto.encrypt(secret)

    def decrypt_webhook_secret(self, encrypted_secret: str | None) -> str | None:
        if encrypted_secret is None:
            return None
        return self._crypto.decrypt(encrypted_secret)
