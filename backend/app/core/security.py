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

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

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


def key_fingerprint(secret: str) -> str:
    """Short, non-reversible identifier of a configured key (for status
    reports): the first 8 hex characters of SHA-256 over the derived key."""
    return hashlib.sha256(_derive_fernet_key(secret)).hexdigest()[:8]


def previous_keys() -> list[str]:
    return [k.strip() for k in (settings.secret_encryption_keys_previous or "").split(",") if k.strip()]


class SecretCrypto:
    """
    Fernet-based encrypt/decrypt helper for stored credentials and secrets.

    Key versioning (enterprise security programme S4): encryption always uses
    the current key (`Settings.secret_encryption_key`); decryption also
    accepts the previous keys (`Settings.secret_encryption_keys_previous`),
    so a key can be rotated without downtime. `rotate()` re-encrypts a value
    with the current key (scripts/rotate_encryption_keys.py does it for every
    stored secret). Instances are cheap to construct.
    """

    def __init__(self, secret_encryption_key: str | None = None, previous: list[str] | None = None) -> None:
        key_source = secret_encryption_key or settings.secret_encryption_key
        older = previous_keys() if previous is None else previous
        self._keys = [key_source, *[k for k in older if k != key_source]]
        self._fernets = [Fernet(_derive_fernet_key(k)) for k in self._keys]
        self._fernet = MultiFernet(self._fernets)

    @property
    def fingerprints(self) -> list[str]:
        """Current key first, then the previous ones."""
        return [key_fingerprint(k) for k in self._keys]

    def key_index(self, ciphertext: str) -> int | None:
        """0 when the current key decrypts it, i for the i-th key, None if none does."""
        data = ciphertext.encode("utf-8")
        for i, f in enumerate(self._fernets):
            try:
                f.decrypt(data)
                return i
            except InvalidToken:
                continue
        return None

    def rotate(self, ciphertext: str) -> str:
        """The same plaintext, encrypted with the current key."""
        return self._fernet.rotate(ciphertext.encode("utf-8")).decode("utf-8")

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
