"""
A minimal PRTG lookup adapter that uses existing SensorRepository to
return real sensor state when available. This is a safe, read-only
adapter (no external network calls) that provides a real evidence
record from the DB for the `prtg_lookup` tool name used by tests.
"""
from __future__ import annotations

from app.core.db import AsyncSessionLocal
from app.repositories.sensor_repository import SensorRepository


class PrtgLookupAdapter:
    async def execute(self, input: dict | None, context: dict | None) -> dict:
        sensor_key = (input or {}).get("sensor")
        async with AsyncSessionLocal() as db:
            repo = SensorRepository(db)
            sensor = None
            tenant_id = None
            if context:
                tenant_id = context.get("tenant_id")
            # If tenant_id is provided, try a name search within that tenant.
            if sensor_key and tenant_id:
                try:
                    sensors = await repo.list_for_tenant(
                        tenant_id=__import__("uuid").UUID(tenant_id),
                        search=sensor_key,
                        limit=1,
                    )
                    if sensors:
                        sensor = sensors[0]
                except Exception:
                    sensor = None

            if sensor is None:
                return {"success": True, "output": {"message": "no sensor matched"}}

            return {
                "success": True,
                "output": {
                    "sensor_id": str(sensor.id),
                    "metric_type": sensor.metric_type,
                    "status": sensor.status,
                    "last_value_at": str(sensor.last_value_at) if sensor.last_value_at else None,
                },
            }


adapter = PrtgLookupAdapter()
