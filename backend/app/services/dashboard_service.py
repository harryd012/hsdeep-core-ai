"""
Dashboard service layer.

Aggregates counts/status across Sites, Devices, MonitoringSources, and
Sensors for the Dashboard API's summary/cloud/network cards. All aggregation
is backed exclusively by existing repository COUNT/list queries (per Clean
Architecture, same convention as alert_service.py / monitoring_service.py) -
this service never issues its own SQLAlchemy queries.

Documented deviations from the illustrative schema comments, both forced by
the repository layer being locked to its current methods (no new repository
classes/methods added for this task):

1. DeviceRepository has no dedicated COUNT method (unlike SiteRepository's
   `count()`). summary(), cloud_status(), and network_status() therefore
   fetch the full Device list with a high `limit` (100_000) and use len()
   instead of a real COUNT query. This is a known limitation - a tenant with
   more than 100_000 devices would have its device-derived counts silently
   truncated - rather than a deliberate design choice. Adding a
   `DeviceRepository.count()` method would remove this limitation but that
   repository is locked for this task.

2. CloudVendorStatus.status is derived from the vendor's own Device.status
   values (down -> "error", warning -> "degraded", else "operational")
   rather than from that vendor's MonitoringSource.status as the schema
   field comment describes. There is no repository-level join path from a
   Device.vendor string back to a specific MonitoringSource without adding
   a new repository method, so deriving status from the devices themselves
   is the most direct, real (non-fabricated) signal available through the
   existing repository surface.
"""
from __future__ import annotations

import uuid

from app.models.device import Device
from app.repositories.device_repository import DeviceRepository
from app.repositories.monitoring_source_repository import MonitoringSourceRepository
from app.repositories.sensor_repository import SensorRepository
from app.repositories.site_repository import SiteRepository
from app.schemas.dashboard import CloudStatus, CloudVendorStatus, DashboardSummary, NetworkStatus

# Device fetches use a high limit rather than the repository's default of
# 100, to avoid silently truncating tenant-wide aggregates (see module
# docstring deviation #1 - there is no COUNT method to use instead).
_DEVICE_FETCH_LIMIT = 100_000


class DashboardService:
    def __init__(
        self,
        site_repo: SiteRepository,
        device_repo: DeviceRepository,
        source_repo: MonitoringSourceRepository,
        sensor_repo: SensorRepository,
    ):
        self.site_repo = site_repo
        self.device_repo = device_repo
        self.source_repo = source_repo
        self.sensor_repo = sensor_repo

    async def summary(self, tenant_id: uuid.UUID) -> DashboardSummary:
        """Five headline counts for the dashboard's summary cards. Each
        defaults to 0 for a zero-row tenant naturally - count() returns 0
        and len() of an empty list is 0 - no special-casing required."""
        sites = await self.site_repo.count(tenant_id)
        devices = await self.device_repo.list(tenant_id, limit=_DEVICE_FETCH_LIMIT)
        monitoring_sources = await self.source_repo.list(tenant_id)
        sensors = await self.sensor_repo.list_for_tenant(tenant_id)
        cloud_resources = [d for d in devices if d.device_type == "cloud_resource"]

        return DashboardSummary(
            sites=sites,
            devices=len(devices),
            monitoring_sources=len(monitoring_sources),
            sensors=len(sensors),
            cloud_resources=len(cloud_resources),
        )

    async def cloud_status(self, tenant_id: uuid.UUID) -> CloudStatus:
        """Groups cloud-resource Devices by vendor and derives each group's
        status from those devices' own `status` field (see module docstring
        deviation #2 for why this isn't MonitoringSource.status)."""
        devices = await self.device_repo.list(
            tenant_id, device_type="cloud_resource", limit=_DEVICE_FETCH_LIMIT
        )

        by_vendor: dict[str, list[Device]] = {}
        for device in devices:
            by_vendor.setdefault(device.vendor or "unknown", []).append(device)

        vendors = [
            CloudVendorStatus(
                vendor=vendor,
                resource_count=len(group),
                status=self._derive_vendor_status(group),
            )
            for vendor, group in by_vendor.items()
        ]

        return CloudStatus(vendors=vendors, total_resources=len(devices))

    @staticmethod
    def _derive_vendor_status(devices: list[Device]) -> str:
        if any(d.status == "down" for d in devices):
            return "error"
        if any(d.status == "warning" for d in devices):
            return "degraded"
        return "operational"

    async def network_status(self, tenant_id: uuid.UUID) -> NetworkStatus:
        """A site counts as "online" when it has at least one device with
        status='up' and zero devices with status='down' (per the schema's
        own field comment). A site with no devices at all is neither
        online nor counted as down - it's simply excluded from
        sites_online, which defaults sites_online to 0 for a zero-device
        tenant without any special-casing."""
        sites = await self.site_repo.list(tenant_id)
        devices = await self.device_repo.list(tenant_id, limit=_DEVICE_FETCH_LIMIT)

        devices_by_site: dict[uuid.UUID, list[Device]] = {}
        for device in devices:
            if device.site_id is not None:
                devices_by_site.setdefault(device.site_id, []).append(device)

        sites_online = 0
        for site in sites:
            site_devices = devices_by_site.get(site.id, [])
            has_up = any(d.status == "up" for d in site_devices)
            has_down = any(d.status == "down" for d in site_devices)
            if has_up and not has_down:
                sites_online += 1

        devices_up = sum(1 for d in devices if d.status == "up")
        devices_down = sum(1 for d in devices if d.status == "down")

        return NetworkStatus(
            sites_total=len(sites),
            sites_online=sites_online,
            devices_total=len(devices),
            devices_up=devices_up,
            devices_down=devices_down,
        )
