"""Tests for Zone CRUD operations."""
from __future__ import annotations

import uuid

import pytest

from app.core.db import AsyncSessionLocal
from app.models.site import Site
from app.models.tenant import Tenant
from app.models.zone import Zone
from app.repositories.zone_repository import ZoneRepository
from app.services.zone_service import ZoneNotFound, ZoneService

pytestmark = pytest.mark.asyncio


async def _make_site(db, tenant_id: uuid.UUID) -> Site:
    site = Site(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name=f"DC-{uuid.uuid4().hex[:6].upper()}",
        code=f"dc{uuid.uuid4().hex[:4]}",
        region="us-east-1",
    )
    db.add(site)
    await db.commit()
    await db.refresh(site)
    return site


async def test_create_zone(tenant: Tenant, db: AsyncSessionLocal):
    site = await _make_site(db, tenant.id)
    service = ZoneService(ZoneRepository(db))
    zone = await service.create(
        tenant_id=tenant.id,
        site_id=site.id,
        name="Production Zone",
        code="prod",
        description="Production network zone",
        zone_type="network",
    )
    assert zone.name == "Production Zone"
    assert zone.code == "prod"
    assert zone.zone_type == "network"
    assert zone.tenant_id == tenant.id
    assert zone.site_id == site.id


async def test_get_zone_not_found(tenant: Tenant, db: AsyncSessionLocal):
    service = ZoneService(ZoneRepository(db))
    with pytest.raises(ZoneNotFound):
        await service.get(uuid.uuid4(), tenant.id)


async def test_zone_tenant_isolation(tenant: Tenant, db: AsyncSessionLocal):
    site = await _make_site(db, tenant.id)
    service = ZoneService(ZoneRepository(db))
    zone = await service.create(
        tenant_id=tenant.id,
        site_id=site.id,
        name="Test Zone",
        code="test",
        zone_type="network",
    )
    other_tenant_id = uuid.uuid4()
    # Cross-tenant read must be blocked
    assert await service.repo.get(zone.id, other_tenant_id) is None
    with pytest.raises(ZoneNotFound):
        await service.get(zone.id, other_tenant_id)


async def test_update_zone(tenant: Tenant, db: AsyncSessionLocal):
    site = await _make_site(db, tenant.id)
    service = ZoneService(ZoneRepository(db))
    zone = await service.create(
        tenant_id=tenant.id,
        site_id=site.id,
        name="Original",
        code="orig",
        zone_type="network",
    )
    updated = await service.update(zone.id, tenant.id, name="Updated", description="new desc")
    assert updated.name == "Updated"
    assert updated.description == "new desc"


async def test_delete_zone(tenant: Tenant, db: AsyncSessionLocal):
    site = await _make_site(db, tenant.id)
    service = ZoneService(ZoneRepository(db))
    zone = await service.create(
        tenant_id=tenant.id,
        site_id=site.id,
        name="To Delete",
        code="del",
        zone_type="network",
    )
    await service.delete(zone.id, tenant.id)
    with pytest.raises(ZoneNotFound):
        await service.get(zone.id, tenant.id)
