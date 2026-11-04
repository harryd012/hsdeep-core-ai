"""
Tamper-evident audit log (enterprise security programme S4).

Each tenant's audit events form a hash chain (models/audit_chain.py):

    hash_n = SHA-256(hash_{n-1} || canonical JSON of event n),  hash_0 = 64 zeros

* `link_event` appends an event as soon as AuditService writes it. A per-tenant
  transaction-level advisory lock keeps the chain strictly sequential.
* `seal_unchained` appends events written some other way (a few services insert
  AuditEvent rows directly) or whose linking failed; the scheduler runs it.
* `verify` walks a tenant's chain and reports any link whose event was changed
  or deleted, or whose link to the previous one was altered.
* `head` is the latest link: record it outside the platform (it is shown on the
  Audit log page and sent with every export) and a rebuilt chain shows too.

The hash is always computed from the event as stored in the database (read
back after insert), so writing and verifying see the same representation.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_chain import AuditChainLink
from app.models.knowledge_audit import AuditEvent

logger = logging.getLogger(__name__)

GENESIS = "0" * 64
_EVENT = AuditEvent.__table__
_FIELDS = (
    "id", "tenant_id", "created_at", "actor_id", "actor_email", "actor_type", "action", "entity_type",
    "entity_id", "description", "old_values", "new_values", "ip_address", "user_agent", "context",
)
VERIFY_BATCH = 2000
MAX_PROBLEMS = 20
#: Links checked by default (the most recent ones); full=True checks everything.
RECENT_LINKS = 2_000


def _plain(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def canonical(row) -> bytes:
    """Deterministic bytes for one stored event (a row mapping of audit_events)."""
    doc = {k: _plain(row[k]) for k in _FIELDS}
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")


def chain_hash(prev_hash: str, row) -> str:
    return hashlib.sha256(prev_hash.encode("ascii") + canonical(row)).hexdigest()


async def _lock(db: AsyncSession, tenant_id: uuid.UUID) -> None:
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"audit_chain:{tenant_id}"})


async def _last(db: AsyncSession, tenant_id: uuid.UUID) -> AuditChainLink | None:
    return (
        await db.execute(
            select(AuditChainLink).where(AuditChainLink.tenant_id == tenant_id)
            .order_by(AuditChainLink.seq.desc()).limit(1)
        )
    ).scalar_one_or_none()


async def _append(db: AsyncSession, tenant_id: uuid.UUID, rows: list) -> int:
    """Append stored event rows (already ordered) to the tenant's chain. Caller holds the lock."""
    last = await _last(db, tenant_id)
    seq, prev = (last.seq, last.hash) if last else (0, GENESIS)
    for row in rows:
        seq += 1
        digest = chain_hash(prev, row)
        db.add(AuditChainLink(event_id=row["id"], tenant_id=tenant_id, seq=seq, prev_hash=prev, hash=digest))
        prev = digest
    await db.flush()
    return len(rows)


async def link_event(db: AsyncSession, event_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
    """Append one just-written event to its tenant's chain (inside the caller's transaction)."""
    await _lock(db, tenant_id)
    row = (await db.execute(select(_EVENT).where(_EVENT.c.id == event_id))).mappings().one()
    already = (await db.execute(select(AuditChainLink.event_id).where(AuditChainLink.event_id == event_id))).first()
    if already is None:
        await _append(db, tenant_id, [row])


async def seal_unchained(db: AsyncSession, tenant_id: uuid.UUID | None = None, limit: int = 5000) -> int:
    """Link events that have no chain link yet, oldest first, per tenant. Commits per tenant."""
    unchained = (
        select(_EVENT)
        .outerjoin(AuditChainLink.__table__, AuditChainLink.__table__.c.event_id == _EVENT.c.id)
        .where(AuditChainLink.__table__.c.event_id.is_(None))
    )
    if tenant_id is not None:
        unchained = unchained.where(_EVENT.c.tenant_id == tenant_id)
    tenants = (await db.execute(select(func.distinct(unchained.subquery().c.tenant_id)))).scalars().all()
    total = 0
    for tid in tenants:
        await _lock(db, tid)
        rows = (
            await db.execute(
                unchained.where(_EVENT.c.tenant_id == tid)
                .order_by(_EVENT.c.created_at, _EVENT.c.id).limit(limit)
            )
        ).mappings().all()
        total += await _append(db, tid, list(rows))
        await db.commit()
    return total


@dataclass
class ChainReport:
    status: str  # "intact" | "broken" | "empty"
    #: "full" (from the first event) or "recent" (the last RECENT_LINKS links,
    #: anchored on the stored link before them).
    scope: str = "full"
    from_seq: int = 1
    links_checked: int = 0
    unchained_events: int = 0
    problems: list[dict] = field(default_factory=list)
    head: dict | None = None

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "scope": self.scope,
            "from_seq": self.from_seq,
            "links_checked": self.links_checked,
            "unchained_events": self.unchained_events,
            "problems": self.problems,
            "head": self.head,
        }


