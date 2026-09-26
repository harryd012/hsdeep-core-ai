"""
Repository for DeviceCapability (1:1 with devices) + the capability
resolver logic that derives honest defaults from a device's linked
monitoring source.

Derivation rules (READ-ONLY transports only - monitoring credentials
never imply configuration privileges):
  * supports_ping  <- device has an IP/hostname (ICMP needs only an address)
  * supports_snmp  <- linked monitoring source protocol == 'snmp'
  * supports_rest  <- linked source protocol == 'https' and vendor API type
  * supports_config / supports_reboot / supports_firmware / supports_rollback
    are NEVER derived - only an explicit operator/collector assertion
    (source='explicit') can enable state-changing capabilities.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Device
from app.models.device_action import DeviceCapability
from app.models.monitoring_source import MonitoringSource

# Capability flags exposed by the resolver (matches DeviceCapability).
CAPABILITY_FLAGS = (
    "supports_ping", "supports_snmp", "supports_ssh", "supports_rest",
    "supports_config", "supports_reboot", "supports_firmware",
    "supports_backup", "supports_rollback", "supports_monitoring_actions",
)


def derive_capabilities(device: Device, source: MonitoringSource | None) -> dict[str, bool | None]:
    """Pure derivation - unit-testable, no I/O. Explicit rows overlay
    these defaults in the service layer (explicit always wins).

    READ-ONLY transports only - monitoring credentials never imply
    configuration privileges. `supports_monitoring_actions` is derived
    ONLY for an active PRTG source (a live monitoring-plane control
    surface); it still says nothing about device config/reboot rights."""
    caps: dict[str, bool | None] = {flag: None for flag in CAPABILITY_FLAGS}
    # ICMP only needs an address.
    if device.ip_address or device.hostname:
        caps["supports_ping"] = True
    if source is not None and source.is_active:
        protocol = (source.protocol or "").lower()
        if protocol == "snmp":
            caps["supports_snmp"] = True
        elif protocol in ("https", "http", "rest"):
            caps["supports_rest"] = True
        if (source.source_type or "").lower() == "prtg":
            caps["supports_monitoring_actions"] = True
    return caps


class DeviceCapabilityRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_for_device(self, device_id: uuid.UUID, tenant_id: uuid.UUID) -> DeviceCapability | None:
        stmt = select(DeviceCapability).where(
            DeviceCapability.device_id == device_id,
            DeviceCapability.tenant_id == tenant_id,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert(
        self,
        *,
        tenant_id: uuid.UUID,
        device_id: uuid.UUID,
        source: str,
        capabilities: dict[str, bool | None],
        firmware_version: str | None = None,
        management_address: str | None = None,
    ) -> DeviceCapability:
        row = await self.get_for_device(device_id, tenant_id)
        if row is None:
            row = DeviceCapability(
                tenant_id=tenant_id, device_id=device_id, source=source
            )
            self.db.add(row)
        row.source = source
        for flag in CAPABILITY_FLAGS:
            if flag in capabilities:
                setattr(row, flag, capabilities[flag])
        if firmware_version is not None:
            row.firmware_version = firmware_version
        if management_address is not None:
            row.management_address = management_address
        await self.db.flush()
        return row
