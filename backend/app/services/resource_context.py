"""Normalized resource context (identity, capabilities, relationships, discovery).

ONE builder serves two consumers:

  * the Device/Resource detail API + UI ("device details" sections), and
  * the existing AI/RCA/recommendation engines, which need normalized
    infrastructure context rather than raw vendor payloads.

It introduces no new model and no second AI subsystem — it reads the EXISTING
Device, DeviceCapability and ResourceRelationship records plus the discovery
provenance already stored in ``Device.custom_metadata['discovery']``.

Secrets are never included: discovery provenance holds only normalized evidence
fields, and this module copies a fixed allow-list.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.device_capability_repository import (
    CAPABILITY_FLAGS,
    DeviceCapabilityRepository,
)
from app.repositories.device_repository import DeviceRepository
from app.repositories.resource_relationship_repository import (
    ResourceRelationshipRepository,
)

#: Provenance keys that are safe to expose (never credentials).
_SAFE_DISCOVERY_KEYS = (
    "source", "transport", "provider", "resource_category",
    "provider_capabilities", "identity_source", "identity_confidence",
    "identity_keys", "recognition", "observed_at",
)

#: Capability flags that imply write privileges. Surfaced separately so a UI or
#: agent never offers an action the platform has not actually been granted.
WRITE_CAPABILITY_FLAGS = (
    "supports_config", "supports_reboot", "supports_firmware",
    "supports_backup", "supports_rollback", "supports_monitoring_actions",
)


async def build_resource_context(
    db: AsyncSession, tenant_id: uuid.UUID, device_id: uuid.UUID
) -> dict[str, Any] | None:
    """Full normalized context for one resource, or None when not in this tenant.

    Tenant-scoped at every read: a device outside ``tenant_id`` is reported as
    absent rather than partially disclosed.
    """
    device = await DeviceRepository(db).get(device_id, tenant_id)
    if device is None:
        return None

    capability_row = await DeviceCapabilityRepository(db).get_for_device(
        device_id, tenant_id
    )
    capabilities: dict[str, bool | None] = {flag: None for flag in CAPABILITY_FLAGS}
    if capability_row is not None:
        for flag in CAPABILITY_FLAGS:
            capabilities[flag] = getattr(capability_row, flag, None)

    relationships = ResourceRelationshipRepository(db)
    outbound = await relationships.list_edges_from(device_id, tenant_id)
    inbound = await relationships.list_edges_to(device_id, tenant_id)

    metadata = dict(device.custom_metadata or {})
    discovery = {
        k: v for k, v in (metadata.get("discovery") or {}).items()
        if k in _SAFE_DISCOVERY_KEYS
    }

    read_only = {
        f: v for f, v in capabilities.items()
        if f not in WRITE_CAPABILITY_FLAGS and v
    }
    write_capable = {
        f: v for f, v in capabilities.items()
        if f in WRITE_CAPABILITY_FLAGS and v
    }

    return {
        "resource": _resource_block(device, discovery),
        "capabilities": capabilities,
        "read_capabilities": read_only,
        "write_capabilities": write_capable,
        "discovery": discovery,
        "relationships": {
            "depends_on": [_edge(rel, name, "outbound") for rel, name in outbound],
            "depended_on_by": [_edge(rel, name, "inbound") for rel, name in inbound],
        },
        # The exact shape an AI/RCA prompt can consume without further joins.
        "ai_context": {
            "tenant_id": str(device.tenant_id),
            "resource_id": str(device.id),
            "resource_type": device.device_type,
            "resource_category": discovery.get("resource_category"),
            "vendor": device.vendor,
            "provider": discovery.get("provider"),
            "site_id": str(device.site_id) if device.site_id else None,
            "collector_id": (
                str(device.monitoring_source_id)
                if device.monitoring_source_id else None
            ),
            "status": device.status,
            "capabilities": sorted(k for k, v in read_only.items() if v),
            "neighbours": [
                {"name": name, "type": rel.relationship_type, "direction": "outbound"}
                for rel, name in outbound
            ] + [
                {"name": name, "type": rel.relationship_type, "direction": "inbound"}
                for rel, name in inbound
            ],
            "recognition": discovery.get("recognition"),
        },
    }


def _resource_block(device: Any, discovery: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(device.id),
        "tenant_id": str(device.tenant_id),
        "name": device.name,
        "hostname": device.hostname,
        "management_ip": device.ip_address,
        "resource_type": device.device_type,
        "resource_category": discovery.get("resource_category"),
        "vendor": device.vendor,
        "model": device.model,
        "version": device.os_version,
        "serial_number": device.serial_number,
        "mac_address": device.mac_address,
        "external_id": device.external_id,
        "provider": discovery.get("provider"),
        "site_id": str(device.site_id) if device.site_id else None,
        "criticality": device.criticality,
        "status": device.status,
        "last_seen_at": (
            device.last_seen_at.isoformat() if device.last_seen_at else None
        ),
    }


def _edge(rel: Any, neighbour_name: str | None, direction: str) -> dict[str, Any]:
    return {
        "relationship_type": rel.relationship_type,
        "relationship_subtype": rel.relationship_subtype,
        "label": rel.label,
        "confidence": rel.confidence,
        "discovered_by": rel.discovered_by,
        "is_inferred": rel.is_inferred,
        "status": rel.status,
        "direction": direction,
        "neighbour_name": neighbour_name,
        "first_seen": rel.first_seen.isoformat() if rel.first_seen else None,
        "last_seen": rel.last_seen.isoformat() if rel.last_seen else None,
        "attributes": dict(rel.attributes or {}),
    }
