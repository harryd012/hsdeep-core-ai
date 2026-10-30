"""
Normalization Engine: persistence step.

`BundleSaver.save()` takes a collector's pure-function output
(`NormalizedBundle`) and persists it via the existing repositories, in the
fixed order device -> interface -> sensor -> metric -> event, resolving
cross-entity references by `external_id` within that single bundle (per the
design's "Order of operations"). Alert writes stay outside this class -
`BundleSaver` hands `bundle.alerts` back to the caller (the
`CollectionOrchestrator`) via `SaveResult.vendor_alert_signals`, which then
routes them through `AlertService`/`AlertRepository`.

Reference-safety (Property 8): `save()` must never raise because a
`NormalizedInterface`/`NormalizedSensorReading`/`NormalizedMetric`/
`NormalizedEvent` references a `device_external_id` (or, for sensors, an
`interface_external_id`) that isn't present among the bundle's devices (or
interfaces). Per Requirement 4.3, the general rule is "persist the entity
anyway with its device foreign key set to NULL". Two entities in this
module deliberately deviate from that general rule because the already-
migrated schema does not support it - see the KNOWN GAPS note below.

KNOWN GAPS (repository layer is locked; these are architecture-level gaps,
not implementation choices made in this file):

1. Interface.device_id is NOT NULL (see `app/models/interface.py`,
   `ForeignKey("devices.id", ondelete="CASCADE")`, `nullable=False`). A
   `NormalizedInterface` whose `device_external_id` does not resolve to an
   upserted `Device` in this bundle therefore CANNOT be persisted via
   `InterfaceRepository.upsert()` (it would violate the NOT NULL
   constraint - the repository method has no way to pass a NULL
   `device_id`, nor should it be made to, since the constraint is
   intentional at the schema level). This file resolves that tension by
   SKIPPING such interfaces (no repository call, no exception) and logging
   a warning, rather than discarding the whole bundle or crashing. This is
   a deliberate, model-driven exception to Requirement 4.3's general
   "persist with NULL FK" rule for this one entity type.

2. SensorRepository.upsert() has no `device_id` parameter at all (see
   `app/repositories/sensor_repository.py`) - only `interface_id`. Even
   when a `NormalizedSensorReading.device_external_id` resolves cleanly to
   an upserted `Device` in this bundle, that link currently cannot be
   persisted onto the `Sensor` row through the existing repository method.
   `Sensor.device_id` therefore stays whatever it already was (NULL for a
   newly created sensor) until a future, separate change extends
   `SensorRepository.upsert()` with a `device_id` keyword - not done here
   because the repository layer is locked for this task.

3. `NormalizedDevice.site_hint` (a best-effort site name/code) is defined
   on the normalized shape, but there is no `SiteRepository` (or any
   existing repository method) that resolves a `Site` by name/code for a
   tenant. Per Requirement 7.5, service layers may only access an entity
   through its repository, and no new repository method may be added for
   this task. `site_hint` resolution therefore always resolves to
   `site_id=None` for now. This is a documented future enhancement, not a
   bug: once a Site-lookup repository method exists, this file's device
   loop should be updated to call it here.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.collectors.base import NormalizedAlertSignal, NormalizedBundle
from app.models.monitoring_source import MonitoringSource
from app.services.monitoring.interface_rates import compute_interface_rate
from app.models.sensor import Sensor
from app.repositories.device_repository import DeviceRepository
from app.repositories.event_repository import EventRepository
from app.repositories.interface_repository import InterfaceRepository
from app.repositories.metric_repository import MetricRepository
from app.repositories.sensor_repository import SensorRepository
# Universal infrastructure intelligence: the canonical telemetry + capability
# layer this saver now also feeds. Imported at module level (no cycle: the
# universal modules import only models/vocabulary at import time).
from app.services.infrastructure_intelligence import telemetry as canonical_telemetry
from app.services.infrastructure_intelligence.pipeline import (
    CanonicalTelemetrySaver,
    capability_of_metric,
    normalize_bundle,
)
from app.services.infrastructure_intelligence.providers import (
    provider_from_source_type,
)

logger = logging.getLogger(__name__)


@dataclass
class SaveResult:
    devices_upserted: int = 0
    interfaces_upserted: int = 0
    #: Phase 9.8: interfaces whose rx/tx rate was derived from two counter
    #: samples this run. 0 on a first collection is the honest answer, not a
    #: missing measurement.
    rates_measured: int = 0
    sensors_created: int = 0
    sensors_updated: int = 0
    metrics_recorded: int = 0
    events_recorded: int = 0
    alerts_upserted: int = 0
    # Plan-limit enforcement (P1 remediation): NEW devices/sensors that were
    # NOT persisted because the tenant had no active subscription or was at
    # its plan limit. Updates to EXISTING rows are never blocked.
    devices_blocked: int = 0
    sensors_blocked: int = 0
    changed_sensors: list[Sensor] = field(default_factory=list)
    vendor_alert_signals: list[NormalizedAlertSignal] = field(default_factory=list)
    # external_id -> resolved internal Device.id, for every
    # NormalizedAlertSignal in vendor_alert_signals whose
    # device_external_id matched a device upserted THIS save. Populated
    # here (not by the caller) because this bundle-scoped
    # device_id_by_external map only exists during save() - see
    # AlertService.upsert_vendor_native()'s device_id parameter, which
    # CollectionOrchestrator resolves via this map (Alert Correlation
    # Engine sprint - closes the gap where vendor-native alerts never got
    # a device_id, which blocked device-based correlation for the primary
    # PRTG "messages" path).
    device_id_by_signal_device_external_id: dict[str, object] = field(default_factory=dict)
    # Phase 3A: observed LLDP/CDP adjacencies persisted as topology edges.
    # `neighbors_written` counts edges created/refreshed via
    # DiscoveryService._write_neighbors; `neighbors_unresolved` counts
    # adjacencies that were observed but could NOT be matched to an existing
    # Device in this tenant, so no edge was written for them (Step 5).
    neighbors_written: int = 0
    neighbors_unresolved: int = 0
    #: Per-protocol collection outcome, copied from the bundle so callers can
    #: tell "SNMP poll failed" apart from "no neighbours reported" (Step 10).
    neighbor_status: dict[str, str] = field(default_factory=dict)
    # -- L3 topology: interface addresses and routes ---------------------------
    addresses_upserted: int = 0
    addresses_retired: int = 0
    routes_upserted: int = 0
    routes_retired: int = 0
    #: Dynamic routes dropped by ``l3_max_dynamic_routes_per_device``.
    routes_capped: int = 0
    l3_status: dict[str, str] = field(default_factory=dict)
    # -- Firewall gateway -------------------------------------------------------
    firewalls_updated: int = 0
    vpn_tunnels_upserted: int = 0
    vpn_tunnels_retired: int = 0
    firewall_status: dict[str, str] = field(default_factory=dict)
    # -- Backup monitoring --------------------------------------------------
    backup_vaults_updated: int = 0
    backup_items_upserted: int = 0
    backup_items_retired: int = 0
    backup_jobs_upserted: int = 0
    backup_status: dict[str, str] = field(default_factory=dict)
    # -- Universal infrastructure layer (spec: canonical telemetry) -----------
    #: Availability observations written for this bundle (one per device, from
    #: the device's own reported status).
    availability_recorded: int = 0
    #: Capability assertions refreshed in `resource_capabilities`.
    capabilities_asserted: int = 0
    #: Resources whose universal-resource facts were updated (provider, last
    #: telemetry timestamp, lifecycle promotion).
    resources_enriched: int = 0
    #: The full normalization summary, so a caller/job log can see exactly what
    #: the universal layer derived from this bundle.
    canonical: dict = field(default_factory=dict)


class BundleSaver:
    """Persists a `NormalizedBundle` via the existing repositories.

    Takes the five repositories as constructor-injected dependencies,
    matching the existing service-layer convention (see
    `AlertService.__init__`, `MonitoringService.__init__`) rather than
    constructing them itself.
    """

    def __init__(
        self,
        device_repo: DeviceRepository,
        interface_repo: InterfaceRepository,
        sensor_repo: SensorRepository,
        metric_repo: MetricRepository,
        event_repo: EventRepository,
        billing: "BillingService | None" = None,
    ):
        self.device_repo = device_repo
        self.interface_repo = interface_repo
        self.sensor_repo = sensor_repo
        self.metric_repo = metric_repo
        self.event_repo = event_repo
        # Optional BillingService - when provided, NEW device/sensor creation
        # is gated by the tenant's plan limits (P1 remediation). When None,
        # saving behaves exactly as before (used by tests / legacy call sites).
        self.billing = billing

    async def _plan_allowances(self, source: MonitoringSource) -> tuple[int | None, int | None]:
        """Resolve how many NEW devices/sensors this save may create.

        Returns ``(devices_allowance, sensors_allowance)`` where ``None``
        means "unmanaged" (no billing wired). A tenant with no ACTIVE
        subscription (none at all, or suspended/canceled/incomplete) gets a
        0/0 allowance - ingestion degrades gracefully (Property 8: save()
        never raises), it just stops creating new resources.
        """
        if self.billing is None:
            return (None, None)

        from app.core.entitlements import _audit_entitlement_denial
        from app.services.billing_service import SubscriptionNotFound

        try:
            entitlements = await self.billing.get_entitlements(source.tenant_id)
        except SubscriptionNotFound:
            await _audit_entitlement_denial(
                source.tenant_id,
                "devices",
                "no_subscription",
                "BundleSaver blocked new resource creation: no active subscription",
            )
            return (0, 0)

        if entitlements.get("over_limit_blocked"):
            await _audit_entitlement_denial(
                source.tenant_id,
                "devices",
                "over_limit_blocked",
                "BundleSaver blocked new resource creation: downgrade over_limit_blocked flag set",
            )
            return (0, 0)

        device_allowance: int | None = None
        sensor_allowance: int | None = None
        for limit in entitlements["limits"]:
            if limit["resource"] == "devices":
                device_allowance = 0 if limit["over_limit"] else max(limit["limit"] - limit["used"], 0)
                if device_allowance == 0:
                    await _audit_entitlement_denial(
                        source.tenant_id, "devices", "plan_limit_exceeded",
                        f"Device limit {limit['limit']} reached (used {limit['used']}); "
                        "BundleSaver dropped new device creation",
                    )
            elif limit["resource"] == "sensors":
                sensor_allowance = 0 if limit["over_limit"] else max(limit["limit"] - limit["used"], 0)
                if sensor_allowance == 0:
                    await _audit_entitlement_denial(
                        source.tenant_id, "sensors", "plan_limit_exceeded",
                        f"Sensor limit {limit['limit']} reached (used {limit['used']}); "
                        "BundleSaver dropped new sensor creation",
                    )
        return (device_allowance, sensor_allowance)

    async def _save_l3(
        self,
        source: MonitoringSource,
        bundle: NormalizedBundle,
        result: SaveResult,
        *,
        provider: str | None,
        device_id_by_external: dict[str, object],
        interface_id_by_key: dict[tuple[str, str], object],
        interface_id_by_name: dict[tuple[str, str], object],
    ) -> None:
        """Sync interface addresses and routes for every device in the bundle.

        Runs only for the tables the collector says it read (``l3_status``),
        so collectors that know nothing about L3 are untouched. Stored rows are
        retired only when the read succeeded ("collected" or "empty").
        """
        from app.core.config import get_settings
        from app.repositories.interface_address_repository import (
            AddressInput,
            InterfaceAddressRepository,
        )
        from app.repositories.network_route_repository import (
            NetworkRouteRepository,
            RouteInput,
        )

        result.l3_status = dict(bundle.l3_status)
        address_status = bundle.l3_status.get("addresses")
        route_status = bundle.l3_status.get("routes")
        if address_status is None and route_status is None:
            return

        db = self.device_repo.db
        device_ext_ids = [
            nd.external_id for nd in bundle.devices if nd.external_id in device_id_by_external
        ]

        def _interface_id(device_ext: str, external_id: str | None, name: str | None):
            if external_id is not None:
                found = interface_id_by_key.get((device_ext, external_id))
                if found is not None:
                    return found
            if name is not None:
                return interface_id_by_name.get((device_ext, name))
            return None

        if address_status is not None:
            retire = address_status in ("collected", "empty")
            by_device: dict[str, list] = {ext: [] for ext in device_ext_ids}
            for na in bundle.addresses:
                if na.device_external_id in by_device:
                    by_device[na.device_external_id].append(AddressInput(
                        address=na.address,
                        prefix_length=na.prefix_length,
                        vrf=na.vrf,
                        interface_id=_interface_id(
                            na.device_external_id, na.interface_external_id, na.interface_name
                        ),
                        interface_name=na.interface_name,
                        is_primary=na.is_primary,
                    ))
            repo = InterfaceAddressRepository(db)
            for ext, items in by_device.items():
                if not items and not retire:
                    continue
                synced = await repo.sync_for_device(
                    tenant_id=source.tenant_id,
                    device_id=device_id_by_external[ext],
                    addresses=items,
                    monitoring_source_id=source.id,
                    provider=provider,
                    retire_missing=retire,
                )
                result.addresses_upserted += synced.upserted
                result.addresses_retired += synced.retired

        if route_status is not None:
            retire = route_status in ("collected", "empty")
            cap = get_settings().l3_max_dynamic_routes_per_device
            routes_by_device: dict[str, list] = {ext: [] for ext in device_ext_ids}
            dynamic_kept: dict[str, int] = {}
            for nr in bundle.routes:
                if nr.device_external_id not in routes_by_device:
                    continue
                if nr.protocol not in ("connected", "static"):
                    kept = dynamic_kept.get(nr.device_external_id, 0)
                    if kept >= cap:
                        result.routes_capped += 1
                        continue
                    dynamic_kept[nr.device_external_id] = kept + 1
                routes_by_device[nr.device_external_id].append(RouteInput(
                    destination=nr.destination,
                    prefix_length=nr.prefix_length,
                    protocol=nr.protocol,
                    next_hop=nr.next_hop,
                    outgoing_interface=nr.outgoing_interface,
                    interface_id=_interface_id(nr.device_external_id, None, nr.outgoing_interface),
                    metric=nr.metric,
                    administrative_distance=nr.administrative_distance,
                    vrf=nr.vrf,
                    is_best=nr.is_best,
                ))
            if result.routes_capped:
                logger.warning(
                    "L3: dropped %d dynamic routes over the per-device cap of %d (source %s)",
                    result.routes_capped, cap, source.id,
                )
            route_repo = NetworkRouteRepository(db)
            for ext, items in routes_by_device.items():
                if not items and not retire:
                    continue
                upserted, retired = await route_repo.sync_for_resource(
                    tenant_id=source.tenant_id,
                    resource_id=device_id_by_external[ext],
                    provider=provider,
                    routes=items,
                    retire_missing=retire,
                )
                result.routes_upserted += upserted
                result.routes_retired += retired

    async def save(self, source: MonitoringSource, bundle: NormalizedBundle) -> SaveResult:
        """Persist every entity in `bundle`, in device -> interface ->
        sensor -> metric -> event order, resolving cross-entity references
        by `external_id` within this bundle only.

        `source` is duck-typed: at runtime `BaseCollector.save()` actually
        passes its `MonitoringSourceConnectionInfo` (which, like
        `MonitoringSource`, exposes `.id`/`.tenant_id`), while orchestrator
        or test call sites may pass the `MonitoringSource` ORM row itself -
        both work here since only `.id`/`.tenant_id` are read.

        Never raises for dangling references or duplicate external_ids
        within `bundle` (Property 8): each per-item loop below calls an
        idempotent repository upsert independently, so a duplicate
        external_id in the same bundle naturally resolves to "last one
        wins" with no extra de-duplication logic required.
        """
        result = SaveResult()

        #: Provider token for this source, used to attribute canonical telemetry
        #: and capability assertions (None when the source type has no mapping,
        #: which the capability resolver reports as "unknown" rather than
        #: guessing).
        provider_token = provider_from_source_type(
            getattr(source, "source_type", None)
        )

        # -- 0. Plan-limit allowances (P1 remediation) ----------------------
        # None means "unmanaged" - no gating. A 0 allowance blocks NEW
        # creations only; updates to existing external_ids always pass.
        device_allowance, sensor_allowance = await self._plan_allowances(source)

        # -- 1. Devices ----------------------------------------------------
        # external_id -> Device.id, built as we go so later steps can
        # resolve interface/sensor/metric/event references against it.
        device_id_by_external: dict[str, object] = {}
        # Device.id -> Device.site_id, so canonical telemetry rows can carry the
        # site they belong to without a second lookup.
        site_id_by_device: dict[object, object] = {}

        for nd in bundle.devices:
            existing_device = await self.device_repo.get_by_external_id(source.id, nd.external_id)
            if existing_device is None and device_allowance is not None and device_allowance <= 0:
                result.devices_blocked += 1
                logger.warning(
                    "Plan limit: skipping NEW device external_id=%r for tenant %s "
                    "(no active subscription or plan device_limit reached)",
                    nd.external_id, source.tenant_id,
                )
                continue
            # site_hint resolution is unavailable - see KNOWN GAPS note #3
            # at the top of this module. Always resolve to site_id=None.
            device, _created = await self.device_repo.upsert(
                tenant_id=source.tenant_id,
                monitoring_source_id=source.id,
                external_id=nd.external_id,
                name=nd.name,
                device_type=nd.device_type,
                vendor=nd.vendor,
                model=nd.model,
                os_version=nd.os_version,
                hostname=nd.hostname,
                ip_address=nd.ip_address,
                mac_address=nd.mac_address,
                status=nd.status,
                site_id=None,
                serial_number=nd.serial_number or None,
                custom_metadata=nd.custom_metadata or None,
            )
            device_id_by_external[nd.external_id] = device.id
            site_id_by_device[device.id] = device.site_id
            result.devices_upserted += 1

        # -- 2. Interfaces ---------------------------------------------------
        # (device_external_id, interface_external_id) -> Interface.id, for
        # sensor interface_id resolution in step 3.
        interface_id_by_key: dict[tuple[str, str], object] = {}
        # (device external_id, interface NAME) -> Interface.id: routes and some
        # address tables name the interface rather than giving its external id.
        interface_id_by_name: dict[tuple[str, str], object] = {}

        # Phase 9.8: previous counter sample per (device, interface), read in ONE
        # query so a two-sample rate can be derived. A missing previous row just
        # means "first observation": no rate and never a fabricated zero.
        collected_at = datetime.now(timezone.utc)
        previous_by_key: dict[tuple[object, str], object] = {}
        if bundle.interfaces and device_id_by_external:
            for existing_row in await self.interface_repo.list_for_devices(
                source.tenant_id, list(device_id_by_external.values())
            ):
                previous_by_key[(existing_row.device_id, existing_row.external_id)] = existing_row

        for ni in bundle.interfaces:
            device_id = device_id_by_external.get(ni.device_external_id)
            if device_id is None:
                # KNOWN GAP #1: Interface.device_id is NOT NULL at the
                # schema level, so an interface with an unresolvable device
                # reference cannot be persisted with a NULL FK the way
                # Requirement 4.3 generally asks for. Skip it (do not call
                # the repository, do not raise) rather than violating the
                # constraint or discarding the whole bundle.
                logger.warning(
                    "Skipping interface external_id=%r: unresolvable device_external_id=%r "
                    "(Interface.device_id is NOT NULL; cannot persist with a NULL device FK)",
                    ni.external_id,
                    ni.device_external_id,
                )
                continue

            previous = previous_by_key.get((device_id, ni.external_id))
            rate = compute_interface_rate(
                previous_in=previous.in_octets if previous is not None else None,
                previous_out=previous.out_octets if previous is not None else None,
                previous_at=previous.last_telemetry_at if previous is not None else None,
                current_in=ni.in_octets,
                current_out=ni.out_octets,
                observed_at=collected_at,
                speed_mbps=ni.speed_mbps,
            )

            interface, _created = await self.interface_repo.upsert(
                tenant_id=source.tenant_id,
                device_id=device_id,
                external_id=ni.external_id,
                name=ni.name,
                monitoring_source_id=source.id,
                mac_address=ni.mac_address,
                admin_status=ni.admin_status,
                oper_status=ni.oper_status,
                speed_mbps=ni.speed_mbps,
                in_octets=ni.in_octets,
                out_octets=ni.out_octets,
                in_errors=ni.in_errors,
                out_errors=ni.out_errors,
                # Derived ONLY from two real samples; None leaves the column
                # untouched/NULL rather than publishing a zero.
                rx_bps=rate.rx_bps,
                tx_bps=rate.tx_bps,
                utilization_pct=rate.utilization_pct,
                # Drives Interface.last_telemetry_at (freshness), so a poll that
                # carried counters is distinguishable from one that did not.
                telemetry_seen=rate.has_counters,
            )
            if rate.measured:
                result.rates_measured += 1
            interface_id_by_key[(ni.device_external_id, ni.external_id)] = interface.id
            interface_id_by_name[(ni.device_external_id, ni.name)] = interface.id
            result.interfaces_upserted += 1

        # -- 2b. Firewall state, VPN tunnels and licences --------------------
        # Stored in their own tables; what the device reported also becomes
        # sensor readings below, so firewall alerts open and auto-resolve
        # through the same sensor path as every other threshold.
        firewall_readings: list = []
        if bundle.firewall_status:
            from app.services.monitoring.firewall_saver import save_firewall

            fw_result = await save_firewall(
                self.device_repo.db,
                tenant_id=source.tenant_id,
                source_id=source.id,
                bundle=bundle,
                device_id_by_external=device_id_by_external,
                now=collected_at,
            )
            firewall_readings = fw_result.readings
            result.firewalls_updated = fw_result.firewalls_updated
            result.vpn_tunnels_upserted = fw_result.tunnels_upserted
            result.vpn_tunnels_retired = fw_result.tunnels_retired
            result.firewall_status = dict(bundle.firewall_status)
        firewall_reading_ids = {r.external_id for r in firewall_readings}

        # -- 2c. Backup vaults, protected items and jobs ----------------------
        # Same shape as the firewall tables: own tables, and what the provider
        # reported becomes sensor readings so backup alerts open and resolve
        # through the sensor path.
        backup_readings: list = []
        if bundle.backup_status:
            from app.services.monitoring.backup_saver import save_backup

            bk_result = await save_backup(
                self.device_repo.db,
                tenant_id=source.tenant_id,
                source_id=source.id,
                bundle=bundle,
                device_id_by_external=device_id_by_external,
                now=collected_at,
            )
            backup_readings = bk_result.readings
            result.backup_vaults_updated = bk_result.vaults_updated
            result.backup_items_upserted = bk_result.items_upserted
            result.backup_items_retired = bk_result.items_retired
            result.backup_jobs_upserted = bk_result.jobs_upserted
            result.backup_status = dict(bundle.backup_status)
        # Firewall and backup readings always name their device (the firewall /
        # the vault), so their alerts are linked to it below.
        device_linked_reading_ids = firewall_reading_ids | {r.external_id for r in backup_readings}

        # -- 3. Sensors ------------------------------------------------------
        # external_id -> Sensor.id, for metric sensor_id resolution in step 4.
        sensor_id_by_external: dict[str, object] = {}

        for reading in [*bundle.sensors, *firewall_readings, *backup_readings]:
            interface_id = None
            if reading.device_external_id is not None and reading.interface_external_id is not None:
                interface_id = interface_id_by_key.get(
                    (reading.device_external_id, reading.interface_external_id)
                )

            # Capture the pre-upsert status (as a plain string, not the ORM
            # object reference - upsert() mutates the same identity-mapped
            # object in place, so holding onto the object itself would not
            # give us a "before" snapshot) to determine whether this sensor
            # "changed" this save.
            prior = await self.sensor_repo.get_by_external_id(source.id, reading.external_id)
            prior_status = prior.status if prior is not None else None

            if prior is None and sensor_allowance is not None and sensor_allowance <= 0:
                result.sensors_blocked += 1
                logger.warning(
                    "Plan limit: skipping NEW sensor external_id=%r for tenant %s "
                    "(no active subscription or plan sensor_limit reached)",
                    reading.external_id, source.tenant_id,
                )
                continue

            # KNOWN GAP #2: SensorRepository.upsert() has no device_id
            # parameter, so reading.device_external_id cannot be persisted
            # onto Sensor.device_id through this call even when it resolves
            # cleanly against device_id_by_external.
            sensor, created = await self.sensor_repo.upsert(
                tenant_id=source.tenant_id,
                monitoring_source_id=source.id,
                external_id=reading.external_id,
                name=reading.name,
                metric_type=reading.metric_type,
                unit=reading.unit,
                current_value=reading.value,
                status=reading.status,
                warning_threshold=reading.warning_threshold,
                critical_threshold=reading.critical_threshold,
                interface_id=interface_id,
            )
            sensor_id_by_external[reading.external_id] = sensor.id
            # Firewall readings always name their device, so their alerts
            # carry it for correlation and health (KNOWN GAP #2 is about the
            # repository signature; the link is set on the row directly).
            if reading.external_id in device_linked_reading_ids:
                fw_device_id = device_id_by_external.get(reading.device_external_id)
                if fw_device_id is not None and sensor.device_id != fw_device_id:
                    sensor.device_id = fw_device_id

            if created:
                result.sensors_created += 1
                result.changed_sensors.append(sensor)
            else:
                result.sensors_updated += 1
                if prior_status != sensor.status:
                    result.changed_sensors.append(sensor)

        # -- 4. Metrics (append-only) -----------------------------------------
        for m in bundle.metrics:
            device_id = (
                device_id_by_external.get(m.device_external_id)
                if m.device_external_id is not None
                else None
            )
            sensor_id = (
                sensor_id_by_external.get(m.sensor_external_id)
                if m.sensor_external_id is not None
                else None
            )
            await self.metric_repo.record(
                tenant_id=source.tenant_id,
                metric_type=m.metric_type,
                value=m.value,
                recorded_at=m.recorded_at,
                monitoring_source_id=source.id,
                device_id=device_id,
                sensor_id=sensor_id,
                unit=m.unit,
                # Canonical telemetry (universal infrastructure intelligence):
                # the SAME row also carries its capability, its canonical metric
                # name and its provider. No second series is created, and
                # `metric_type` keeps the provider's own spelling.
                metric_name=canonical_telemetry.canonical_metric_name(m.metric_type),
                capability=capability_of_metric(m.metric_type) or None,
                provider=provider_token,
                dimensions={"provider_metric_type": m.metric_type},
            )
            result.metrics_recorded += 1

        # -- 5. Events -----------------------------------------------------
        for e in bundle.events:
            device_id = (
                device_id_by_external.get(e.device_external_id)
                if e.device_external_id is not None
                else None
            )
            await self.event_repo.upsert(
                tenant_id=source.tenant_id,
                event_type=e.event_type,
                message=e.message,
                occurred_at=e.occurred_at,
                monitoring_source_id=source.id,
                device_id=device_id,
                external_id=e.external_id,
                severity=e.severity,
                raw_payload=e.raw_payload,
            )
            result.events_recorded += 1

        # -- 6. Vendor-native alert signals ------------------------------
        # Handed back to the caller (CollectionOrchestrator), not written
        # here - Alert writes stay inside AlertService/AlertRepository.
        # device_id_by_signal_device_external_id is exposed alongside so
        # the caller can resolve each signal's device_id without needing
        # its own copy of this bundle-scoped external-id map (Alert
        # Correlation Engine sprint).
        result.vendor_alert_signals = bundle.alerts
        result.device_id_by_signal_device_external_id = {
            signal.device_external_id: device_id_by_external[signal.device_external_id]
            for signal in bundle.alerts
            if signal.device_external_id is not None
            and signal.device_external_id in device_id_by_external
        }

        # -- 7. Observed adjacencies (Phase 3A: LLDP/CDP) --------------------
        # The collector NEVER writes edges itself. Adjacencies observed by a
        # protocol are handed to DiscoveryService, which owns the single
        # evidence-backed decision about whether an edge exists and writes it
        # through ResourceRelationshipRepository.
        result.neighbor_status = dict(bundle.neighbor_status)
        if bundle.neighbors:
            from app.services.discovery.evidence import NeighborEvidence
            from app.services.discovery.service import DiscoveryService

            # A bundle may describe MANY local devices: an SNMP poll reports one
            # endpoint, while a lab/topology controller (PNETLab) reports a whole
            # topology at once. Neighbours are therefore grouped by the local
            # device they were observed from, and each group goes through the same
            # single writer - so both provider shapes share one code path.
            by_local: dict[str, list] = {}
            for nb in bundle.neighbors:
                by_local.setdefault(nb.device_external_id, []).append(nb)

            discovery = DiscoveryService(self.device_repo.db)
            for local_external_id, group in by_local.items():
                source_device_id = device_id_by_external.get(local_external_id)
                if source_device_id is None:
                    # The local device did not persist (e.g. blocked by a plan
                    # limit), so there is no endpoint to hang an edge on.
                    result.neighbors_unresolved += len(group)
                    continue
                evidence: list[NeighborEvidence] = []
                for nb in group:
                    if not nb.remote_address and not nb.remote_external_id:
                        # Real adjacency, but the source exposed NEITHER a
                        # management address NOR an identity of its own, so it
                        # cannot be matched to a Device. No edge - and no
                        # fabricated device either.
                        result.neighbors_unresolved += 1
                        logger.info(
                            "neighbour %s on %s has no management address or source "
                            "identity (evidence=%s) - adjacency recorded, no edge written",
                            nb.remote_identity or "?", nb.local_interface, nb.evidence_type,
                        )
                        continue
                    evidence.append(NeighborEvidence(
                        # An empty address is honest: the edge is then resolved by
                        # the source identity below, never by a name guess.
                        address=nb.remote_address or "",
                        evidence_type=nb.evidence_type,
                        local_interface=nb.local_interface,
                        remote_interface=nb.remote_interface,
                        # Physical adjacency uses the existing vocabulary; the
                        # protocol is evidence, not the relationship type.
                        relationship_type="connected_to",
                        remote_external_id=nb.remote_external_id,
                    ))

                if evidence:
                    written, unresolved = await discovery.write_observed_neighbors(
                        tenant_id=source.tenant_id,
                        monitoring_source_id=source.id,
                        source_device_id=source_device_id,
                        neighbors=evidence,
                    )
                    result.neighbors_written += written
                    result.neighbors_unresolved += unresolved

        # -- 7b. L3 addressing and routes ------------------------------------
        await self._save_l3(
            source,
            bundle,
            result,
            provider=provider_token,
            device_id_by_external=device_id_by_external,
            interface_id_by_key=interface_id_by_key,
            interface_id_by_name=interface_id_by_name,
        )

        # -- 8. Universal infrastructure layer (provider-neutral) -------------
        # Feeds the canonical telemetry/capability model from the SAME bundle:
        #   * one availability observation per device, from its own reported
        #     status (the only writer of that series),
        #   * capability assertions for the resource (capabilities this run
        #     actually collected, and the ones this provider provably cannot
        #     supply, each with an explicit reason),
        #   * the resource facts the universal layer owns (provider, last
        #     telemetry timestamp, discovered -> active lifecycle promotion).
        # Numeric readings are NOT re-written: step 4 already persisted them WITH
        # their canonical columns, so `include_metrics=False` prevents duplicates.
        from app.repositories.resource_capability_repository import (
            ResourceCapabilityRepository,
        )

        canonical = normalize_bundle(
            bundle,
            tenant_id=source.tenant_id,
            provider=provider_token,
            device_id_by_external=device_id_by_external,
            interface_id_by_key=interface_id_by_key,
            site_id_by_device=site_id_by_device,
        )
        canonical_result = await CanonicalTelemetrySaver(
            metric_repo=self.metric_repo,
            capability_repo=ResourceCapabilityRepository(self.metric_repo.db),
            device_repo=self.device_repo,
        ).save(
            canonical,
            device_type_by_id={
                device_id_by_external[nd.external_id]: nd.device_type
                for nd in bundle.devices
                if nd.external_id in device_id_by_external
            },
            include_metrics=False,
        )
        result.canonical = canonical.to_dict()
        result.availability_recorded = canonical_result["availability_written"]
        result.capabilities_asserted = canonical_result["capabilities_asserted"]
        result.resources_enriched = canonical_result["resources_enriched"]

        return result
