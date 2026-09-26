"""
Repository for FailoverDetectorState persistence (restart-safe baseline).

One row per (tenant, scope). The detector reads it before evaluating a scope and
writes it back afterwards; because the baseline lives in the database, a
scheduler restart or worker crash mid-episode resumes the same episode rather
than re-opening it.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.failover_detector_state import FailoverDetectorState


class FailoverDetectorStateRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(
        self, tenant_id: uuid.UUID, scope_key: str,
    ) -> FailoverDetectorState | None:
        stmt = select(FailoverDetectorState).where(
            FailoverDetectorState.tenant_id == tenant_id,
            FailoverDetectorState.scope_key == scope_key,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_by_id(
        self, state_id: uuid.UUID, tenant_id: uuid.UUID,
    ) -> FailoverDetectorState | None:
        stmt = select(FailoverDetectorState).where(
            FailoverDetectorState.id == state_id,
            FailoverDetectorState.tenant_id == tenant_id,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def upsert(
        self,
        tenant_id: uuid.UUID,
        scope_key: str,
        *,
        site_id: uuid.UUID | None = None,
        edge_device_id: uuid.UUID | None = None,
        active_episode_id: uuid.UUID | None = None,
        consecutive_breaches: int = 0,
        consecutive_clear: int = 0,
        last_breach_at: datetime | None = None,
        previous_decision: dict | None = None,
        link_snapshot: dict | None = None,
        route_snapshot: dict | None = None,
        bgp_snapshot: dict | None = None,
        last_evaluated_at: datetime | None = None,
        attributes: dict | None = None,
    ) -> tuple[FailoverDetectorState, bool]:
        """Create or refresh the baseline for a scope.

        Returns ``(row, created)``. ``previous_decision`` and
        ``active_episode_id`` are written verbatim, including explicit None, so
        a resolved episode genuinely clears its resume handle.
        """
        now = last_evaluated_at or datetime.now(timezone.utc)
        existing = await self.get(tenant_id, scope_key)
        if existing is None:
            row = FailoverDetectorState(
                tenant_id=tenant_id,
                scope_key=scope_key,
                site_id=site_id,
                edge_device_id=edge_device_id,
                active_episode_id=active_episode_id,
                consecutive_breaches=max(0, int(consecutive_breaches)),
                consecutive_clear=max(0, int(consecutive_clear)),
                last_breach_at=last_breach_at,
                previous_decision=previous_decision,
                link_snapshot=link_snapshot or {},
                route_snapshot=route_snapshot or {},
                bgp_snapshot=bgp_snapshot or {},
                last_evaluated_at=now,
                attributes=attributes or {},
            )
            self.db.add(row)
            await self.db.flush()
            await self.db.refresh(row)
            return row, True

        existing.site_id = site_id if site_id is not None else existing.site_id
        existing.edge_device_id = (
            edge_device_id if edge_device_id is not None else existing.edge_device_id
        )
        existing.active_episode_id = active_episode_id
        existing.consecutive_breaches = max(0, int(consecutive_breaches))
        existing.consecutive_clear = max(0, int(consecutive_clear))
        existing.last_breach_at = last_breach_at
        existing.previous_decision = previous_decision
        if link_snapshot is not None:
            existing.link_snapshot = link_snapshot
        if route_snapshot is not None:
            existing.route_snapshot = route_snapshot
        if bgp_snapshot is not None:
            existing.bgp_snapshot = bgp_snapshot
        if attributes:
            merged = dict(existing.attributes or {})
            merged.update(attributes)
            existing.attributes = merged
        existing.last_evaluated_at = now
        await self.db.flush()
        await self.db.refresh(existing)
        return existing, False

    async def delete(self, state: FailoverDetectorState) -> None:
        """Drop a baseline (used when a scope no longer has candidate links)."""
        await self.db.delete(state)
        await self.db.flush()
