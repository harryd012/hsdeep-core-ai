"""
End-to-end validation of the Device Action Plane against the live DB.

Runs a REAL read-only PING request through the full pipeline
(policy -> provider -> verification -> execution audit -> action audit),
prints the evidence, then cleans up the rows it created.
"""
import asyncio
import sys

sys.path.insert(0, r"I:\AI-LAB\hsdeep-core-ai\backend")

from sqlalchemy import text  # noqa: E402

from app.core.db import AsyncSessionLocal  # noqa: E402
from app.services.device_action_service import DeviceActionService  # noqa: E402


async def main() -> None:
    async with AsyncSessionLocal() as db:
        row = (await db.execute(text("""
            SELECT d.id, d.tenant_id, d.name, d.ip_address, d.hostname
            FROM devices d
            WHERE d.is_deleted = false
              AND (d.ip_address IS NOT NULL OR d.hostname IS NOT NULL)
            ORDER BY d.created_at
            LIMIT 1
        """))).first()
        if row is None:
            print("NO DEVICE with an address found - nothing to exercise.")
            return
        device_id, tenant_id, name, ip, hostname = row
        print(f"target device: {name} ip={ip} hostname={hostname} tenant={tenant_id}")

        svc = DeviceActionService(db)

        # 1) catalog (what the API serves)
        catalog = svc.catalog()
        ping_entry = next(c for c in catalog if c["action"] == "PING")
        print("catalog[PING]:", ping_entry)

        # 2) capabilities (resolved, no fabrication)
        caps = await svc.get_capabilities(tenant_id, device_id)
        print("capabilities source:", caps["source"])
        print("supports_ping:", caps["capabilities"]["supports_ping"],
              "supports_config:", caps["capabilities"]["supports_config"])

        # 3) request a READ_ONLY action -> policy should auto-execute
        req = await svc.request_action(
            tenant_id=tenant_id, device_id=device_id, action="PING",
            params={"count": 2}, motivation={"why": "e2e validation of action plane"},
        )
        await db.commit()
        await db.refresh(req)
        print("request:", req.action, "class=", req.classification)
        print("policy:", req.policy_decision)
        print("status:", req.status, "| error:", req.error)
        print("result keys:", sorted(req.result.keys()))
        print("verification:", req.verification)
        print("correlation_id:", req.correlation_id)
        print("execution_job_id:", req.execution_job_id)

        # 4) execution + audit evidence
        job = (await db.execute(text("""
            SELECT status, target, source_type FROM execution_jobs WHERE id = :id
        """), {"id": req.execution_job_id})).first()
        print("execution_job:", job)

        logs = (await db.execute(text("""
            SELECT step_order, step_title, status FROM execution_logs
            WHERE execution_job_id = :id ORDER BY step_order
        """), {"id": req.execution_job_id})).all()
        print("execution_logs:", logs)

        audits = (await db.execute(text("""
            SELECT action FROM audit_events
            WHERE entity_id = :id ORDER BY created_at
        """), {"id": req.id})).scalars().all()
        print("audit_events:", audits)

        # 5) state-changing action must be policy-gated (no adapter yet)
        req2 = await svc.request_action(
            tenant_id=tenant_id, device_id=device_id, action="REBOOT_DEVICE",
            params={}, motivation={"why": "e2e validation: must be denied"},
        )
        await db.commit()
        await db.refresh(req2)
        print("REBOOT_DEVICE status:", req2.status)
        print("REBOOT_DEVICE policy reason:", req2.policy_decision.get("reason"))

        # 6) cleanup the rows this validation created
        await db.execute(text("DELETE FROM audit_events WHERE entity_id = :id"), {"id": req.id})
        await db.execute(text("DELETE FROM audit_events WHERE entity_id = :id"), {"id": req2.id})
        await db.execute(text("DELETE FROM execution_logs WHERE execution_job_id = :id"),
                         {"id": req.execution_job_id})
        await db.execute(text("DELETE FROM execution_jobs WHERE id = :id"),
                         {"id": req.execution_job_id})
        await db.execute(text("DELETE FROM device_action_requests WHERE id IN (:a, :b)"),
                         {"a": req.id, "b": req2.id})
        await db.commit()
        print("cleanup: validation rows removed")


asyncio.run(main())