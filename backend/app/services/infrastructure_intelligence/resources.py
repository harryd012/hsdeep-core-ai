"""
Universal Resource model (spec sections 4, 6, 24).

The canonical, provider-neutral view of ANY infrastructure object.

Implementation note - why this is a projection, not a table
----------------------------------------------------------
HSDEEP already has exactly one persisted resource identity: ``devices``. That
row is referenced by interfaces, metrics/sensors, alerts, incidents, topology
links, resource relationships and device actions. A parallel
``universal_resources`` table would have duplicated that graph, and the spec
forbids duplicate device models.

So the universal resource is a PROJECTION of the canonical Device row plus the
identity facts the provider/collector actually observed. What makes it
"universal" is that it is expressed in provider-neutral vocabulary (see
``vocabulary.py``) and carries capability state - not that it is stored twice.

Hierarchy (spec sections 11, 24) uses the additive
``Device.parent_resource_id`` column, so
    tenant -> cloud_subscription -> resource_group -> cloud_resource
and
    tenant -> site -> firewall -> interface
are the same traversal, not two different architectures.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from app.services.infrastructure_intelligence import vocabulary as V


def _location_of(device) -> dict:
    """Physical/logical placement, provider-neutral.

    Only real columns are read; nothing is inferred from an address or a name.
    """
    location = {
        "site_id": str(device.site_id) if device.site_id else None,
        "zone_id": str(device.zone_id) if getattr(device, "zone_id", None) else None,
        "country": getattr(device, "country", None),
        "city": getattr(device, "city", None),
        "campus": getattr(device, "campus", None),
        "building": getattr(device, "building", None),
        "datacenter": getattr(device, "datacenter_name", None),
        "rack": getattr(device, "rack", None),
    }
    return {k: v for k, v in location.items() if v}


@dataclass(frozen=True)
class UniversalResource:
    """One provider-neutral infrastructure resource."""

    resource_id: uuid.UUID
    tenant_id: uuid.UUID
    resource_type: str
    name: str
    family: str
    site_id: uuid.UUID | None = None
    parent_resource_id: uuid.UUID | None = None
    resource_subtype: str | None = None
    provider: str | None = None
    provider_resource_id: str | None = None
    description: str | None = None
    location: dict = field(default_factory=dict)
    #: Canonical operational state (up | down | degraded | unknown).
    status: str = V.OPER_STATE_UNKNOWN
    #: The raw stored spelling, always returned alongside the canonical one so a
    #: consumer can never be shown a bucket without its input.
    status_raw: str = "unknown"
    #: healthy | warning | critical | unknown | maintenance
    health: str = V.HEALTH_UNKNOWN
    #: Why health is what it is - ``None`` once a health pass has run.
    health_reason: str | None = V.REASON_NOT_YET_COLLECTED
    lifecycle_state: str = V.LIFECYCLE_DISCOVERED
    discovered_at: datetime | None = None
    last_seen_at: datetime | None = None
    last_telemetry_at: datetime | None = None
    metadata: dict = field(default_factory=dict)

    @classmethod
    def from_device(cls, device, *, provider: str | None = None) -> "UniversalResource":
        """Project a stored ``Device`` row into the universal resource shape.

        ``provider`` lets the caller supply the provider token resolved from the
        owning monitoring source; when omitted the column value (or None) is
        used rather than a guess.
        """
        resource_type = V.universal_resource_type(device.device_type)
        operation = V.operational_state(device.status)
        stored_health = getattr(device, "health", None)
        metadata = dict(getattr(device, "custom_metadata", None) or {})
        extra = {
            "vendor": device.vendor,
            "model": device.model,
            "os_version": device.os_version,
            "serial_number": device.serial_number,
            "hostname": device.hostname,
            "ip_address": device.ip_address,
            "mac_address": device.mac_address,
            "criticality": getattr(device, "criticality", None),
            "business_service_id": (
                str(device.business_service_id)
                if getattr(device, "business_service_id", None) else None
            ),
            "role": getattr(device, "role", None),
            "role_class": getattr(device, "role_class", None),
            "monitoring_source_id": (
                str(device.monitoring_source_id)
                if getattr(device, "monitoring_source_id", None) else None
            ),
        }
        metadata.update({k: v for k, v in extra.items() if v})
        return cls(
            resource_id=device.id,
            tenant_id=device.tenant_id,
            site_id=device.site_id,
            parent_resource_id=getattr(device, "parent_resource_id", None),
            resource_type=resource_type,
            resource_subtype=getattr(device, "role", None),
            family=V.resource_family(resource_type),
            provider=provider or getattr(device, "provider", None),
            provider_resource_id=device.external_id,
            name=device.name,
            description=metadata.get("description"),
            location=_location_of(device),
            status=operation,
            status_raw=device.status,
            health=stored_health or V.HEALTH_UNKNOWN,
            health_reason=None if stored_health else V.REASON_NOT_YET_COLLECTED,
            lifecycle_state=(
                getattr(device, "lifecycle_state", None) or V.LIFECYCLE_DISCOVERED
            ),
            discovered_at=getattr(device, "discovered_at", None),
            last_seen_at=getattr(device, "last_seen_at", None),
            last_telemetry_at=getattr(device, "last_telemetry_at", None),
            metadata=metadata,
        )

    def to_dict(self) -> dict:
        return {
            "resource_id": str(self.resource_id),
            "tenant_id": str(self.tenant_id),
            "site_id": str(self.site_id) if self.site_id else None,
            "parent_resource_id": (
                str(self.parent_resource_id) if self.parent_resource_id else None
            ),
            "resource_type": self.resource_type,
            "resource_subtype": self.resource_subtype,
            "family": self.family,
            "provider": self.provider,
            "provider_resource_id": self.provider_resource_id,
            "name": self.name,
            "description": self.description,
            "location": dict(self.location),
            "status": self.status,
            "status_raw": self.status_raw,
            "health": self.health,
            "health_reason": self.health_reason,
            "lifecycle_state": self.lifecycle_state,
            "discovered_at": (
                self.discovered_at.isoformat() if self.discovered_at else None
            ),
            "last_seen_at": (
                self.last_seen_at.isoformat() if self.last_seen_at else None
            ),
            "last_telemetry_at": (
                self.last_telemetry_at.isoformat() if self.last_telemetry_at else None
            ),
            "metadata": dict(self.metadata),
        }


def resource_type_catalog() -> list[dict]:
    """The universal resource-type catalog, grouped by family.

    Powers the API's catalog endpoint and the UI's type filter. Counts come from
    the real vocabulary tables, so the catalog can never advertise a type the
    resolver does not know.
    """
    from app.services.infrastructure_intelligence.capabilities import (
        RESOURCE_TYPE_CAPABILITIES,
        type_exposes,
    )

    by_family: dict[str, list[dict]] = {}
    for resource_type in V.UNIVERSAL_RESOURCE_TYPES:
        family = V.resource_family(resource_type)
        by_family.setdefault(family, []).append({
            "resource_type": resource_type,
            "family": family,
            "capability_count": len(type_exposes(resource_type)),
            "declared_profile": resource_type in RESOURCE_TYPE_CAPABILITIES,
        })
    return [
        {
            "family": family,
            "resource_types": sorted(entries, key=lambda e: e["resource_type"]),
        }
        for family, entries in sorted(by_family.items())
        if entries
    ]
