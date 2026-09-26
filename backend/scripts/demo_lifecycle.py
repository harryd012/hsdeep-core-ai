"""Isolated DEMO lifecycle fixture — seed / validate / cleanup.

Runs the REAL production chain for a clearly-separated demo tenant:
  demo tenant -> site -> device -> SOP(+approved version) -> alerts
  -> CorrelationEngine.correlate() -> IncidentRepository.create() -> COMMIT
  -> post-commit trigger_rca_for_incident() -> AI_RCA_STARTED -> RCALifecycle
  -> RCAService -> DependencyEnrichedRootCauseAnalyser -> Dependency Intelligence
  -> SOPRecommendationEngine -> persisted root_cause_summary /
  supporting_evidence / recommended_sop.

The intelligence is produced by the real engines - never hardcoded here.

Usage:
    python scripts/demo_lifecycle.py seed       # create + run + report (keeps rows)
    python scripts/demo_lifecycle.py validate   # read the demo incident back
    python scripts/demo_lifecycle.py cleanup    # delete every demo row

Isolation: every row belongs to a tenant named 'HSDEEP-DEMO-E2E%'; cleanup
deletes only by that name. No customer tenant is touched.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

# Allow both `python scripts/demo_lifecycle.py ...` and
# `python -m scripts.demo_lifecycle ...` from the backend root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db import AsyncSessionLocal
from app.repositories.alert_repository import AlertRepository
from app.repositories.incident_repository import IncidentRepository
from app.services.alerting.correlation_engine import CorrelationEngine
from app.services.events.event_bus import EventBus
from app.services.rca_lifecycle import RCALifecycle, create_rca_service
from app.services.rca_trigger import trigger_rca_for_incident

TAG = "HSDEEP-DEMO-E2E"
PATTERN = f"{TAG}%"
NOW = datetime.now(timezone.utc)
IDS = {
    "tenant": uuid.uuid4(),
    "site": uuid.uuid4(),
    "device": uuid.uuid4(),
    "sop": uuid.uuid4(),
    "version": uuid.uuid4(),
}


async def seed_rows(session) -> None:
    await session.execute(
        text("INSERT INTO tenants (id, name, slug) VALUES (:i, :n, :s)"),
        {"i": str(IDS["tenant"]), "n": f"{TAG} tenant", "s": f"demo-{IDS['tenant'].hex[:12]}"},
    )
    await session.execute(
        text("INSERT INTO sites (id, tenant_id, name, code) VALUES (:i, :t, :n, :c)"),
        {"i": str(IDS["site"]), "t": str(IDS["tenant"]), "n": f"{TAG} site", "c": "DEMO-E2E"},
    )
    await session.execute(
        text(
            "INSERT INTO devices (id, tenant_id, site_id, name, device_type, vendor, status) "
            "VALUES (:i, :t, :s, :n, 'switch', 'demo-vendor', 'up')"
        ),
        {"i": str(IDS["device"]), "t": str(IDS["tenant"]), "s": str(IDS["site"]),
         "n": f"{TAG} core-switch"},
    )
    # Eligible SOP (category matches the root-cause alert) + APPROVED version.
    await session.execute(
        text(
            "INSERT INTO sops (id, tenant_id, sop_number, name, category, is_active) "
            "VALUES (:i, :t, 'DEMO-SOP-001', :n, 'snmp', true)"
        ),
        {"i": str(IDS["sop"]), "t": str(IDS["tenant"]),
         "n": f"{TAG} SNMP connectivity troubleshooting"},
    )
    await session.execute(
        text(
            "INSERT INTO sop_versions (id, tenant_id, sop_id, version_number, status) "
            "VALUES (:i, :t, :s, 1, 'approved')"
        ),
        {"i": str(IDS["version"]), "t": str(IDS["tenant"]), "s": str(IDS["sop"])},
    )
    await session.execute(
        text("UPDATE sops SET current_version_id = :v WHERE id = :s"),
        {"v": str(IDS["version"]), "s": str(IDS["sop"])},
    )


async def new_alert(repo, *, category, status, severity, title):
    return await repo.create(
        tenant_id=IDS["tenant"], site_id=IDS["site"], device_id=IDS["device"],
        sensor_id=None, monitoring_source_id=None, title=title,
        description=f"{TAG} {category}", severity=severity, status=status,
        metric_type=None, category=category, vendor="demo-vendor", region=None,
        source="demo_e2e", triggered_value=None, threshold_value=None,
        triggered_at=NOW, last_occurred_at=NOW, occurrence_count=1,
    )


async def find_demo_incident():
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                text(
                    "SELECT i.id FROM incidents i JOIN tenants t ON t.id = i.tenant_id "
                    "WHERE t.name LIKE :p ORDER BY i.last_alert_at DESC LIMIT 1"
                ),
                {"p": PATTERN},
            )
        ).first()
        return row[0] if row else None


async def do_seed() -> None:
    async with AsyncSessionLocal() as session:
        await seed_rows(session)
        alert_repo = AlertRepository(session)
        incident_repo = IncidentRepository(session)
        engine = CorrelationEngine(alert_repo, incident_repo)

        # SNMP FIRST so the incident is born with the SNMP alert as its root
        # cause (category 'snmp' -> eligible SOP category). The ICMP evidence is
        # then attached to that SAME incident using the existing repository
        # grouping API, so no orphan incident is created.
        snmp = await new_alert(alert_repo, category="snmp", status="open",
                               severity="critical", title=f"{TAG} PRTG SNMP CPU Usage")
        created = await engine.correlate(snmp)
        incident = created.incident

        icmp = await new_alert(alert_repo, category="icmp", status="resolved",
                               severity="info", title=f"{TAG} PRTG ping")
        await alert_repo.set_correlation_fields(
            icmp, correlation_id=incident.id, incident_id=incident.id
        )
        await incident_repo.add_alert(incident, occurred_at=NOW)

        incident_id = incident.id
        await session.commit()
        print(f"seed: tenant={IDS['tenant']} incident={incident_id} status={incident.status}")

        bus = EventBus()
        lifecycle = RCALifecycle(create_rca_service(event_bus=bus), bus)
        await lifecycle.start()
        published = await trigger_rca_for_incident(
            incident_id, IDS["tenant"], incident_repo=incident_repo, event_bus=bus
        )
        print(f"seed: post-commit AI_RCA_STARTED published={published}")
        await asyncio.sleep(3)
        await bus.stop()
    await do_validate()


async def do_validate() -> None:
    incident_id = await find_demo_incident()
    if incident_id is None:
        print("validate: no demo incident found (run 'seed' first)")
        return
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                text(
                    "SELECT status, severity, alert_count, root_cause_summary, "
                    "supporting_evidence, recommended_sop FROM incidents WHERE id = :i"
                ),
                {"i": str(incident_id)},
            )
        ).first()
        print(f"validate: incident={incident_id}")
        print(f"  status={row[0]} severity={row[1]} alert_count={row[2]}")
        print(f"  recommended_sop={row[5]}")
        print(f"  root_cause_summary={(row[3] or '')[:220]}")
        evidence = row[4]
        entries = evidence.get("evidence", []) if isinstance(evidence, dict) else evidence
        entries = entries if isinstance(entries, list) else []
        di = [
            e for e in entries
            if isinstance(e, dict) and str(e.get("source", "")).startswith("dependency_intelligence")
        ]
        print(f"  evidence entries={len(entries)} | dependency entries={len(di)}")
        for entry in di:
            print(
                f"   -> {entry.get('capability')} healthy={entry.get('healthy_dependencies')} "
                f"confidence={entry.get('confidence')} "
                f"excluded={entry.get('excluded_conclusions')}"
            )
            print(f"      causes={entry.get('candidate_causes')}")


async def do_cleanup() -> None:
    async with AsyncSessionLocal() as session:
        for table in ("alerts", "incidents", "devices", "sites", "sop_versions", "sops"):
            result = await session.execute(
                text(
                    f"DELETE FROM {table} WHERE tenant_id IN "
                    "(SELECT id FROM tenants WHERE name LIKE :p)"
                ),
                {"p": PATTERN},
            )
            print(f"  deleted {table}: {result.rowcount}")
        result = await session.execute(
            text("DELETE FROM tenants WHERE name LIKE :p"), {"p": PATTERN}
        )
        print(f"  deleted tenants: {result.rowcount}")
        await session.commit()


def main() -> None:
    action = sys.argv[1] if len(sys.argv) > 1 else "seed"
    if action == "seed":
        asyncio.run(do_seed())
    elif action == "validate":
        asyncio.run(do_validate())
    elif action == "cleanup":
        asyncio.run(do_cleanup())
    else:
        print("usage: python scripts/demo_lifecycle.py [seed|validate|cleanup]")


if __name__ == "__main__":
    main()
