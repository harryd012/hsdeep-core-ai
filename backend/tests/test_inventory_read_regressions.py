"""Regression coverage without application data writes."""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.routes.infrastructure import get_topology_service
from app.models.device import Device
from app.schemas.asset import DeviceCMDBOut
from app.services import topology_service as module


def test_route_constructs_service():
    db = object()
    service = get_topology_service(db)
    assert service.db is db
    assert callable(service.tenant_topology)


def test_cmdb_fields_are_mapped_and_serialize():
    device = Device(id=uuid.uuid4(), tenant_id=uuid.uuid4(), name="CMDB test",
                    device_type="switch", status="unknown", tags=["inventory"],
                    custom_metadata={"source": "cmdb"}, environment="test")
    result = DeviceCMDBOut.model_validate(device)
    assert result.tags == ["inventory"]
    assert result.environment == "test"
    assert result.warranty_start is None
    for name, length in {"environment": 20, "business_owner": 255,
                         "support_team": 255, "maintenance_window": 255}.items():
        column = Device.__table__.c[name]
        assert column.type.length == length and column.nullable
    assert Device.__table__.c.warranty_start.type.timezone
    assert Device.__table__.c.warranty_end.type.timezone
    assert Device.__table__.c.tags.nullable
    assert str(Device.__table__.c.tags.server_default.arg) == "[]"


@pytest.mark.asyncio
async def test_tree_uses_scoped_repositories_and_all_devices(monkeypatch):
    tenant = uuid.uuid4()
    site = SimpleNamespace(id=uuid.uuid4(), name="Site", code="S", address=None, region=None)
    source = SimpleNamespace(id=uuid.uuid4(), site_id=site.id, name="Source",
                             source_type="prtg", status="connected")
    sensor = SimpleNamespace(id=uuid.uuid4(), monitoring_source_id=source.id, name="CPU")
    devices = [SimpleNamespace(id=uuid.uuid4(), site_id=site.id, name=f"D{i}") for i in range(125)]
    devices.append(SimpleNamespace(id=uuid.uuid4(), site_id=None, name="Unassigned"))
    sites = AsyncMock(return_value=[site])
    devs = AsyncMock(return_value=devices)
    sources = AsyncMock(return_value=[source])
    sensors = AsyncMock(return_value=[sensor])
    monkeypatch.setattr(module.SiteRepository, "list", sites)
    monkeypatch.setattr(module.DeviceRepository, "list", devs)
    monkeypatch.setattr(module.MonitoringSourceRepository, "list", sources)
    monkeypatch.setattr(module.SensorRepository, "list_for_tenant", sensors)
    result = await get_topology_service(object()).tenant_topology(tenant)
    assert result.tenant_id == tenant
    assert result.generated_at.tzinfo is not None
    assert len(result.sites) == 1
    assert len(result.sites[0].devices) == 125
    assert result.sites[0].monitoring_sources[0].sensors[0].id == sensor.id
    sites.assert_awaited_once_with(tenant)
    devs.assert_awaited_once_with(tenant, limit=None)
    sources.assert_awaited_once_with(tenant)
    sensors.assert_awaited_once_with(tenant)


@pytest.mark.asyncio
async def test_empty_tree_does_not_invent_sites(monkeypatch):
    monkeypatch.setattr(module.SiteRepository, "list", AsyncMock(return_value=[]))
    devices = AsyncMock()
    monkeypatch.setattr(module.DeviceRepository, "list", devices)
    result = await get_topology_service(object()).tenant_topology(uuid.uuid4())
    assert result.sites == []
    devices.assert_not_awaited()
