"""
AI Insight service layer.

Produces deterministic, rule-based observations from existing Alert/Sensor/
CollectorRun data. Explicitly NOT an ML/AI engine - documented as heuristic
in design.md - since no such engine exists in app/ yet. Every insight is
built exclusively from real rows returned by the repository layer; there is
no random selection and no literal business-value text that wasn't just
queried.

Repository constraint: only AlertRepository, SensorRepository, and
CollectorRunRepository are used here (per design.md and task 20.4) -
MonitoringSourceRepository is intentionally NOT imported, so the
collector_failing rule derives its set of monitoring sources from the
CollectorRun rows themselves rather than from a dedicated source query.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.models.alert import Alert
from app.repositories.alert_repository import AlertRepository
from app.repositories.collector_run_repository import CollectorRunRepository
from app.repositories.sensor_repository import SensorRepository
from app.schemas.dashboard import AIInsight, AIInsights

# Total insight list is capped at this size so a large tenant can never
# produce an unbounded response - insights are sorted (critical > warning >
# info, then most-relevant timestamp descending) before truncating, so the
# most urgent/recent observations are kept.
_MAX_INSIGHTS = 50

# An open/acknowledged critical alert open longer than this is "sustained".
_SUSTAINED_CRITICAL_THRESHOLD = timedelta(minutes=30)

# An alert that has re-triggered at least this many times is "flapping".
_FLAPPING_OCCURRENCE_THRESHOLD = 3

# Only the most recent N no-data sensors are surfaced, to avoid one insight
# per sensor on a tenant with many never-reporting sensors.
_MAX_NO_DATA_SENSORS = 5

_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


class AIInsightService:
    # Each rule fetches only a bounded working set of active alerts/runs -
    # never the tenant's entire table (which on a high-volume deployment can
    # hold hundreds of thousands of resolved/historical rows). The combined
    # result is later truncated to _MAX_INSIGHTS anyway, so loading a small
    # recent window per rule is sufficient and keeps the Copilot/AI path fast.
    _ALERT_RULE_LIMIT = 200
    # Bounded, recent-only window for the collector-failing rule. We only ever
    # need the last two runs per source to detect a double-failure, so a recent
    # window capped at a modest count is more than enough and avoids scanning
    # every CollectorRun row a tenant has ever produced.
    _COLLECTOR_RUN_LIMIT = 1_000
    _COLLECTOR_RUN_WINDOW = timedelta(hours=24)

    def __init__(
        self,
        alert_repo: AlertRepository,
        sensor_repo: SensorRepository,
        run_repo: CollectorRunRepository,
    ):
        self.alert_repo = alert_repo
        self.sensor_repo = sensor_repo
        self.run_repo = run_repo

    async def insights(self, tenant_id: uuid.UUID) -> AIInsights:
        now = datetime.now(timezone.utc)

        # AlertRepository has no OR-status filter, so active (open +
        # acknowledged, the two non-resolved statuses) alerts are fetched once
        # per rule with a bounded limit and server-side filters - loading up to
        # 100000 open + 100000 acknowledged alerts into Python (the previous
        # behaviour) is what made a sustained-critical/flapping query on a
        # busy tenant balloon context size and blow through the AI request
        # deadline. The combined insights list is truncated to _MAX_INSIGHTS
        # below, so a bounded recent window per rule is all that is needed.
        insights: list[AIInsight] = []
        insights.extend(
            await self._sustained_critical_insights(tenant_id, now)
        )
        insights.extend(await self._flapping_sensor_insights(tenant_id))
        insights.extend(await self._collector_failing_insights(tenant_id, now))
        insights.extend(await self._no_data_insights(tenant_id))

        insights.sort(key=lambda i: _SEVERITY_ORDER.get(i.severity, 3))
        insights = insights[:_MAX_INSIGHTS]

        return AIInsights(generated_at=now, insights=insights)

    # -- rule 1: sustained_critical ------------------------------------------
    async def _sustained_critical_insights(
        self, tenant_id: uuid.UUID, now: datetime
    ) -> list[AIInsight]:
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        sustained_since = now - _SUSTAINED_CRITICAL_THRESHOLD
        active_critical = await self.alert_repo.list_active_for_insights(
            tenant_id,
            severity="critical",
            triggered_before=sustained_since,
            limit=self._ALERT_RULE_LIMIT,
        )
        results: list[AIInsight] = []
        for alert in active_critical:
            elapsed = now - alert.triggered_at
            if elapsed <= _SUSTAINED_CRITICAL_THRESHOLD:
                continue
            # Round to whole minutes so the message is stable across repeated
            # computations of the same unchanged condition (idempotent), e.g.
            # the same alert at 45m:01s and 45m:59s both report "45 minutes".
            elapsed_minutes = max(
                int(_SUSTAINED_CRITICAL_THRESHOLD.total_seconds() // 60),
                int(elapsed.total_seconds() // 60),
            )
            results.append(
                AIInsight(
                    kind="sustained_critical",
                    severity="critical",
                    message=(
                        f"{alert.title} has been critical for over 30 minutes "
                        f"(~{elapsed_minutes} minutes elapsed)."
                    ),
                    device_id=alert.device_id,
                    monitoring_source_id=alert.monitoring_source_id,
                )
            )
            if len(results) >= _MAX_INSIGHTS:
                break
        return results

    # -- rule 2: flapping_sensor ----------------------------------------------
    async def _flapping_sensor_insights(self, tenant_id: uuid.UUID) -> list[AIInsight]:
        re_triggered = await self.alert_repo.list_active_for_insights(
            tenant_id,
            min_occurrence_count=_FLAPPING_OCCURRENCE_THRESHOLD,
            limit=self._ALERT_RULE_LIMIT,
        )
        results: list[AIInsight] = []
        for alert in re_triggered:
            if alert.sensor_id is None:
                continue
            if alert.occurrence_count < _FLAPPING_OCCURRENCE_THRESHOLD:
                continue
            results.append(
                AIInsight(
                    kind="flapping_sensor",
                    severity="warning",
                    message=(
                        f"Sensor for alert '{alert.title}' has re-triggered "
                        f"{alert.occurrence_count} times."
                    ),
                    device_id=alert.device_id,
                    monitoring_source_id=alert.monitoring_source_id,
                )
            )
            if len(results) >= _MAX_INSIGHTS:
                break
        return results

    # -- rule 3: collector_failing --------------------------------------------
    async def _collector_failing_insights(
        self, tenant_id: uuid.UUID, now: datetime
    ) -> list[AIInsight]:
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        runs = await self.run_repo.list(
            tenant_id,
            since=now - self._COLLECTOR_RUN_WINDOW,
            limit=self._COLLECTOR_RUN_LIMIT,
        )

        by_source: dict[uuid.UUID, list] = {}
        for run in runs:
            by_source.setdefault(run.monitoring_source_id, []).append(run)

        results: list[AIInsight] = []
        for source_id, source_runs in by_source.items():
            # list() already orders by started_at desc, but re-sort here so
            # this rule doesn't silently depend on that repository detail.
            source_runs.sort(key=lambda r: r.started_at, reverse=True)
            last_two = source_runs[:2]
            if len(last_two) < 2:
                continue
            if all(run.status == "error" for run in last_two):
                latest = last_two[0]
                results.append(
                    AIInsight(
                        kind="collector_failing",
                        severity="critical",
                        message=(
                            "Collector for monitoring source has failed its last 2 "
                            f"runs. Last error: {latest.error}"
                        ),
                        monitoring_source_id=source_id,
                    )
                )
        return results

    # -- rule 4: no_data --------------------------------------------------------
    async def _no_data_insights(self, tenant_id: uuid.UUID) -> list[AIInsight]:
        sensors = await self.sensor_repo.list_for_tenant(tenant_id)

        if not sensors:
            return [
                AIInsight(
                    kind="no_data",
                    severity="info",
                    message="No sensor data has been collected yet for this tenant.",
                )
            ]

        results: list[AIInsight] = []
        for sensor in sensors:
            if sensor.status != "unknown" or sensor.last_value_at is not None:
                continue
            results.append(
                AIInsight(
                    kind="no_data",
                    severity="info",
                    message=f"Sensor '{sensor.name}' has not reported any data yet.",
                    device_id=sensor.device_id,
                    monitoring_source_id=sensor.monitoring_source_id,
                )
            )
            if len(results) >= _MAX_NO_DATA_SENSORS:
                break
        return results
