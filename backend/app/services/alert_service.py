"""
Alert Engine service layer.

Owns the business rule that turns a Sensor's normalized status into an
Alert: when to open one, when a recurring bad reading should just bump an
existing incident's occurrence_count instead of creating noise, and when a
sensor recovering to "up" should auto-resolve whatever was open for it.

Design decision (severity source): severity is derived from the
vendor-normalized `Sensor.status` (up | down | warning | unknown) rather
than re-deriving it from warning_threshold/critical_threshold directly.
Those thresholds are metric-specific - "higher is worse" for CPU%, "lower is
worse" for free disk space - and Sensor does not yet carry a direction flag,
so guessing the comparison direction here would silently misclassify some
metric types. The threshold that corresponds to the derived severity is
still recorded on the Alert for operator context/audit trail. Tracked as a
documented follow-up once metric-direction metadata exists (see
docs/architecture.md).

Alert Normalization & Correlation Engine sprint: this class now also
enriches every created/updated Alert with the Standard Alert Model fields
(category, vendor, region, interface_id), runs `CorrelationEngine` to
group it into an Incident, and writes an `AlertHistory` row for every
lifecycle transition. `CorrelationEngine`/classification/history are
optional constructor dependencies that default-construct from the same DB
session `alert_repo` already holds - existing call sites
(`AlertService(AlertRepository(db))`) keep working unchanged; nothing here
is a breaking API change.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal

from sqlalchemy import select

from app.models.alert import Alert
from app.models.device import Device
from app.models.sensor import Sensor
from app.models.site import Site
from app.repositories.alert_history_repository import AlertHistoryRepository
from app.repositories.alert_repository import AlertRepository
from app.repositories.business_service_repository import BusinessServiceRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.incident_repository import IncidentRepository
from app.repositories.maintenance_window_repository import MaintenanceWindowRepository
from app.repositories.site_repository import SiteRepository
from app.services.alerting import event_classifier
from app.services.alerting.correlation_engine import CorrelationEngine
from app.services.alerting.lifecycle import resolve_on_clear
from app.services.alerting.severity_mapper import severity_for_sensor_status
from app.services.priority.business_impact_engine import BusinessImpactEngine
from app.services.priority.confidence_scorer import alert_confidence
from app.services.priority.decision_engine import DecisionEngine
from app.services.priority.priority_engine import PriorityEngine
from app.services.priority.risk_engine import RiskEngine

if TYPE_CHECKING:
    from app.collectors.base import NormalizedAlertSignal
    from app.models.monitoring_source import MonitoringSource

AlertAction = Literal["opened", "updated", "resolved", "noop"]


@dataclass(frozen=True)
class CreatedIncidentReference:
    """Minimal identity of an Incident created during a collection run.

    Deliberately NOT the ORM entity: only what the post-commit RCA trigger
    needs, so callers gain no coupling to Incident.
    """

    incident_id: uuid.UUID
    tenant_id: uuid.UUID


@dataclass
class AlertEvaluation:
    alert: Alert | None
    action: AlertAction


class AlertNotFound(Exception):
    pass


class AlertService:
    def __init__(
        self,
        alert_repo: AlertRepository,
        *,
        incident_repo: IncidentRepository | None = None,
        history_repo: AlertHistoryRepository | None = None,
        device_repo: DeviceRepository | None = None,
        site_repo: SiteRepository | None = None,
        correlation_engine: CorrelationEngine | None = None,
        priority_engine: PriorityEngine | None = None,
    ):
        self.alert_repo = alert_repo
        # Incidents CREATED by this service, awaiting the post-commit RCA
        # trigger. Drained by CollectionOrchestrator after a collection run.
        # Purely propagation - NOT a deduplication layer (RCALifecycle owns
        # duplicate guarding, RCATrigger owns eligibility).
        self._created_incidents: list[CreatedIncidentReference] = []
        db = alert_repo.db
        self.incident_repo = incident_repo or IncidentRepository(db)
        self.history_repo = history_repo or AlertHistoryRepository(db)
        self.device_repo = device_repo or DeviceRepository(db)
        self.site_repo = site_repo or SiteRepository(db)
        self.correlation_engine = correlation_engine or CorrelationEngine(self.alert_repo, self.incident_repo)
        # AI Priority Engine sprint: recomputed automatically after every
        # correlation pass, same "extend the existing pipeline in place"
        # pattern CorrelationEngine itself was wired in with. Optional
        # constructor dependency, default-constructed from the same
        # session - existing call sites (AlertService(AlertRepository(db)))
        # keep working unchanged.
        maintenance_window_repo = MaintenanceWindowRepository(db)
        self.priority_engine = priority_engine or PriorityEngine(
            alert_repo=self.alert_repo,
            incident_repo=self.incident_repo,
            maintenance_window_repo=maintenance_window_repo,
            business_impact_engine=BusinessImpactEngine(
                device_repo=self.device_repo,
                site_repo=self.site_repo,
                business_service_repo=BusinessServiceRepository(db),
            ),
            risk_engine=RiskEngine(),
            decision_engine=DecisionEngine(),
        )

    # -- correlation with Monitoring Engine ---------------------------------
    async def evaluate_sensor(self, sensor: Sensor) -> AlertEvaluation:
        """Called once per sensor after every monitoring sync. This is the
        single integration point between the Monitoring Engine and the
        Alert Engine - Monitoring never imports Alert code itself, the route
        layer wires the two together (see api/routes/monitoring.py)."""
        now = datetime.now(timezone.utc)
        severity = severity_for_sensor_status(sensor.status)

        active = await self.alert_repo.get_active_for_sensor(sensor.id)

        if severity is None:
            # Sensor recovered (or was never bad this sync). Auto-resolve
            # any alert still open for it.
            if active is not None:
                resolved = await self.alert_repo.resolve(active, resolved_by=None, auto=True)
                await self._record_history(
                    resolved, event_type="resolved", message="Auto-resolved: sensor recovered.",
                )
                # Resolution-on-clear: if this cleared the incident's last active
                # alert, resolve the incident too (no member alert left standing).
                if resolved.incident_id is not None:
                    incident = await self.incident_repo.get(resolved.incident_id, resolved.tenant_id)
                    await resolve_on_clear(self.alert_repo, self.incident_repo, incident)
                return AlertEvaluation(alert=resolved, action="resolved")
            return AlertEvaluation(alert=None, action="noop")

        threshold_value = (
            sensor.critical_threshold if severity == "critical" else sensor.warning_threshold
        )

        if active is not None:
            previous_severity = active.severity
            updated = await self.alert_repo.record_occurrence(
                active,
                triggered_value=sensor.current_value,
                threshold_value=threshold_value,
                severity=severity,
                occurred_at=now,
            )
            if updated.confidence_score is None:
                # Backfill for alerts created before Alert.confidence_score
                # existed, or created before this re-occurrence path also
                # started scoring it - never leaves a re-occurring legacy
                # alert permanently unscored.
                category = event_classifier.classify(metric_type=sensor.metric_type)
                await self.alert_repo.set_confidence_score(
                    updated,
                    confidence_score=alert_confidence(
                        device_resolved=updated.device_id is not None,
                        category_matched=category != "other",
                        severity_source="threshold",
                    ),
                )
            await self._record_history(
                updated,
                event_type="updated",
                message=f"Re-occurred (occurrence #{updated.occurrence_count}).",
                details={"severity": {"from": previous_severity, "to": updated.severity}}
                if previous_severity != updated.severity
                else {},
            )
            await self._correlate(updated)
            return AlertEvaluation(alert=updated, action="updated")

        category = event_classifier.classify(metric_type=sensor.metric_type)
        vendor, region = await self._resolve_vendor_region(
            device_id=sensor.device_id, monitoring_source_id=sensor.monitoring_source_id
        )
        site_id = await self._resolve_site_id(sensor.device_id)

        created = await self.alert_repo.create(
            tenant_id=sensor.tenant_id,
            site_id=site_id,
            device_id=sensor.device_id,
            sensor_id=sensor.id,
            interface_id=sensor.interface_id,
            monitoring_source_id=sensor.monitoring_source_id,
            title=self._build_title(sensor),
            description=f"{sensor.name} reported status '{sensor.status}'.",
            severity=severity,
            status="open",
            metric_type=sensor.metric_type,
            category=category,
            vendor=vendor,
            region=region,
            source="monitoring_sync",
            triggered_value=sensor.current_value,
            threshold_value=threshold_value,
            triggered_at=now,
            last_occurred_at=now,
            occurrence_count=1,
        )
        await self.alert_repo.set_confidence_score(
            created,
            confidence_score=alert_confidence(
                device_resolved=sensor.device_id is not None,
                category_matched=category != "other",
                severity_source="threshold",
            ),
        )
        await self._record_history(created, event_type="opened", message=f"Alert opened: {created.title}")
        await self._correlate(created)
        return AlertEvaluation(alert=created, action="opened")

    @staticmethod
    def _build_title(sensor: Sensor) -> str:
        label = sensor.metric_type.replace("_", " ").title()
        return f"{label} — {sensor.name}"

    # -- Standard Alert Model enrichment (Correlation Engine sprint) --------
    async def _resolve_vendor_region(
        self, *, device_id: uuid.UUID | None, monitoring_source_id: uuid.UUID | None
    ) -> tuple[str | None, str | None]:
        """Resolves the denormalized (vendor, region) pair for a new alert.
        Vendor prefers the linked Device's vendor (more specific); falls
        back to nothing if there's no device link yet - vendor is NOT
        derived from monitoring_source_id here since MonitoringSource's
        `vendor` column is a display label the operator may not have set
        (see monitoring_source.py), whereas Device.vendor is populated by
        the collector itself. Region comes from the device's Site, if any.
        Never raises - a device/site lookup miss just leaves both as None,
        same "enrich best-effort, never block alert creation" pattern the
        rest of this codebase uses (e.g. BundleSaver's KNOWN GAPS notes).
        """
        if device_id is None:
            return None, None
        # device_repo.get() is tenant-scoped and would need a tenant_id we
        # don't have handy here without an extra parameter threaded through
        # every caller - a direct, non-tenant-filtered lookup by id is safe
        # in this internal enrichment context since the device_id itself
        # already came from a tenant-scoped row (the Sensor/signal that
        # triggered this alert), not from client input.
        result = await self.device_repo.db.execute(select(Device).where(Device.id == device_id))
        device = result.scalar_one_or_none()
        if device is None:
            return None, None
        region = None
        if device.site_id is not None:
            site_result = await self.site_repo.db.execute(select(Site).where(Site.id == device.site_id))
            site = site_result.scalar_one_or_none()
            region = site.region if site is not None else None
        return device.vendor, region

    async def _resolve_site_id(self, device_id: uuid.UUID | None) -> uuid.UUID | None:
        if device_id is None:
            return None
        result = await self.device_repo.db.execute(select(Device).where(Device.id == device_id))
        device = result.scalar_one_or_none()
        return device.site_id if device is not None else None

    async def _correlate(self, alert: Alert) -> None:
        """Runs CorrelationEngine for `alert` and writes an AlertHistory
        entry if it joined/created an incident. Swallows nothing silently
        wrong - if correlation legitimately has no grouping key (no
        device_id/site_id), CorrelationResult.incident is simply None and
        no history entry is written, which is correct behavior, not an
        error being hidden."""
        result = await self.correlation_engine.correlate(alert)
        if result.incident is None:
            return
        if not result.recomputed:
            # Idempotent recurrence of an unchanged condition: the incident's
            # aggregate state (member set/severity/root cause/suppression) is
            # already correct, so there is nothing new to correlate. Skip the
            # redundant "correlated" history entry and the priority recompute
            # that a genuinely new/changed correlation would need - repeated
            # monitoring polls of the same condition must not re-create work.
            return
        if result.is_new_incident:
            await self._record_history(
                alert,
                event_type="correlated",
                message=f"New incident opened: {result.incident.id}",
                details={"incident_id": str(result.incident.id)},
            )
        else:
            await self._record_history(
                alert,
                event_type="correlated",
                message=f"Grouped into existing incident: {result.incident.id}",
                details={"incident_id": str(result.incident.id)},
            )
        if result.is_suppressed:
            await self._record_history(
                alert, event_type="suppressed", message="Suppressed as a child of a detected root cause.",
            )

        # AI Priority Engine sprint: recompute priority/risk/confidence for
        # the incident this alert just joined/created. Runs after every
        # correlation pass so the incident's priority always reflects its
        # current member set, not just its state at creation.
        await self.priority_engine.recompute(result.incident)

        if result.is_new_incident:
            # Propagation only: the caller that owns the transaction drains
            # this AFTER a successful commit, so RCA can never fire for an
            # Incident that subsequently rolls back.
            self._created_incidents.append(
                CreatedIncidentReference(
                    incident_id=result.incident.id,
                    tenant_id=result.incident.tenant_id,
                )
            )

    def drain_created_incidents(self) -> list[CreatedIncidentReference]:
        """Return (and clear) Incidents created since the last drain.

        MUST be called only after the caller's transaction has committed; the
        returned references are what the post-commit RCA trigger receives.
        """
        created = list(self._created_incidents)
        self._created_incidents.clear()
        return created

    async def _record_history(
        self, alert: Alert, *, event_type: str, message: str, details: dict | None = None
    ) -> None:
        await self.history_repo.record(
            tenant_id=alert.tenant_id,
            alert_id=alert.id,
            event_type=event_type,
            message=message,
            occurred_at=datetime.now(timezone.utc),
            details=details or {},
        )

    async def upsert_vendor_native(
        self, signal: "NormalizedAlertSignal", source: "MonitoringSource", *, device_id: uuid.UUID | None = None
    ) -> AlertEvaluation:
        """Vendor-native counterpart to evaluate_sensor(): records a
        vendor-reported alarm/alert (e.g. a FortiAnalyzer alarm, a PRTG
        message) as an Alert, distinguishing it from threshold-derived alerts
        via source="vendor_native" and signal_external_id. Preserves the same
        alert-storm-prevention invariant: at most one non-resolved Alert per
        (monitoring_source_id, signal_external_id).

        Note: unlike evaluate_sensor(), there is no auto-resolve branch here.
        A vendor-native signal (e.g. a PRTG message) doesn't carry an inverse
        "recovered" signal in the same call the way a Sensor's status
        flipping to "up" does, so there is no "resolved"/"noop" action. If
        vendor-native auto-resolve semantics are needed, that requires new
        design input (e.g. how a vendor communicates recovery) rather than
        being invented here.
        """
        now = datetime.now(timezone.utc)

        active = await self.alert_repo.get_active_for_signal(source.id, signal.external_id)

        if active is not None:
            updated = await self.alert_repo.record_occurrence(
                active,
                triggered_value=None,
                threshold_value=None,
                severity=signal.severity,
                occurred_at=now,
            )
            if updated.confidence_score is None:
                category = event_classifier.classify(title=signal.title, description=signal.description)
                await self.alert_repo.set_confidence_score(
                    updated,
                    confidence_score=alert_confidence(
                        device_resolved=updated.device_id is not None,
                        category_matched=category != "other",
                        severity_source="vendor_native",
                    ),
                )
            await self._record_history(updated, event_type="updated", message="Vendor-native signal re-occurred.")
            await self._correlate(updated)
            return AlertEvaluation(alert=updated, action="updated")

        category = event_classifier.classify(title=signal.title, description=signal.description)
        vendor, region = await self._resolve_vendor_region(
            device_id=device_id, monitoring_source_id=source.id
        )
        site_id = await self._resolve_site_id(device_id)

        created = await self.alert_repo.create(
            tenant_id=source.tenant_id,
            site_id=site_id,
            device_id=device_id,
            sensor_id=None,
            monitoring_source_id=source.id,
            title=signal.title,
            description=signal.description,
            severity=signal.severity,
            status="open",
            metric_type=None,
            category=category,
            vendor=vendor,
            region=region,
            source="vendor_native",
            signal_external_id=signal.external_id,
            triggered_value=None,
            threshold_value=None,
            triggered_at=signal.triggered_at,
            last_occurred_at=now,
            occurrence_count=1,
        )
        await self.alert_repo.set_confidence_score(
            created,
            confidence_score=alert_confidence(
                device_resolved=device_id is not None,
                category_matched=category != "other",
                severity_source="vendor_native",
            ),
        )
        await self._record_history(created, event_type="opened", message=f"Vendor-native alert opened: {created.title}")
        await self._correlate(created)
        return AlertEvaluation(alert=created, action="opened")

    # -- operator actions ----------------------------------------------------
    async def acknowledge(
        self, alert_id: uuid.UUID, tenant_id: uuid.UUID, acknowledged_by: uuid.UUID
    ) -> Alert:
        alert = await self._get_or_raise(alert_id, tenant_id)
        acknowledged = await self.alert_repo.acknowledge(alert, acknowledged_by=acknowledged_by)
        await self._record_history(
            acknowledged,
            event_type="acknowledged",
            message="Alert acknowledged by operator.",
            details={"acknowledged_by": str(acknowledged_by)},
        )
        return acknowledged

    async def resolve(
        self, alert_id: uuid.UUID, tenant_id: uuid.UUID, resolved_by: uuid.UUID | None
    ) -> Alert:
        alert = await self._get_or_raise(alert_id, tenant_id)
        resolved = await self.alert_repo.resolve(alert, resolved_by=resolved_by, auto=False)
        await self._record_history(
            resolved,
            event_type="resolved",
            message="Alert manually resolved by operator.",
            details={"resolved_by": str(resolved_by)} if resolved_by else {},
        )
        return resolved

    async def get(self, alert_id: uuid.UUID, tenant_id: uuid.UUID) -> Alert:
        return await self._get_or_raise(alert_id, tenant_id)

    async def list_alerts(self, tenant_id: uuid.UUID, **filters) -> list[Alert]:
        return await self.alert_repo.list(tenant_id, **filters)

    async def summary(self, tenant_id: uuid.UUID) -> dict:
        return await self.alert_repo.summary(tenant_id)

    async def statistics(self, tenant_id: uuid.UUID) -> dict:
        return await self.alert_repo.statistics(tenant_id)

    async def get_history(self, alert_id: uuid.UUID, tenant_id: uuid.UUID) -> list:
        """Alert Timeline API - validates the alert belongs to `tenant_id`
        before returning its history (same tenant-isolation-first pattern
        as every other per-id lookup in this class)."""
        await self._get_or_raise(alert_id, tenant_id)
        return await self.history_repo.list_for_alert(alert_id)

    async def _get_or_raise(self, alert_id: uuid.UUID, tenant_id: uuid.UUID) -> Alert:
        alert = await self.alert_repo.get(alert_id, tenant_id)
        if alert is None:
            raise AlertNotFound(f"Alert {alert_id} not found")
        return alert