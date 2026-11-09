"""Infrastructure health overview: device health per resource family and cloud
health per provider and service category, on the canonical overview
(`InfrastructureOverviewService`, `devices.by_category` / `devices.cloud`).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import httpx

from app.collectors.azure import AzureCollector
from app.core.auth import create_access_token
from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.device import Device
from app.services.infrastructure_overview_service import category_rollup, cloud_provider_of


def _dev(device_type, status, vendor=None, meta=None):
    return SimpleNamespace(device_type=device_type, status=status, vendor=vendor, custom_metadata=meta)


def test_category_rollup_buckets_health_per_family_and_cloud():
    devices = [
        _dev("server", "up"), _dev("server", "down"), _dev("server", "online"),
        _dev("switch", "warning"), _dev("router", "up"),
        _dev("firewall", "up"),
        _dev("unknown", None), _dev("printer-ish", "up"),  # types nobody mapped stay unknown
        _dev("cloud_resource", "up", "Amazon", {"provider": "aws", "resource_kind": "ec2_instance"}),
        _dev("cloud_resource", "down", "Amazon", {"provider": "aws", "resource_kind": "ec2_instance"}),
        _dev("cloud_resource", "up", "Amazon", {"provider": "aws", "resource_kind": "security_group"}),
        _dev("cloud_resource", "up", "Microsoft", None),  # older Azure row: provider from the vendor
        _dev("cloud_resource", "up", None, {"provider": "gcp", "resource_kind": "something_new"}),
    ]
    by_category, cloud = category_rollup(devices)
    assert by_category["compute"] == {"total": 3, "healthy": 2, "warning": 0, "critical": 1, "unknown": 0}
    assert by_category["network"] == {"total": 2, "healthy": 1, "warning": 1, "critical": 0, "unknown": 0}
    assert by_category["security"]["total"] == 1
    assert by_category["unknown"] == {"total": 2, "healthy": 1, "warning": 0, "critical": 0, "unknown": 1}
    assert by_category["cloud"]["total"] == 5
    assert list(by_category)[:2] == ["network", "security"]  # the vocabulary's family order

    aws = cloud["providers"]["aws"]
    assert (aws["total"], aws["critical"]) == (3, 1)
    assert aws["services"]["virtual_machines"] == {"total": 2, "healthy": 1, "warning": 0, "critical": 1, "unknown": 0}
    assert aws["services"]["security"]["total"] == 1
    assert cloud["providers"]["azure"]["services"] == {"other": {"total": 1, "healthy": 1, "warning": 0, "critical": 0, "unknown": 0}}
    assert list(cloud["providers"]["gcp"]["services"]) == ["other"]  # an unmapped kind is never guessed
    assert cloud_provider_of(_dev("cloud_resource", "up", "Oracle")) == "unknown"


def test_azure_vms_carry_provider_and_kind():
    bundle = AzureCollector(source=None).normalize(
        {"vms": [{"id": "/sub/vm1", "name": "vm1", "location": "eastus", "provisioning_state": "Succeeded"}]}
    )
    assert bundle.devices[0].custom_metadata == {"provider": "azure", "resource_kind": "virtual_machine", "region": "eastus"}


async def test_overview_api_exposes_categories_and_cloud_per_tenant(tenant):
    async with AsyncSessionLocal() as db:
        db.add_all([
            Device(tenant_id=tenant.id, name="srv-1", device_type="server", status="up"),
            Device(tenant_id=tenant.id, name="sw-1", device_type="switch", status="down"),
            Device(tenant_id=tenant.id, name="i-1", device_type="cloud_resource", status="up", vendor="Amazon",
                   custom_metadata={"provider": "aws", "resource_kind": "ec2_instance"}),
        ])
        await db.commit()

    token = create_access_token(user_id=uuid.UUID(int=0), tenant_id=tenant.id, email="noc@hsdeep.local",
                                is_superuser=False, permissions=frozenset({"devices.read"}))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                 headers={"Authorization": f"Bearer {token}"}) as client:
        devices = (await client.get("/api/infrastructure/overview")).json()["devices"]
    assert devices["by_category"]["compute"]["healthy"] == 1
    assert devices["by_category"]["network"]["critical"] == 1
    assert devices["cloud"]["providers"]["aws"]["services"]["virtual_machines"]["total"] == 1

    other = create_access_token(user_id=uuid.UUID(int=0), tenant_id=uuid.uuid4(), email="x@hsdeep.local",
                                is_superuser=False, permissions=frozenset({"devices.read"}))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                 headers={"Authorization": f"Bearer {other}"}) as client:
        empty = (await client.get("/api/infrastructure/overview")).json()["devices"]
    assert empty["by_category"] == {} and empty["cloud"] == {"providers": {}}
