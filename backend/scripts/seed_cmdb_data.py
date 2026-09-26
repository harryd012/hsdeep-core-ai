"""
Seed CMDB/Asset Intelligence data for local/dev environments.

Extends seed_demo_data.py with realistic asset hierarchy:
- Sites with region/building/floor/rack
- Devices with environment, criticality, tags, custom_metadata
- Asset relationships (device->sensor, device->interface, device->business_service)

Idempotent: safe to run multiple times.

Environment variables:
    SEED_TENANT_ID  UUID of the tenant to seed (required)

Usage (from backend directory with venv active):
    python scripts/seed_cmdb_data.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.core.db import AsyncSessionLocal  # noqa: E402
from app.models.asset_relationship import AssetRelationship  # noqa: E402
from app.models.device import Device  # noqa: E402
from app.models.site import Site  # noqa: E402
from app.models.business_service import BusinessService  # noqa: E402
from app.models.monitoring_source import MonitoringSource  # noqa: E402
from app.models.sensor import Sensor  # noqa: E402
from app.repositories.monitoring_source_repository import MonitoringSourceRepository  # noqa: E402
from app.repositories.sensor_repository import SensorRepository  # noqa: E402

REQUIRED_ENV_VARS = ("SEED_TENANT_ID",)


def _missing_required_env_vars() -> list[str]:
    return [v for v in REQUIRED_ENV_VARS if not os.environ.get(v)]


async def _seed_sites(db: AsyncSession, tenant_id: uuid.UUID) -> list[Site]:
    """Create demo sites if missing."""
    existing = (
        await db.execute(select(Site).where(Site.tenant_id == tenant_id, Site.is_deleted.is_(False)))
    ).scalars().all()
    if existing:
        return existing

    sites_data = [
        {"name": "Headquarters", "code": "HQ-01", "region": "us-east", "building": "Main Office", "floor": "1", "rack": "R1"},
        {"name": "Branch Office", "code": "BR-02", "region": "us-west", "building": "Branch", "floor": "2", "rack": "R2"},
        {"name": "Data Center", "code": "DC-03", "region": "eu-central", "building": "DC", "floor": "B1", "rack": "R3"},
    ]

    sites = []
    for data in sites_data:
        site = Site(
            tenant_id=tenant_id,
            name=data["name"],
            code=data["code"],
            address=f"{data['building']}, {data['region']}",
            region=data["region"],
            building=data["building"],
            floor=data["floor"],
            rack=data["rack"],
            criticality="high",
            site_owner="IT Team",
            support_contact="support@example.com",
            timezone="UTC",
        )
        db.add(site)
        sites.append(site)

    await db.flush()
    print(f"[seed_cmdb_data] Created {len(sites)} site(s)")
    return sites


async def _seed_devices(db: AsyncSession, tenant_id: uuid.UUID, sites: list[Site], sources: list[MonitoringSource]) -> list[Device]:
    """Create demo devices with CMDB fields."""
    existing = (
        await db.execute(select(Device).where(Device.tenant_id == tenant_id, Device.is_deleted.is_(False)))
    ).scalars().all()
    if existing:
        return existing

    devices = []
    source_map = {s.name: s for s in sources}
    default_source = sources[0] if sources else None

    device_templates = [
        {"name": "core-router-01", "device_type": "network", "vendor": "cisco", "model": "ISR 4000", "status": "up", "environment": "Production", "criticality": "critical"},
        {"name": "dist-switch-01", "device_type": "network", "vendor": "cisco", "model": "Catalyst 9300", "status": "up", "environment": "Production", "criticality": "high"},
        {"name": "firewall-01", "device_type": "firewall", "vendor": "paloalto", "model": "PA-5200", "status": "up", "environment": "Production", "criticality": "critical"},
        {"name": "server-web-01", "device_type": "server", "vendor": "dell", "model": "PowerEdge R740", "status": "up", "environment": "Production", "criticality": "high"},
        {"name": "server-db-01", "device_type": "database", "vendor": "oracle", "model": "Exadata", "status": "up", "environment": "Production", "criticality": "critical"},
        {"name": "server-app-01", "device_type": "server", "vendor": "hp", "model": "ProLiant DL380", "status": "warning", "environment": "UAT", "criticality": "medium"},
        {"name": "wireless-controller", "device_type": "wireless", "vendor": "cisco", "model": "CW9200", "status": "up", "environment": "Production", "criticality": "medium"},
        {"name": "azure-gateway", "device_type": "cloud_resource", "vendor": "microsoft", "model": "Azure GW", "status": "up", "environment": "Production", "criticality": "high"},
        {"name": "aws-nat-gateway", "device_type": "cloud_resource", "vendor": "aws", "model": "NAT Gateway", "status": "up", "environment": "Production", "criticality": "medium"},
        {"name": "vmware-esxi-01", "device_type": "virtualization", "vendor": "vmware", "model": "ESXi 7", "status": "up", "environment": "Production", "criticality": "high"},
    ]

    for i, template in enumerate(device_templates):
        site = sites[i % len(sites)]
        source = default_source
        device = Device(
            tenant_id=tenant_id,
            site_id=site.id,
            monitoring_source_id=source.id if source else None,
            name=template["name"],
            device_type=template["device_type"],
            vendor=template["vendor"],
            model=template["model"],
            status=template["status"],
            hostname=f"{template['name']}.example.com",
            ip_address=f"10.0.{i // 256}.{i % 256}",
            environment=template["environment"],
            criticality=template["criticality"],
            business_owner="IT Operations",
            support_team="Network Team",
            tags=[template["device_type"], template["environment"], template["vendor"]],
            custom_metadata={"rack": site.rack, "floor": site.floor, "region": site.region},
        )
        db.add(device)
        devices.append(device)

    await db.flush()
    print(f"[seed_cmdb_data] Created {len(devices)} device(s)")
    return devices


async def _seed_sensors(db: AsyncSession, tenant_id: uuid.UUID, devices: list[Device], sources: list[MonitoringSource]) -> None:
    """Create sensors for devices."""
    if not devices or not sources:
        return

    source = sources[0]
    sensor_repo = SensorRepository(db)

    for device in devices:
        existing_sensors = await sensor_repo.list_for_device(device.id)
        if existing_sensors:
            continue

        sensor_types = ["cpu", "memory", "disk", "interface_traffic"]
        for sensor_type in sensor_types:
            sensor = Sensor(
                tenant_id=tenant_id,
                monitoring_source_id=source.id,
                device_id=device.id,
                external_id=f"{device.name}-{sensor_type}",
                name=f"{sensor_type} sensor",
                metric_type=sensor_type,
                unit="%" if sensor_type in ["cpu", "memory", "disk"] else "mbps",
                current_value=50.0,
                status="up",
            )
            db.add(sensor)

    await db.flush()
    print(f"[seed_cmdb_data] Created sensors for {len(devices)} device(s)")


async def _seed_relationships(db: AsyncSession, tenant_id: uuid.UUID, devices: list[Device], sites: list[Site]) -> None:
    """Create asset relationships."""
    existing = (
        await db.execute(select(AssetRelationship).where(AssetRelationship.tenant_id == tenant_id))
    ).scalars().all()
    if existing:
        return

    relationships = []
    # Device -> Site
    for device in devices:
        if device.site_id:
            rel = AssetRelationship(
                tenant_id=tenant_id,
                asset_id=device.id,
                related_asset_id=device.site_id,
                relationship_type="depends_on",
                label="located_in",
                weight=1.0,
            )
            relationships.append(rel)

    # Device -> Device (some fake dependencies)
    for i in range(min(3, len(devices) - 1)):
        rel = AssetRelationship(
            tenant_id=tenant_id,
            asset_id=devices[i].id,
            related_asset_id=devices[i + 1].id,
            relationship_type="dependency",
            label="depends_on",
            weight=0.8,
        )
        relationships.append(rel)

    for rel in relationships:
        db.add(rel)

    await db.flush()
    print(f"[seed_cmdb_data] Created {len(relationships)} relationship(s)")


async def seed_cmdb_data(db: AsyncSession) -> int:
    missing = _missing_required_env_vars()
    if missing:
        print(f"[seed_cmdb_data] Aborted: missing env vars: {', '.join(missing)}")
        return 1

    tenant_id = uuid.UUID(os.environ["SEED_TENANT_ID"])
    print(f"[seed_cmdb_data] Seeding CMDB data for tenant_id={tenant_id}")

    # 1. Sites
    sites = await _seed_sites(db, tenant_id)

    # 2. Monitoring sources
    source_repo = MonitoringSourceRepository(db)
    sources = await source_repo.list(tenant_id)
    if not sources:
        print("[seed_cmdb_data] No monitoring sources found - device/sensor seeding limited")
    else:
        print(f"[seed_cmdb_data] Found {len(sources)} monitoring source(s)")

    # 3. Devices
    devices = await _seed_devices(db, tenant_id, sites, sources)

    # 4. Sensors
    await _seed_sensors(db, tenant_id, devices, sources)

    # 5. Relationships
    await _seed_relationships(db, tenant_id, devices, sites)

    return 0


async def main() -> int:
    try:
        async with AsyncSessionLocal() as db:
            exit_code = await seed_cmdb_data(db)
            if exit_code == 0:
                await db.commit()
            else:
                await db.rollback()
            return exit_code
    except Exception as exc:  # noqa: BLE001
        print(f"[seed_cmdb_data] Failed: {exc}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