async def head(db: AsyncSession, tenant_id: uuid.UUID) -> dict | None:
    last = await _last(db, tenant_id)
    if last is None:
        return None
    return {"seq": last.seq, "hash": last.hash, "linked_at": last.linked_at.isoformat()}


async def verify(db: AsyncSession, tenant_id: uuid.UUID, *, full: bool = False) -> ChainReport:
    """Check the chain. By default the most recent RECENT_LINKS links, starting
    from the stored hash of the link before them, so the check stays fast at any
    size; full=True starts from the first event (scripts/verify_audit_chain.py)."""
    links = AuditChainLink.__table__
    report = ChainReport(status="intact")
    expected_prev, expected_seq = GENESIS, 1
    after = 0
    last = await _last(db, tenant_id)
    if not full and last is not None and last.seq > RECENT_LINKS:
        after = last.seq - RECENT_LINKS
        anchor = (
            await db.execute(select(links.c.hash).where(links.c.tenant_id == tenant_id, links.c.seq == after))
        ).scalar_one_or_none()
        if anchor is None:
            report.status = "broken"
            report.problems.append({"seq": after, "event_id": None, "problem": "missing_link"})
            anchor = GENESIS
        expected_prev, expected_seq = anchor, after + 1
        report.scope = "recent"
    report.from_seq = after + 1
    while True:
        rows = (
            await db.execute(
                select(links.c.seq, links.c.event_id, links.c.prev_hash, links.c.hash, *[_EVENT.c[k].label(f"e_{k}") for k in _FIELDS])
                .select_from(links.outerjoin(_EVENT, _EVENT.c.id == links.c.event_id))
                .where(links.c.tenant_id == tenant_id, links.c.seq > after)
                .order_by(links.c.seq).limit(VERIFY_BATCH)
            )
        ).mappings().all()
        if not rows:
            break
        for r in rows:
            report.links_checked += 1
            problem = None
            if r["seq"] != expected_seq:
                problem = "missing_link"
            elif r["prev_hash"] != expected_prev:
                problem = "broken_link"
            elif r["e_id"] is None:
                problem = "event_deleted"
            elif chain_hash(r["prev_hash"], {k: r[f"e_{k}"] for k in _FIELDS}) != r["hash"]:
                problem = "event_modified"
            if problem:
                report.status = "broken"
                if len(report.problems) < MAX_PROBLEMS:
                    report.problems.append({"seq": r["seq"], "event_id": str(r["event_id"]), "problem": problem})
            expected_prev, expected_seq = r["hash"], r["seq"] + 1
            after = r["seq"]
    report.unchained_events = int((
        await db.execute(
            select(func.count()).select_from(_EVENT.outerjoin(links, links.c.event_id == _EVENT.c.id))
            .where(_EVENT.c.tenant_id == tenant_id, links.c.event_id.is_(None))
        )
    ).scalar_one())
    if report.links_checked == 0 and report.status == "intact":
        report.status = "empty"
    report.head = await head(db, tenant_id)
    return report


async def seal_audit_chain_job() -> None:
    """Scheduler entry point: link any events written outside AuditService."""
    from app.core.db import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as db:
            sealed = await seal_unchained(db)
        if sealed:
            logger.info("Audit chain: linked %d event(s).", sealed)
    except Exception:  # noqa: BLE001 - a scheduler job must never crash the scheduler
        logger.exception("Audit chain sealing failed")
