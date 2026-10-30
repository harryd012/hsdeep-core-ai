"""Topology coverage: devices with a recorded link, counted per tenant."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models.device import Device
from app.models.resource_relationship import ResourceRelationship
from app.repositories.resource_relationship_repository import ResourceRelationshipRepository

pytestmark = pytest.mark.asyncio
NOW = datetime.now(timezone.utc)


async def test_linked_devices_counts_each_endpoint_once_and_ignores_deleted(db, tenant):
    a, b, c, lone = (Device(tenant_id=tenant.id, name=n, device_type="switch") for n in ("a", "b", "c", "lone"))
    db.add_all([a, b, c, lone])
    await db.flush()

    def rel(src, dst, **kw):
        return ResourceRelationship(tenant_id=tenant.id, source_resource_id=src.id, target_resource_id=dst.id,
                                    relationship_type="connected_to", first_seen=NOW, last_seen=NOW, **kw)

    db.add_all([rel(a, b), rel(b, a), rel(c, lone, is_deleted=True)])
    await db.commit()  # committed so the tenant fixture's teardown can cascade
    assert await ResourceRelationshipRepository(db).count_linked_devices(tenant.id) == 2

    c.is_deleted = True
    db.add(rel(a, c))
    await db.commit()
    # A deleted device is not counted even when a live link still names it.
    assert await ResourceRelationshipRepository(db).count_linked_devices(tenant.id) == 2
