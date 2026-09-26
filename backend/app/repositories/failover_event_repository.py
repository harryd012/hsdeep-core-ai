"""
Repository for FailoverEvent persistence (append-only episode capture).

Every query is tenant-scoped. The FSM lives in ``apply_transition`` so that no
caller can write an illegal status edge or forget to append to the audit
``transition_log``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.failover_event import (
    ALLOWED_TRANSITIONS,
    OPEN_STATUSES,
    STATUSES,
    FailoverEvent,
)


class IllegalFailoverTransition(ValueError):
    """Raised when a caller tries to move a FailoverEvent along an edge the
    finite-state machine does not allow (e.g. ``recovered`` back to
    ``failed_over``). Failing loudly here is what keeps the episode history
    trustworthy - a silent illegal write would corrupt the timeline."""


class FailoverEventRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    async def create(
        self,
        tenant_id: uuid.UUID,
        *,
        scope_key: str,
        dedup_key: str,
        status: str,
        detected_at: datetime,
        sequence_no: int = 1,
        site_id: uuid.UUID | None = None,
        edge_device_id: uuid.UUID | None = None,
        trigger: dict | None = None,
        decision: dict | None = None,
        link_from_id: uuid.UUID | None = None,
        link_from_name: str | None = None,
        link_to_id: uuid.UUID | None = None,
        link_to_name: str | None = None,
        route_transitions: list | None = None,
        bgp_transitions: list | None = None,
        blast_radius: dict | None = None,
        evidence: list | None = None,
        verification: dict | None = None,
        incident_id: uuid.UUID | None = None,
        correlation_id: uuid.UUID | None = None,
        failed_over_at: datetime | None = None,
        recovering_at: datetime | None = None,
        recovered_at: datetime | None = None,
        attributes: dict | None = None,
        transition_reason: str | None = None,
    ) -> FailoverEvent:
        if status not in STATUSES:
            raise IllegalFailoverTransition(f"unknown failover status {status!r}")
        now = detected_at
        row = FailoverEvent(
            tenant_id=tenant_id,
            site_id=site_id,
            edge_device_id=edge_device_id,
            sequence_no=int(sequence_no),
            scope_key=scope_key,
            dedup_key=dedup_key,
            status=status,
            status_changed_at=now,
            transition_log=[self._entry(status, now, transition_reason)],
            trigger=trigger or {},
            decision=decision or {},
            link_from_id=link_from_id,
            link_from_name=link_from_name,
            link_to_id=link_to_id,
            link_to_name=link_to_name,
            route_transitions=route_transitions or [],
            bgp_transitions=bgp_transitions or [],
            blast_radius=blast_radius or {},
            evidence=evidence or [],
            verification=verification,
            incident_id=incident_id,
            correlation_id=correlation_id,
            detected_at=detected_at,
            failed_over_at=failed_over_at,
            recovering_at=recovering_at,
            recovered_at=recovered_at,
            attributes=attributes or {},
        )
        self.db.add(row)
        await self.db.flush()
        await self.db.refresh(row)
        return row

    @staticmethod
    def _entry(status: str, at: datetime, reason: str | None) -> dict:
        return {"status": status, "at": at.isoformat(), "reason": reason}

    async def update(self, event: FailoverEvent, **fields) -> FailoverEvent:
        """Apply the given fields verbatim (explicit None is honoured)."""
        for key, value in fields.items():
            if hasattr(event, key):
                setattr(event, key, value)
        await self.db.flush()
        await self.db.refresh(event)
        return event

    async def apply_transition(
        self,
        event: FailoverEvent,
        *,
        status: str,
        at: datetime | None = None,
        reason: str | None = None,
        append_routes: list | None = None,
        append_bgp: list | None = None,
        **fields,
    ) -> FailoverEvent:
        """Move an episode one FSM step forward and record the transition.

        Appends any newly observed route/BGP transitions to the existing arrays
        (never replaces them - those arrays are the captured history), applies
        the caller's field updates, and appends an audit entry to
        ``transition_log``. Raises ``IllegalFailoverTransition`` for an edge the
        FSM does not allow or a no-op self-transition.
        """
        if status not in STATUSES:
            raise IllegalFailoverTransition(f"unknown failover status {status!r}")
        if status == event.status:
            raise IllegalFailoverTransition(
                f"{event.status!r} -> {status!r} is a no-op self-transition"
            )
        allowed = ALLOWED_TRANSITIONS.get(event.status, ())
        if status not in allowed:
            raise IllegalFailoverTransition(
                f"{event.status!r} -> {status!r} is not a legal failover transition"
            )
        when = at or datetime.now(timezone.utc)
        event.status = status
        event.status_changed_at = when
        event.transition_log = list(event.transition_log or []) + [
            self._entry(status, when, reason)
        ]
        if append_routes:
            event.route_transitions = (
                list(event.route_transitions or []) + list(append_routes)
            )
        if append_bgp:
            event.bgp_transitions = list(event.bgp_transitions or []) + list(append_bgp)
        for key, value in fields.items():
            if hasattr(event, key):
                setattr(event, key, value)
        await self.db.flush()
        await self.db.refresh(event)
        return event

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    async def get(
        self, event_id: uuid.UUID, tenant_id: uuid.UUID,
    ) -> FailoverEvent | None:
        stmt = select(FailoverEvent).where(
            FailoverEvent.id == event_id,
            FailoverEvent.tenant_id == tenant_id,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def find_open(
        self, tenant_id: uuid.UUID, dedup_key: str,
    ) -> FailoverEvent | None:
        """The one open episode for a (tenant, dedup_key), if any.

        Mirrors the ``uq_failover_events_open_dedup`` partial unique index, so
        the detector can resume an in-flight episode after a restart instead of
        violating the constraint (or, worse, opening a duplicate).
        """
        stmt = select(FailoverEvent).where(
            FailoverEvent.tenant_id == tenant_id,
            FailoverEvent.dedup_key == dedup_key,
            FailoverEvent.status.in_(OPEN_STATUSES),
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def find_open_for_scope(
        self, tenant_id: uuid.UUID, scope_key: str,
    ) -> FailoverEvent | None:
        """Any open episode for a scope, regardless of which path failed.

        Used on resume: if the detector state was lost (fresh deployment, manual
        truncation) but an episode is still open, it must be continued rather
        than duplicated.
        """
        stmt = (
            select(FailoverEvent)
            .where(
                FailoverEvent.tenant_id == tenant_id,
                FailoverEvent.scope_key == scope_key,
                FailoverEvent.status.in_(OPEN_STATUSES),
            )
            .order_by(FailoverEvent.detected_at.desc())
            .limit(1)
        )
        return (await self.db.execute(stmt)).scalars().first()

    async def next_sequence(self, tenant_id: uuid.UUID, scope_key: str) -> int:
        """Monotonic per-(tenant, scope) episode number."""
        stmt = select(func.max(FailoverEvent.sequence_no)).where(
            FailoverEvent.tenant_id == tenant_id,
            FailoverEvent.scope_key == scope_key,
        )
        current = (await self.db.execute(stmt)).scalar_one_or_none()
        return int(current or 0) + 1

    def _filtered(
        self,
        tenant_id: uuid.UUID,
        *,
        status: str | None,
        statuses: list[str] | None,
        site_id: uuid.UUID | None,
        edge_device_id: uuid.UUID | None,
        open_only: bool,
        since: datetime | None,
        until: datetime | None,
    ):
        stmt = select(FailoverEvent).where(FailoverEvent.tenant_id == tenant_id)
        cstmt = select(func.count()).select_from(FailoverEvent).where(
            FailoverEvent.tenant_id == tenant_id
        )
        conditions = []
        if status is not None:
            conditions.append(FailoverEvent.status == status)
        if statuses:
            conditions.append(FailoverEvent.status.in_(list(statuses)))
        if open_only:
            conditions.append(FailoverEvent.status.in_(OPEN_STATUSES))
        if site_id is not None:
            conditions.append(FailoverEvent.site_id == site_id)
        if edge_device_id is not None:
            conditions.append(FailoverEvent.edge_device_id == edge_device_id)
        if since is not None:
            conditions.append(FailoverEvent.detected_at >= since)
        if until is not None:
            conditions.append(FailoverEvent.detected_at <= until)
        for condition in conditions:
            stmt = stmt.where(condition)
            cstmt = cstmt.where(condition)
        return stmt, cstmt

    async def list(
        self,
        tenant_id: uuid.UUID,
        *,
        status: str | None = None,
        statuses: list[str] | None = None,
        site_id: uuid.UUID | None = None,
        edge_device_id: uuid.UUID | None = None,
        open_only: bool = False,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[FailoverEvent], int]:
        stmt, cstmt = self._filtered(
            tenant_id,
            status=status, statuses=statuses, site_id=site_id,
            edge_device_id=edge_device_id, open_only=open_only,
            since=since, until=until,
        )
        total = int((await self.db.execute(cstmt)).scalar_one())
        stmt = (
            stmt.order_by(
                FailoverEvent.detected_at.desc(), FailoverEvent.sequence_no.desc()
            )
            .limit(limit)
            .offset(offset)
        )
        rows = list((await self.db.execute(stmt)).scalars().all())
        return rows, total

    async def counts_by_status(self, tenant_id: uuid.UUID) -> dict[str, int]:
        stmt = (
            select(FailoverEvent.status, func.count())
            .where(FailoverEvent.tenant_id == tenant_id)
            .group_by(FailoverEvent.status)
        )
        return {
            status: int(count) for status, count in (await self.db.execute(stmt)).all()
        }

    async def summary(self, tenant_id: uuid.UUID) -> dict:
        counts = await self.counts_by_status(tenant_id)
        open_count = sum(counts.get(s, 0) for s in OPEN_STATUSES)
        return {
            "total": sum(counts.values()),
            "open": open_count,
            "by_status": counts,
        }
