"""
Encryption key rotation (enterprise security programme S4).

Every column that holds a secret encrypted with SecretCrypto is listed in
ENCRYPTED_FIELDS. `status()` reports, per column, how many values the current
key decrypts, how many still need an older key, and how many no configured key
can read (lost key). `rotate()` re-encrypts every value that an older key
decrypts with the current key, in batches; values no key can read are left
untouched and reported. Both can be limited to one tenant.

Rotating a key:
  1. Set SECRET_ENCRYPTION_KEY to the new key and add the old one to
     SECRET_ENCRYPTION_KEYS_PREVIOUS; restart. Everything keeps working.
  2. Run `python scripts/rotate_encryption_keys.py --rotate`.
  3. When `--status` shows nothing on a previous key, remove it.
Nothing here logs or returns a plaintext or ciphertext value.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import SecretCrypto
from app.models.itsm import ITSMConnection
from app.models.monitoring_source import MonitoringSource
from app.models.servicenow import ServiceNowConnection
from app.models.user_mfa import UserMfa

BATCH = 500


@dataclass(frozen=True)
class EncryptedField:
    model: type
    column: str
    #: "text": the column is one ciphertext; "json": a dict of ciphertexts.
    kind: str = "text"

    @property
    def name(self) -> str:
        return f"{self.model.__tablename__}.{self.column}"


ENCRYPTED_FIELDS: tuple[EncryptedField, ...] = (
    EncryptedField(MonitoringSource, "api_key_encrypted"),
    EncryptedField(MonitoringSource, "password_encrypted"),
    EncryptedField(ServiceNowConnection, "password_encrypted"),
    EncryptedField(ServiceNowConnection, "oauth_client_secret_encrypted"),
    EncryptedField(ServiceNowConnection, "oauth_access_token_encrypted"),
    EncryptedField(ServiceNowConnection, "oauth_refresh_token_encrypted"),
    EncryptedField(ServiceNowConnection, "webhook_secret_encrypted"),
    EncryptedField(ITSMConnection, "credentials_encrypted", "json"),
    EncryptedField(ITSMConnection, "webhook_secret_encrypted"),
    EncryptedField(UserMfa, "secret_encrypted"),
)


@dataclass
class FieldReport:
    name: str
    current: int = 0
    previous: int = 0
    unreadable: int = 0
    rotated: int = 0

    def as_dict(self) -> dict:
        return {"field": self.name, "current": self.current, "previous": self.previous,
                "unreadable": self.unreadable, "rotated": self.rotated}


@dataclass
class RotationReport:
    key_fingerprints: list[str]
    fields: list[FieldReport] = field(default_factory=list)

    @property
    def needs_rotation(self) -> int:
        return sum(f.previous for f in self.fields)

    @property
    def unreadable(self) -> int:
        return sum(f.unreadable for f in self.fields)

    def as_dict(self) -> dict:
        return {
            "current_key": self.key_fingerprints[0],
            "previous_keys": self.key_fingerprints[1:],
            "needs_rotation": self.needs_rotation,
            "unreadable": self.unreadable,
            "fields": [f.as_dict() for f in self.fields],
        }


def _values(f: EncryptedField, raw) -> list[str]:
    if raw is None:
        return []
    if f.kind == "json":
        return [v for v in (raw or {}).values() if isinstance(v, str) and v]
    return [raw] if raw else []


async def _process(db: AsyncSession, crypto: SecretCrypto, tenant_id: uuid.UUID | None, rotate: bool) -> RotationReport:
    report = RotationReport(key_fingerprints=crypto.fingerprints)
    for f in ENCRYPTED_FIELDS:
        fr = FieldReport(f.name)
        model = f.model
        pk = list(model.__table__.primary_key.columns)[0]
        col = getattr(model, f.column)
        last = None
        while True:
            stmt = select(model).where(col.is_not(None)).order_by(pk).limit(BATCH)
            if tenant_id is not None:
                stmt = stmt.where(model.tenant_id == tenant_id)
            if last is not None:
                stmt = stmt.where(pk > last)
            if rotate:
                stmt = stmt.with_for_update(skip_locked=True)
            rows = (await db.execute(stmt)).scalars().all()
            if not rows:
                break
            for row in rows:
                raw = getattr(row, f.column)
                changed = False
                new_json = dict(raw) if f.kind == "json" and raw else None
                for value in _values(f, raw):
                    idx = crypto.key_index(value)
                    if idx is None:
                        fr.unreadable += 1
                    elif idx == 0:
                        fr.current += 1
                    else:
                        fr.previous += 1
                        if rotate:
                            fresh = crypto.rotate(value)
                            if f.kind == "json":
                                for k, v in new_json.items():
                                    if v == value:
                                        new_json[k] = fresh
                            else:
                                setattr(row, f.column, fresh)
                            changed = True
                            fr.rotated += 1
                if changed and f.kind == "json":
                    setattr(row, f.column, new_json)
            last = getattr(rows[-1], pk.key)
            if rotate:
                await db.commit()
        report.fields.append(fr)
    return report


async def encryption_status(db: AsyncSession, tenant_id: uuid.UUID | None = None, crypto: SecretCrypto | None = None) -> RotationReport:
    return await _process(db, crypto or SecretCrypto(), tenant_id, rotate=False)


async def rotate_encryption(db: AsyncSession, tenant_id: uuid.UUID | None = None, crypto: SecretCrypto | None = None) -> RotationReport:
    """Re-encrypt every value an older key reads with the current key. Commits per batch."""
    return await _process(db, crypto or SecretCrypto(), tenant_id, rotate=True)
