"""
Credential encryption utilities.

Provides `SecretCrypto`, a thin wrapper around Fernet (symmetric encryption
from the `cryptography` package) used to encrypt/decrypt monitoring source
credentials (`api_key`, `password`) before they are persisted into the
`api_key_encrypted`/`password_encrypted` columns.

Security note: this module MUST NEVER log a plaintext or decrypted secret
value. Only non-sensitive metadata (e.g. success/failure, lengths) may be
logged, and only at DEBUG level if ever needed.
"""
import base64
import hashlib
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings

if TYPE_CHECKING:
    from app.models.monitoring_source import MonitoringSource

settings = get_settings()

logger = logging.getLogger(__name__)


def _derive_fernet_key(secret: str) -> bytes:
    """
    Derive a 32-byte urlsafe-base64-encoded key suitable for `Fernet` from an
    arbitrary-length configured secret.

    Fernet requires exactly 32 raw bytes, base64-urlsafe-encoded. Since
    `Settings.secret_encryption_key` is an arbitrary string (not guaranteed
    to already be a valid Fernet key), we deterministically derive a fixed
    32-byte digest via SHA-256 and then urlsafe-base64-encode it. This is
    deterministic (the same configured secret always yields the same Fernet
    key) and never logs or exposes the input secret.
    """
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


class SecretCrypto:
    """
    Fernet-based encrypt/decrypt helper for monitoring source credentials.

    The encryption key is derived from `Settings.secret_encryption_key`
    (see `_derive_fernet_key`). Instances are cheap to construct; a module
    level singleton is not required but callers may create one per request
    or reuse a shared instance.
    """

    def __init__(self, secret_encryption_key: str | None = None) -> None:
        key_source = secret_encryption_key or settings.secret_encryption_key
        self._fernet = Fernet(_derive_fernet_key(key_source))

    def encrypt(self, plaintext: str) -> str:
        """
        Encrypt `plaintext` and return the ciphertext as a `str`.

        The plaintext value is never logged, including on error paths.
        """
        token = self._fernet.encrypt(plaintext.encode("utf-8"))
        return token.decode("utf-8")

    def decrypt(self, ciphertext: str) -> str:
        """
        Decrypt `ciphertext` (as produced by `encrypt`) and return the
        plaintext `str`.

        Raises `InvalidToken` if `ciphertext` is malformed or was not
        produced with the configured key. The decrypted value is never
        logged.
        """
        try:
            token = self._fernet.decrypt(ciphertext.encode("utf-8"))
        except InvalidToken:
            logger.warning("Failed to decrypt a stored credential: invalid token")
            raise
        return token.decode("utf-8")


@dataclass
class MonitoringSourceConnectionInfo:
    """
    In-process DTO exposing a `MonitoringSource`'s typed connection fields
    with credentials already decrypted, for use by `BaseCollector`
    subclasses.

    This is never serialized (no Pydantic model, no `__repr__` override
    exposing secrets) and must never be logged - it exists purely to hand
    plaintext credentials to a collector for the duration of a single
    collection run.
    """

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    source_type: str
    hostname: str | None
    ip_address: str | None
    port: int | None
    protocol: str | None
    snmp_version: str | None
    username: str | None
    api_key: str | None
    password: str | None
    config: dict
    verify_ssl: bool


def decrypt_connection_info(source: "MonitoringSource") -> MonitoringSourceConnectionInfo:
    """
    Build a `MonitoringSourceConnectionInfo` from a `MonitoringSource` ORM
    row, decrypting `api_key_encrypted`/`password_encrypted` via
    `SecretCrypto` in-process.

    Decrypted values are never logged or included in any exception message
    raised from this function.
    """
    crypto = SecretCrypto()

    api_key = crypto.decrypt(source.api_key_encrypted) if source.api_key_encrypted else None
    password = crypto.decrypt(source.password_encrypted) if source.password_encrypted else None

    return MonitoringSourceConnectionInfo(
        id=source.id,
        tenant_id=source.tenant_id,
        name=source.name,
        source_type=source.source_type,
        hostname=source.hostname,
        ip_address=source.ip_address,
        port=source.port,
        protocol=source.protocol,
        snmp_version=source.snmp_version,
        username=source.username,
        api_key=api_key,
        password=password,
        config=source.config,
        verify_ssl=source.verify_ssl if source.verify_ssl is not None else False,
    )
