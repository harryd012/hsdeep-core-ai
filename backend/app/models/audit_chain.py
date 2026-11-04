"""
AuditChainLink (enterprise security programme S4): a per-tenant hash chain
over the audit log, so a changed or deleted audit event can be detected.

Each link stores the SHA-256 of (previous link's hash + the event's canonical
content). Links live in their own table, keyed by event id with no foreign
key, so deleting an audit event leaves its link behind and the gap shows.
``seq`` is the event's position in its tenant's chain (1, 2, 3 …).

Someone with full write access to the database could rebuild the whole chain;
recording the chain head (shown on the Audit log page and sent with every
export) outside the platform detects that too.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AuditChainLink(Base):
    __tablename__ = "audit_chain"
    __table_args__ = (UniqueConstraint("tenant_id", "seq", name="uq_audit_chain_tenant_seq"),)

    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False)
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
