"""Cloud Operations KPI service.

Answers ONE question for one authenticated tenant: what is the current state of
the cloud estate? - and returns six KPI cards: three measured from real,
tenant-scoped telemetry and three explicitly marked unavailable because the
corresponding integrations do not exist in this repository.

DATA SOURCE CONVENTIONS (authoritative, tenant-scoped repo methods only):

    1. Cloud Resources       - DeviceRepository.count(device_type="cloud_resource")
    2. Resource Health       - DeviceRepository.counts_by_state
                               (up/down/stale/unknown/total)
    3. Active Cloud Incidents - IncidentRepository.active_counts
                                (statuses: open + acknowledged + investigating)
    4. Cloud Spend           - NOT CONFIGURED (no billing integration)
    5. Policy Compliance     - NOT CONFIGURED (no policy-assessment integration)
    6. Backup Readiness      - NOT CONFIGURED (no backup/recovery evidence)

Nothing here is independently recalculated: each measured card is read through
the same repository method the detail pages already use, so this panel's numbers
can never disagree with Operations / Command Center / Infrastructure for the
same tenant. Tenant scope is applied by the repository, in SQL, on every query.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.repositories.device_repository import DeviceRepository
from app.repositories.incident_repository import IncidentRepository
from app.schemas.dashboard import CloudOperations, CloudOpsKPI
from app.services.operational_intelligence_service import (
    DEVICE_FRESHNESS_WINDOW_SECONDS,
)

#: Explicit "not configured" reason strings - surfaced verbatim to the operator.
REASON_BILLING_ABSENT = "billing integration not configured (no CostManagementClient)"
REASON_POLICY_ABSENT = "policy-assessment integration not configured (no policyinsights)"
REASON_BACKUP_ABSENT = "backup/recovery integration not configured (no RecoveryServicesBackup)"

#: Incident statuses the platform treats as an active (unresolved) incident.
ACTIVE_INCIDENT_STATUSES = ("open", "acknowledged", "investigating")


class CloudOperationsService:
    """Thin tenant-scoped aggregator for the Cloud Operations KPI panel."""

    def __init__(
        self,
        device_repo: DeviceRepository,
        incident_repo: IncidentRepository,
    ) -> None:
        self.device_repo = device_repo
        self.incident_repo = incident_repo

    async def get(self, tenant_id: uuid.UUID) -> CloudOperations:
        """Return the six-card Cloud Operations KPI snapshot for ``tenant_id``."""
        now = datetime.now(timezone.utc)

        # 1. Cloud Resources - real, tenant-scoped count of cloud_resource devices.
        cloud_resources = await self.device_repo.count(
            tenant_id, device_type="cloud_resource"
        )

        # 2. Resource Health - the canonical device state buckets
        #    (up/down/stale/unknown/total), the SAME classification the Command
        #    Center / Operational Intelligence payload exposes as
        #    `devices.canonical`, judged against the same freshness window.
        device_states = await self.device_repo.counts_by_state(
            tenant_id,
            now=now,
            freshness_seconds=DEVICE_FRESHNESS_WINDOW_SECONDS,
        )

        # 3. Active Cloud Incidents - authoritative lifecycle counts. The tuple
        #    is (active_total, critical_active); critical is a strict subset of
        #    active, so the card shows the total and carries critical in detail.
        active_total, active_critical = await self.incident_repo.active_counts(
            tenant_id, statuses=list(ACTIVE_INCIDENT_STATUSES)
        )

        return CloudOperations(
            cloud_resources=CloudOpsKPI(
                value=cloud_resources,
                last_collected_at=now,
            ),
            resource_health=CloudOpsKPI(
                value={
                    "online": device_states["up"],
                    "offline": device_states["down"],
                    "stale": device_states["stale"],
                    "unknown": device_states["unknown"],
                    "total": device_states["total"],
                },
                last_collected_at=now,
            ),
            active_incidents=CloudOpsKPI(
                value=active_total,
                detail={"total": active_total, "critical": active_critical},
                last_collected_at=now,
            ),
            cloud_spend=CloudOpsKPI(
                value=None,
                unavailable=True,
                unavailable_reason=REASON_BILLING_ABSENT,
                last_collected_at=None,
            ),
            policy_compliance=CloudOpsKPI(
                value=None,
                unavailable=True,
                unavailable_reason=REASON_POLICY_ABSENT,
                last_collected_at=None,
            ),
            backup_readiness=CloudOpsKPI(
                value=None,
                unavailable=True,
                unavailable_reason=REASON_BACKUP_ABSENT,
                last_collected_at=None,
            ),
        )
