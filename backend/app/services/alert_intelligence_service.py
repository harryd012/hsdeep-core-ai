"""
Alert Intelligence Service (Sprint 15).

Computes the dashboard-level metrics the sprint's "Dashboard Metrics"
requirement asks for:
  - Active Incidents
  - Correlated Incidents
  - Suppressed Alerts
  - Duplicate Alerts Removed
  - Noise Reduction Percentage

All metrics are derived from real Alert/Incident rows - same "honest
heuristic" philosophy as the rest of the alerting pipeline. "Duplicate
Alerts Removed" is the sum of (occurrence_count - 1) across all active
alerts, i.e. how many individual sensor readings were collapsed into
existing alert rows instead of creating new ones. "Noise Reduction
Percentage" is the ratio of suppressed+deduplicated alerts to the total
raw alert volume, showing how much dashboard noise the correlation engine
eliminated.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import Alert
from app.models.incident import Incident


@dataclass
class AlertIntelligenceMetrics:
    """Sprint 15 dashboard metrics for the Alert Intelligence widget."""
    active_incidents: int
    correlated_incidents: int
    suppressed_alerts: int
    duplicate_alerts_removed: int
    noise_reduction_pct: float
    total_raw_alerts: int  # occurrence_count sum (what would have been shown without dedup)
    total_displayed_alerts: int  # distinct alert rows currently active


class AlertIntelligenceService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_metrics(self, tenant_id: uuid.UUID) -> AlertIntelligenceMetrics:
        """Compute all Sprint 15 dashboard metrics for a tenant in a single
        service call. The route layer maps this directly to a JSON response
        - the frontend renders the prepared DTO without any re-aggregation."""
        # Active incidents (not closed/archived)
        active_states = ("open", "acknowledged", "investigating", "monitoring")
        active_incidents_stmt = select(func.count()).select_from(Incident).where(
            Incident.tenant_id == tenant_id,
            Incident.status.in_(active_states),
            Incident.is_deleted.is_(False),
        )
        active_incidents = (await self.db.execute(active_incidents_stmt)).scalar_one()

        # Correlated incidents (incidents with alert_count > 1, i.e. more
        # than one alert was grouped into them)
        correlated_stmt = select(func.count()).select_from(Incident).where(
            Incident.tenant_id == tenant_id,
            Incident.alert_count > 1,
            Incident.is_deleted.is_(False),
        )
        correlated_incidents = (await self.db.execute(correlated_stmt)).scalar_one()

        # Suppressed alerts (is_suppressed=True, not deleted)
        suppressed_stmt = select(func.count()).select_from(Alert).where(
            Alert.tenant_id == tenant_id,
            Alert.is_suppressed.is_(True),
            Alert.is_deleted.is_(False),
        )
        suppressed_alerts = (await self.db.execute(suppressed_stmt)).scalar_one()

        # Total raw alert volume = sum of occurrence_count across all
        # non-deleted alerts. This is how many individual sensor readings
        # would have appeared on the dashboard without dedup/correlation.
        raw_volume_stmt = select(func.coalesce(func.sum(Alert.occurrence_count), 0)).where(
            Alert.tenant_id == tenant_id,
            Alert.is_deleted.is_(False),
        )
        total_raw_alerts = (await self.db.execute(raw_volume_stmt)).scalar_one()

        # Total displayed alerts = count of distinct alert rows (what the
        # operator actually sees after dedup/correlation/suppression)
        displayed_stmt = select(func.count()).select_from(Alert).where(
            Alert.tenant_id == tenant_id,
            Alert.is_deleted.is_(False),
            Alert.is_suppressed.is_(False),
        )
        total_displayed_alerts = (await self.db.execute(displayed_stmt)).scalar_one()

        # Duplicate alerts removed = raw volume - displayed rows
        # (each alert row with occurrence_count=5 represents 4 duplicates
        # that were collapsed into it)
        duplicate_alerts_removed = max(0, total_raw_alerts - total_displayed_alerts - suppressed_alerts)

        # Noise reduction percentage: how much of the raw volume was
        # eliminated by dedup + suppression + correlation
        if total_raw_alerts > 0:
            noise_reduction = ((total_raw_alerts - total_displayed_alerts) / total_raw_alerts) * 100.0
        else:
            noise_reduction = 0.0

        return AlertIntelligenceMetrics(
            active_incidents=active_incidents,
            correlated_incidents=correlated_incidents,
            suppressed_alerts=suppressed_alerts,
            duplicate_alerts_removed=duplicate_alerts_removed,
            noise_reduction_pct=round(noise_reduction, 1),
            total_raw_alerts=total_raw_alerts,
            total_displayed_alerts=total_displayed_alerts,
        )