"""
Repository Pattern: all direct SQLAlchemy access for Metric lives here.
Metrics are an append-only time series - there is no unique key on the row,
so record() always inserts and never upserts, same convention as
MonitoringSourceRepository / AlertRepository.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.metric import Metric

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids a circular import
    from app.services.infrastructure_intelligence.telemetry import CanonicalTelemetry


class MetricRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def record(
        self,
        *,
        tenant_id: uuid.UUID,
        metric_type: str,
        value: float | None,
        recorded_at: datetime,
        monitoring_source_id: uuid.UUID | None = None,
        device_id: uuid.UUID | None = None,
        sensor_id: uuid.UUID | None = None,
        unit: str | None = None,
        # -- canonical telemetry (universal infrastructure intelligence) ------
        # All optional: existing callers keep working unchanged, and a caller
        # that knows the canonical shape passes it through here rather than
        # writing a second row.
        interface_id: uuid.UUID | None = None,
        metric_name: str | None = None,
        capability: str | None = None,
        provider: str | None = None,
        collection_status: str = "collected",
        quality: str | None = None,
        text_value: str | None = None,
        boolean_value: bool | None = None,
        source_timestamp: datetime | None = None,
        received_timestamp: datetime | None = None,
        dimensions: dict | None = None,
    ) -> Metric:
        metric = Metric(
            tenant_id=tenant_id,
            monitoring_source_id=monitoring_source_id,
            device_id=device_id,
            sensor_id=sensor_id,
            interface_id=interface_id,
            metric_type=metric_type,
            metric_name=metric_name or metric_type,
            value=value,
            unit=unit,
            recorded_at=recorded_at,
            provider=provider,
            capability=capability,
            collection_status=collection_status,
            quality=quality,
            text_value=text_value,
            boolean_value=boolean_value,
            source_timestamp=source_timestamp,
            received_timestamp=received_timestamp,
            dimensions=dimensions,
        )
        self.db.add(metric)
        await self.db.flush()
        await self.db.refresh(metric)
        return metric

    async def list_for_device(
        self,
        device_id: uuid.UUID,
        *,
        metric_type: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Metric]:
        stmt = select(Metric).where(Metric.device_id == device_id)
        if metric_type is not None:
            stmt = stmt.where(Metric.metric_type == metric_type)
        if since is not None:
            stmt = stmt.where(Metric.recorded_at >= since)
        stmt = stmt.order_by(Metric.recorded_at.desc()).limit(limit).offset(offset)

        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def list_for_sensor(
        self,
        sensor_id: uuid.UUID,
        *,
        since: datetime | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[Metric]:
        """Historical readings for one sensor - powers the Sensor Details
        page's historical graph and status timeline."""
        stmt = select(Metric).where(Metric.sensor_id == sensor_id)
        if since is not None:
            stmt = stmt.where(Metric.recorded_at >= since)
        stmt = stmt.order_by(Metric.recorded_at.desc()).limit(limit).offset(offset)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    # -- Canonical telemetry (universal infrastructure intelligence) ---------
    #
    # Additive: the legacy sensor/device read paths above are untouched. They
    # exist so the universal layer has ONE persistence and query surface
    # instead of growing a second telemetry store.

    async def record_canonical(self, record: "CanonicalTelemetry") -> Metric:
        """Persist one canonical telemetry record."""
        return await self.record(
            tenant_id=record.tenant_id,
            metric_type=record.metric_name,
            metric_name=record.metric_name,
            value=record.numeric_value,
            recorded_at=record.timestamp,
            device_id=record.device_id,
            interface_id=record.interface_id,
            unit=record.unit,
            capability=record.capability,
            provider=record.provider,
            collection_status=record.collection_status,
            quality=record.quality,
            text_value=record.text_value,
            boolean_value=record.boolean_value,
            source_timestamp=record.source_timestamp,
            received_timestamp=record.received_timestamp,
            dimensions=record.dimensions or None,
        )

    async def record_canonical_batch(
        self, records: list["CanonicalTelemetry"]
    ) -> int:
        """Persist many canonical records in ONE flush (batch collection path).

        Returns the number of rows written - a real count, never an estimate.
        """
        if not records:
            return 0
        rows = [
            Metric(
                tenant_id=r.tenant_id,
                device_id=r.device_id,
                interface_id=r.interface_id,
                metric_type=r.metric_name,
                metric_name=r.metric_name,
                value=r.numeric_value,
                unit=r.unit,
                recorded_at=r.timestamp,
                provider=r.provider,
                capability=r.capability,
                collection_status=r.collection_status,
                quality=r.quality,
                text_value=r.text_value,
                boolean_value=r.boolean_value,
                source_timestamp=r.source_timestamp,
                received_timestamp=r.received_timestamp,
                dimensions=r.dimensions or None,
            )
            for r in records
        ]
        self.db.add_all(rows)
        await self.db.flush()
        return len(rows)

    def _canonical_filters(
        self,
        tenant_id: uuid.UUID,
        *,
        device_id: uuid.UUID | None,
        interface_id: uuid.UUID | None,
        capability: str | None,
        metric_name: str | None,
        since: datetime | None,
        until: datetime | None,
    ) -> list:
        filters = [Metric.tenant_id == tenant_id]
        if device_id is not None:
            filters.append(Metric.device_id == device_id)
        if interface_id is not None:
            filters.append(Metric.interface_id == interface_id)
        if capability is not None:
            filters.append(Metric.capability == capability)
        if metric_name is not None:
            filters.append(Metric.metric_type == metric_name)
        if since is not None:
            filters.append(Metric.recorded_at >= since)
        if until is not None:
            filters.append(Metric.recorded_at <= until)
        return filters

    async def list_canonical(
        self,
        tenant_id: uuid.UUID,
        *,
        device_id: uuid.UUID | None = None,
        interface_id: uuid.UUID | None = None,
        capability: str | None = None,
        metric_name: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[Metric]:
        """Time-range canonical telemetry query (spec sections 33/34).

        Always bounded by ``limit`` and ordered newest-first: the frontend is
        never handed an unbounded raw series.
        """
        stmt = (
            select(Metric)
            .where(
                *self._canonical_filters(
                    tenant_id,
                    device_id=device_id,
                    interface_id=interface_id,
                    capability=capability,
                    metric_name=metric_name,
                    since=since,
                    until=until,
                )
            )
            .order_by(Metric.recorded_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def count_canonical(
        self,
        tenant_id: uuid.UUID,
        *,
        device_id: uuid.UUID | None = None,
        interface_id: uuid.UUID | None = None,
        capability: str | None = None,
        metric_name: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> int:
        stmt = select(func.count()).select_from(Metric).where(
            *self._canonical_filters(
                tenant_id,
                device_id=device_id,
                interface_id=interface_id,
                capability=capability,
                metric_name=metric_name,
                since=since,
                until=until,
            )
        )
        return (await self.db.execute(stmt)).scalar_one()

    async def latest_canonical_per_metric(
        self,
        tenant_id: uuid.UUID,
        *,
        device_id: uuid.UUID | None = None,
        interface_id: uuid.UUID | None = None,
    ) -> list[Metric]:
        """Newest row per metric_type - the resource "current values" view.

        Postgres DISTINCT ON keeps this ONE query, so a resource with 40
        metrics is not 40 round trips (spec section 34).

        DISTINCT ON expressions must match the LEFTMOST ``ORDER BY`` items, so
        the newest-row tiebreak (``recorded_at DESC``) is applied in the ORDER BY
        only - putting a ``DESC`` expression inside ``distinct()`` produces
        invalid SQL.
        """
        group_columns = [Metric.metric_type]
        if interface_id is not None:
            group_columns = [Metric.interface_id, Metric.metric_type]
        filters = self._canonical_filters(
            tenant_id,
            device_id=device_id,
            interface_id=interface_id,
            capability=None,
            metric_name=None,
            since=None,
            until=None,
        )
        stmt = (
            select(Metric)
            .where(*filters)
            .distinct(*group_columns)
            .order_by(*group_columns, Metric.recorded_at.desc())
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def distinct_capabilities_for_device(
        self, tenant_id: uuid.UUID, device_id: uuid.UUID
    ) -> list[str]:
        """Capabilities this resource has ACTUAL telemetry for.

        This is the honest evidence the capability resolver prefers over any
        profile guess.
        """
        stmt = (
            select(Metric.capability)
            .where(
                Metric.tenant_id == tenant_id,
                Metric.device_id == device_id,
                Metric.capability.is_not(None),
            )
            .distinct()
        )
        return [c for c in (await self.db.execute(stmt)).scalars().all() if c]

    async def count_by_capability_for_tenant(
        self, tenant_id: uuid.UUID
    ) -> dict[str, int]:
        stmt = (
            select(Metric.capability, func.count())
            .where(Metric.tenant_id == tenant_id, Metric.capability.is_not(None))
            .group_by(Metric.capability)
        )
        return {c: n for c, n in (await self.db.execute(stmt)).all() if c}

    async def last_recorded_at(
        self,
        tenant_id: uuid.UUID,
        *,
        device_id: uuid.UUID | None = None,
    ) -> datetime | None:
        """Newest telemetry timestamp for a tenant/resource, or None."""
        conditions = [Metric.tenant_id == tenant_id]
        if device_id is not None:
            conditions.append(Metric.device_id == device_id)
        stmt = select(func.max(Metric.recorded_at)).where(*conditions)
        return (await self.db.execute(stmt)).scalar_one_or_none()
