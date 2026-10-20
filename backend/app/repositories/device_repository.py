"""
Repository Pattern: all direct SQLAlchemy access for Device lives here. The
service layer talks to this class only - it never issues its own queries.
This keeps persistence concerns swappable/mockable independently of business
logic (e.g. for unit tests).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Device


#: Canonical device-state classification sets — mirror `UP_STATUSES` /
#: `DOWN_STATUSES` in frontend/src/lib/dashboard/metrics.ts exactly, so the
#: SQL aggregate and the frontend `classifyDevice()` can never disagree.
CANONICAL_UP_STATUSES = ("up", "online")
CANONICAL_DOWN_STATUSES = (
    "down", "offline", "critical", "error", "unreachable", "degraded", "warning",
)


class DeviceRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_external_id(
        self, monitoring_source_id: uuid.UUID, external_id: str
    ) -> Device | None:
        stmt = select(Device).where(
            Device.monitoring_source_id == monitoring_source_id,
            Device.external_id == external_id,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_serial_number(
        self, tenant_id: uuid.UUID, serial_number: str
    ) -> Device | None:
        """Resolve a device by AUTHORITATIVE hardware serial within a tenant.

        This is the cross-source identity lookup (Phase 4 §16/§17): a device seen
        by SNMP *and* by a vendor API is one Device, so a second collector
        enriches the existing row instead of creating a duplicate.

        Tenant-scoped, always: a serial in another tenant must never resolve here
        (§20). The serial is normalised first, so placeholder junk such as
        "To be filled by O.E.M." resolves to nothing rather than merging
        unrelated devices. Soft-deleted rows are ignored.
        """
        from app.services.discovery.identity import normalise_serial

        key = normalise_serial(serial_number)
        if key is None:
            return None
        stmt = (
            select(Device)
            .where(
                Device.tenant_id == tenant_id,
                Device.serial_number == key,
                Device.deleted_at.is_(None),
            )
            .order_by(Device.created_at)
            .limit(1)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert(
        self,
        *,
        tenant_id: uuid.UUID,
        monitoring_source_id: uuid.UUID,
        external_id: str,
        name: str,
        device_type: str,
        vendor: str | None = None,
        model: str | None = None,
        os_version: str | None = None,
        hostname: str | None = None,
        ip_address: str | None = None,
        mac_address: str | None = None,
        status: str = "unknown",
        site_id: uuid.UUID | None = None,
        serial_number: str | None = None,
        custom_metadata: dict | None = None,
        # -- universal resource model (additive, all optional) ---------------
        # Existing callers pass none of these and behave exactly as before.
        provider: str | None = None,
        parent_resource_id: uuid.UUID | None = None,
        lifecycle_state: str | None = None,
        health: str | None = None,
        last_telemetry_at: datetime | None = None,
    ) -> tuple[Device, bool]:
        """Create or refresh a device from an adapter reading.

        Returns (device, created) so the sync service can report accurate
        created/updated counts back to the caller.

        `custom_metadata` is the collector overflow channel documented on
        `NormalizedDevice.custom_metadata` (e.g. Phase 3B switching facts). It
        was declared but never persisted before, so callers' metadata was
        silently dropped. On update the incoming keys are MERGED over the
        existing dict rather than replacing it: two monitoring sources can
        contribute different keys to the same device, and a source that knows
        nothing about switching must not erase what another one discovered.
        Passing None leaves existing metadata untouched.

        CROSS-SOURCE IDENTITY (Phase 4 §16/§17)
        --------------------------------------
        Resolution order is:
          1. (monitoring_source_id, external_id) - the same collector re-polling,
             which is the normal steady-state path and is unchanged;
          2. hardware serial within the tenant - a DIFFERENT collector observing
             the same physical device, which must enrich one canonical Device
             rather than create a duplicate.
        The owning (monitoring_source_id, external_id) pair is NEVER reassigned
        on a cross-source merge. Reassigning it would make the original source
        stop finding its own device on its next poll and create a duplicate again,
        which is the exact failure this exists to prevent.
        """
        from app.services.discovery.identity import normalise_serial

        # Store the canonical form so matching is stable across case/whitespace
        # differences; None when the provider gave placeholder junk.
        serial = normalise_serial(serial_number)
        now = datetime.now(timezone.utc)

        existing = await self.get_by_external_id(monitoring_source_id, external_id)
        merged_from_another_source = False

        if existing is None and serial is not None:
            existing = await self.get_by_serial_number(tenant_id, serial)
            merged_from_another_source = existing is not None

        if existing is None:
            device = Device(
                tenant_id=tenant_id,
                monitoring_source_id=monitoring_source_id,
                external_id=external_id,
                name=name,
                device_type=device_type,
                vendor=vendor,
                model=model,
                os_version=os_version,
                hostname=hostname,
                ip_address=ip_address,
                mac_address=mac_address,
                status=status,
                site_id=site_id,
                serial_number=serial,
                custom_metadata=custom_metadata,
                last_seen_at=now,
                provider=provider,
                parent_resource_id=parent_resource_id,
                lifecycle_state=lifecycle_state or "discovered",
                health=health,
                discovered_at=now,
                last_telemetry_at=last_telemetry_at,
            )
            self.db.add(device)
            await self.db.flush()
            await self.db.refresh(device)
            return device, True

        existing.name = name
        existing.device_type = device_type
        existing.vendor = vendor
        existing.model = model
        existing.os_version = os_version
        existing.hostname = hostname
        existing.ip_address = ip_address
        existing.mac_address = mac_address
        existing.status = status
        existing.site_id = site_id
        if serial is not None:
            # Never blank out a known serial just because this source could not
            # read one; a serial is permanent identity evidence.
            existing.serial_number = serial
        if custom_metadata is not None:
            # Merge, do not replace - see the docstring.
            existing.custom_metadata = {
                **(existing.custom_metadata or {}),
                **custom_metadata,
            }
        if merged_from_another_source:
            # Record which source observed it, so multi-collector enrichment is
            # visible instead of looking like one collector's device.
            metadata = dict(existing.custom_metadata or {})
            observers = list(metadata.get("observed_by_source_ids") or [])
            observer = str(monitoring_source_id)
            if observer not in observers:
                observers.append(observer)
            metadata["observed_by_source_ids"] = observers
            existing.custom_metadata = metadata
        # Universal resource model: only ever ENRICH on update. A refresh that
        # does not carry a provider/hierarchy/lifecycle/telemetry fact must not
        # erase one another collector (or an operator) already established.
        if provider is not None:
            existing.provider = provider
        if parent_resource_id is not None:
            existing.parent_resource_id = parent_resource_id
        if lifecycle_state is not None:
            existing.lifecycle_state = lifecycle_state
        if health is not None:
            existing.health = health
        if last_telemetry_at is not None:
            existing.last_telemetry_at = last_telemetry_at
        if existing.discovered_at is None:
            existing.discovered_at = existing.created_at or now
        existing.last_seen_at = now
        await self.db.flush()
        await self.db.refresh(existing)
        return existing, False

    async def apply_resource_facts(
        self,
        *,
        tenant_id: uuid.UUID,
        device_id: uuid.UUID,
        provider: str | None = None,
        last_telemetry_at: datetime | None = None,
        mark_active: bool = False,
    ) -> bool:
        """Record universal-resource facts observed by a collector run.

        Deliberately NOT a general update: it only ever ENRICHES the columns the
        universal layer owns, so an unrelated caller cannot accidentally blank a
        field. ``mark_active`` only promotes ``discovered`` -> ``active`` (a
        real inference: the resource produced telemetry), and never demotes an
        operator's explicit ``maintenance``/``retired`` state.

        Returns True when a row was found and updated.
        """
        assert tenant_id is not None  # tenant isolation is not optional here
        stmt = select(Device).where(
            Device.id == device_id,
            Device.tenant_id == tenant_id,
            Device.is_deleted.is_(False),
        )
        device = (await self.db.execute(stmt)).scalar_one_or_none()
        if device is None:
            return False
        if provider is not None:
            device.provider = provider
        if last_telemetry_at is not None:
            device.last_telemetry_at = last_telemetry_at
        if device.discovered_at is None:
            device.discovered_at = device.created_at or datetime.now(timezone.utc)
        if mark_active and (device.lifecycle_state or "discovered") == "discovered":
            device.lifecycle_state = "active"
        await self.db.flush()
        await self.db.refresh(device)
        return True

    async def list_by_ids(
        self, tenant_id: uuid.UUID, device_ids: list[uuid.UUID]
    ) -> list[Device]:
        """Fetch MANY devices in ONE tenant-scoped query (N+1 avoidance)."""
        if not device_ids:
            return []
        stmt = select(Device).where(
            Device.tenant_id == tenant_id,
            Device.id.in_(device_ids),
            Device.is_deleted.is_(False),
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def counts_by_type(self, tenant_id: uuid.UUID) -> dict[str, int]:
        """Device count per STORED ``device_type``, in one query."""
        stmt = (
            select(Device.device_type, func.count())
            .where(Device.tenant_id == tenant_id, Device.is_deleted.is_(False))
            .group_by(Device.device_type)
        )
        return {
            device_type: count
            for device_type, count in (await self.db.execute(stmt)).all()
        }

    async def counts_by_health(self, tenant_id: uuid.UUID) -> dict[str, int]:
        """Device count per persisted ``health`` bucket (NULL = not computed)."""
        stmt = (
            select(Device.health, func.count())
            .where(Device.tenant_id == tenant_id, Device.is_deleted.is_(False))
            .group_by(Device.health)
        )
        return {health: count for health, count in (await self.db.execute(stmt)).all()}

    async def counts_by_status(self, tenant_id: uuid.UUID) -> dict[str, int]:
        """Device count per persisted ``status``, in ONE query (PHASE 6A).

        Deliberately grouped on ``status`` (the collector-reported state) rather
        than on ``health`` (the universal layer's later health-pass bucket, which
        is NULL until such a pass has run and would therefore report most of an
        estate as unclassifiable). ``status`` is the column the collectors
        actually write and the one the dashboard's device health KPI is defined
        against, so this is the same signal the UI already uses - just measured
        once, server-side.

        Any value the collector vocabulary does not define is preserved as its
        own key and is bucketed as "unknown" by the service, never as a problem.
        """
        stmt = (
            select(Device.status, func.count())
            .where(Device.tenant_id == tenant_id, Device.is_deleted.is_(False))
            .group_by(Device.status)
        )
        return {status: count for status, count in (await self.db.execute(stmt)).all()}

    async def counts_by_status_and_state(
        self,
        tenant_id: uuid.UUID,
        *,
        now: datetime,
        freshness_seconds: int,
    ) -> tuple[dict[str, int], dict[str, int]]:
        """Raw status distribution + canonical state classification, ONE query.

        Returns ``(status_counts, state_counts)``. The canonical half mirrors
        ``classifyDevice()`` in ``frontend/src/lib/dashboard/metrics.ts`` exactly,
        so the backend aggregate and the frontend selector can never drift:

          last_seen_at IS NULL                        -> unknown
          last_seen_at < now - freshness_seconds      -> stale
          lower(trim(status)) in ("up", "online")     -> up
          lower(trim(status)) in ("down", "offline",
              "critical", "error", "unreachable",
              "degraded", "warning")                  -> down
          else                                        -> unknown

        Freshness wins: a device whose ``last_seen_at`` is too old is ``stale``
        regardless of its status, and a NULL ``last_seen_at`` is ``unknown`` — so
        a device is only ``down`` with recent confirming evidence, never merely
        because telemetry has stopped. Both halves share ONE GROUP BY and the
        same filter (tenant + not deleted), so
        ``state_counts["total"] == sum(status_counts.values())``.
        """
        stale_cutoff = now - timedelta(seconds=freshness_seconds)
        state_expr = case(
            (Device.last_seen_at.is_(None), "unknown"),
            (Device.last_seen_at < stale_cutoff, "stale"),
            (func.lower(func.trim(Device.status)).in_(CANONICAL_UP_STATUSES), "up"),
            (func.lower(func.trim(Device.status)).in_(CANONICAL_DOWN_STATUSES), "down"),
            else_="unknown",
        )
        stmt = (
            select(Device.status, state_expr.label("state"), func.count().label("n"))
            .where(Device.tenant_id == tenant_id, Device.is_deleted.is_(False))
            .group_by(Device.status, state_expr)
        )
        status_counts: dict[str, int] = {}
        state_counts: dict[str, int] = {"up": 0, "down": 0, "stale": 0, "unknown": 0}
        for status, state, n in (await self.db.execute(stmt)).all():
            status_counts[status] = status_counts.get(status, 0) + n
            state_counts[state] += n
        state_counts["total"] = sum(state_counts.values())
        return status_counts, state_counts

    async def counts_by_state(
        self,
        tenant_id: uuid.UUID,
        *,
        now: datetime,
        freshness_seconds: int,
    ) -> dict[str, int]:
        """Canonical state counts (up/down/stale/unknown/total).

        Convenience wrapper over :meth:`counts_by_status_and_state` for callers
        that only need the classification, not the raw status distribution.
        """
        _, state_counts = await self.counts_by_status_and_state(
            tenant_id, now=now, freshness_seconds=freshness_seconds
        )
        return state_counts

    async def counts_by_site_and_type(
        self, tenant_id: uuid.UUID
    ) -> dict[uuid.UUID | None, dict[str, int]]:
        """Per-site device-type counts in ONE query, for site overviews."""
        stmt = (
            select(Device.site_id, Device.device_type, func.count())
            .where(Device.tenant_id == tenant_id, Device.is_deleted.is_(False))
            .group_by(Device.site_id, Device.device_type)
        )
        grouped: dict[uuid.UUID | None, dict[str, int]] = {}
        for site_id, device_type, count in (await self.db.execute(stmt)).all():
            grouped.setdefault(site_id, {})[device_type] = count
        return grouped

    async def counts_by_monitoring_source(
        self, tenant_id: uuid.UUID
    ) -> dict[uuid.UUID | None, int]:
        """Device count per ``monitoring_source_id``, in ONE GROUP BY (P1).

        The Command Center's collection-health rows report
        ``affected_resources`` — how many devices a failing source is
        responsible for — and this is that attribution, measured once for the
        whole tenant instead of one COUNT per source (no N+1).
        """
        stmt = (
            select(Device.monitoring_source_id, func.count())
            .where(Device.tenant_id == tenant_id, Device.is_deleted.is_(False))
            .group_by(Device.monitoring_source_id)
        )
        return {row[0]: row[1] for row in (await self.db.execute(stmt)).all()}

    async def count(
        self,
        tenant_id: uuid.UUID,
        *,
        site_id: uuid.UUID | None = None,
        device_type: str | None = None,
        vendor: str | None = None,
        status: str | None = None,
        monitoring_source_id: uuid.UUID | None = None,
        search: str | None = None,
    ) -> int:
        stmt = select(func.count()).select_from(Device).where(
            Device.tenant_id == tenant_id,
            Device.is_deleted.is_(False),
        )
        if site_id is not None:
            stmt = stmt.where(Device.site_id == site_id)
        if device_type is not None:
            stmt = stmt.where(Device.device_type == device_type)
        if vendor is not None:
            stmt = stmt.where(Device.vendor == vendor)
        if status is not None:
            stmt = stmt.where(Device.status == status)
        if monitoring_source_id is not None:
            stmt = stmt.where(Device.monitoring_source_id == monitoring_source_id)
        if search:
            like = f"%{search}%"
            stmt = stmt.where(
                (Device.name.ilike(like))
                | (Device.hostname.ilike(like))
                | (Device.ip_address.ilike(like))
            )
        result = await self.db.execute(stmt)
        return result.scalar_one()

    async def get(self, device_id: uuid.UUID, tenant_id: uuid.UUID) -> Device | None:
        stmt = select(Device).where(
            Device.id == device_id,
            Device.tenant_id == tenant_id,
            Device.is_deleted.is_(False),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list(
        self,
        tenant_id: uuid.UUID,
        *,
        site_id: uuid.UUID | None = None,
        device_type: str | None = None,
        vendor: str | None = None,
        status: str | None = None,
        monitoring_source_id: uuid.UUID | None = None,
        search: str | None = None,
        sort_by: str = "name",
        sort_dir: str = "asc",
        limit: int | None = 100,
        offset: int = 0,
    ) -> list[Device]:
        stmt = select(Device).where(
            Device.tenant_id == tenant_id,
            Device.is_deleted.is_(False),
        )
        if site_id is not None:
            stmt = stmt.where(Device.site_id == site_id)
        if device_type is not None:
            stmt = stmt.where(Device.device_type == device_type)
        if vendor is not None:
            stmt = stmt.where(Device.vendor == vendor)
        if status is not None:
            stmt = stmt.where(Device.status == status)
        if monitoring_source_id is not None:
            stmt = stmt.where(Device.monitoring_source_id == monitoring_source_id)
        if search:
            like = f"%{search}%"
            stmt = stmt.where(
                (Device.name.ilike(like))
                | (Device.hostname.ilike(like))
                | (Device.ip_address.ilike(like))
            )

        # Sortable columns for the Infrastructure/Device tables - restricted
        # to a fixed allowlist so `sort_by` can never be used to inject an
        # arbitrary column/expression.
        sortable_columns = {
            "name": Device.name,
            "hostname": Device.hostname,
            "ip_address": Device.ip_address,
            "vendor": Device.vendor,
            "model": Device.model,
            "status": Device.status,
            "last_seen_at": Device.last_seen_at,
        }
        column = sortable_columns.get(sort_by, Device.name)
        stmt = stmt.order_by(column.desc() if sort_dir == "desc" else column.asc())
        stmt = stmt.limit(limit).offset(offset)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def count_by_status_for_site(self, site_id: uuid.UUID) -> dict[str, int]:
        """Per-site device status breakdown (online/offline/warning/critical
        counts), used by the Infrastructure page's Site cards. Device.status
        values are up|down|warning|unknown - "critical" isn't a distinct
        Device.status today, so it's derived as an alias of "down" for
        display purposes (see the Infrastructure API route for the mapping)."""
        stmt = (
            select(Device.status, func.count())
            .where(Device.site_id == site_id, Device.is_deleted.is_(False))
            .group_by(Device.status)
        )
        result = await self.db.execute(stmt)
        return {status: count for status, count in result.all()}

    async def hourly_down_counts(self, tenant_id: uuid.UUID, *, since: datetime) -> dict[datetime, int]:
        """``{hour: devices whose status is 'down'}`` for the window (P1).

        Buckets are truncations of ``last_seen_at`` — the only timestamp a
        device row carries — so the trend series shows when the estate last
        reported a down device.
        """
        hour = func.date_trunc("hour", Device.last_seen_at)
        stmt = (
            select(hour, func.count())
            .where(
                Device.tenant_id == tenant_id,
                Device.is_deleted.is_(False),
                Device.status == "down",
                Device.last_seen_at >= since,
            )
            .group_by(hour)
        )
        return {bucket: count for bucket, count in (await self.db.execute(stmt)).all()}
