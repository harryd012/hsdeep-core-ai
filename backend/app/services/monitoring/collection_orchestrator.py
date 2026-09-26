"""
Collection Orchestrator.

Coordinates one end-to-end collection run for a single `MonitoringSource`:
look up its collector, authenticate, collect + normalize the vendor
payload, persist it via `BundleSaver`, route the results into the Alert
Engine, and record everything as a `CollectorRun` row - regardless of
whether the run succeeded, failed, or was skipped.

This is the only place in the codebase that ties the Collector Framework,
the Normalization Engine (`BundleSaver`), the Alert Engine (`AlertService`),
and the `CollectorRun` audit trail together. `MonitoringService.sync_source()`
predates this class and does not yet call into it (see that method's
docstring); new scheduled/manual collection runs should go through
`CollectionOrchestrator.run()` instead.

Design deviation note: the design lists `crypto: SecretCrypto` as a
constructor dependency, matching `MonitoringService`'s use of `SecretCrypto`
for encrypting credentials on write. However, decrypting a
`MonitoringSource`'s credentials for a collector is done via the
module-level `decrypt_connection_info(source)` function in
`app.core.security` (as `MonitoringService._build_collector` already does),
not via a method on `SecretCrypto` itself - `SecretCrypto` has no
`decrypt_connection_info`-shaped method, only plain `encrypt`/`decrypt` for
a single string. Since matching a non-existent method call would be worse
than a documented simplification, `crypto` is intentionally omitted from
this class's constructor. `decrypt_connection_info(source)` is called
directly inside `run()` instead.
"""
from __future__ import annotations

import logging

from app.collectors.base import CollectorConfigError, CollectorRegistry
from app.core.security import decrypt_connection_info
from app.models.collector_run import CollectorRun
from app.models.monitoring_source import MonitoringSource
from app.repositories.collector_run_repository import CollectorRunRepository
from app.repositories.monitoring_source_repository import MonitoringSourceRepository
from app.services.alert_service import AlertService
from app.services.ingester.pipeline import count_collector_run, flush_usage_counters
from app.services.monitoring.bundle_saver import BundleSaver

logger = logging.getLogger(__name__)


class CollectionOutcome:
    """Return wrapper for `CollectionOrchestrator.run()`.

    Exposes the finished `CollectorRun` both directly (`outcome.status`,
    used by the sync-source API route and collection-pipeline tests) and
    namespaced (`outcome.run_record`, used by the orchestrator error-path
    tests) so both call-site conventions keep working.
    """

    def __init__(
        self,
        run_record: CollectorRun,
        created_incidents: list | None = None,
    ) -> None:
        self.run_record = run_record
        # Incidents CREATED during this run, as minimal (incident_id, tenant_id)
        # references. Populated by run(); the transaction owner triggers RCA for
        # these only AFTER a successful commit.
        self.created_incidents = list(created_incidents or [])

    def __getattr__(self, name: str):
        return getattr(self.run_record, name)


class CollectionOrchestrator:
    """Runs one collection cycle for a `MonitoringSource` and records the
    outcome as a `CollectorRun`. Never raises for vendor-side failures -
    `run()` always returns a `CollectorRun` row describing what happened.
    """

    def __init__(
        self,
        run_repo: CollectorRunRepository,
        saver: BundleSaver,
        alert_service: AlertService,
        source_repo: MonitoringSourceRepository,
    ):
        self.run_repo = run_repo
        self.saver = saver
        self.alert_service = alert_service
        self.source_repo = source_repo

    async def run(self, source: MonitoringSource, trigger: str = "scheduler") -> CollectorRun:
        # Metering hook (usage metering sprint): every completed run - success,
        # error, or skipped - increments the tenant's collector.run usage
        # counter. Best-effort: a metering failure must never fail the run.
        async def _meter() -> None:
            try:
                await count_collector_run(source.tenant_id, self.run_repo.db)
                await flush_usage_counters(self.run_repo.db)
            except Exception:  # noqa: BLE001 - metering must never break collection
                logger.warning("Metering record failed for tenant %s", source.tenant_id)

        # Concurrent-run-skip guard FIRST, before creating the "real"
        # CollectorRun row - if a run is already in flight for this source,
        # record a distinct "skipped" run and return immediately without
        # touching the source's status (Requirement 5.7).
        existing_running = await self.run_repo.get_running_for_source(source.id)
        if existing_running is not None:
            skipped_run = await self.run_repo.create(
                tenant_id=source.tenant_id, monitoring_source_id=source.id, trigger=trigger
            )
            skipped_run = await self.run_repo.finish(
                skipped_run,
                status="skipped",
                error="Skipped: a collection run is already in progress for this source.",
            )
            await _meter()
            return CollectionOutcome(skipped_run)

        run_record = await self.run_repo.create(
            tenant_id=source.tenant_id, monitoring_source_id=source.id, trigger=trigger
        )

        # Bound before the try so the cleanup below can always see it.
        collector = None

        try:
            collector_cls = CollectorRegistry.get(source.source_type)
            connection_info = decrypt_connection_info(source)
            collector = collector_cls(connection_info)

            check = await collector.authenticate()
            if not check.success:
                run_record = await self.run_repo.finish(
                    run_record, status="error", error=check.message
                )
                await self.source_repo.mark_sync_result(source, status="error", error=check.message)
                await _meter()
                return CollectionOutcome(run_record)

            raw = await collector.collect()
            bundle = collector.normalize(raw)
            save_result = await self.saver.save(source, bundle)

            # alerts_upserted counts DISTINCT Alert rows touched this run -
            # NOT the raw count of vendor alert signals or changed sensors
            # fed in, and NOT a running tally of every evaluate/upsert call.
            # A single Alert row can legitimately be hit multiple times in
            # one run (e.g. a vendor reports the same underlying alarm as
            # several duplicate message rows, or a sensor's evaluate_sensor
            # call and a later re-check both touch the same open Alert) -
            # alert-storm-prevention in AlertRepository means those calls all
            # resolve to the SAME row, so counting calls (rather than
            # distinct row ids) would vastly overstate how many Alert rows
            # were actually written, exactly as the raw vendor-signal count
            # did before this fix. Track affected Alert ids in a set instead.
            touched_alert_ids: set = set()
            for sensor in save_result.changed_sensors:
                evaluation = await self.alert_service.evaluate_sensor(sensor)
                if evaluation.action != "noop" and evaluation.alert is not None:
                    touched_alert_ids.add(evaluation.alert.id)
            for signal in save_result.vendor_alert_signals:
                # Alert Correlation Engine sprint: resolve device_id from
                # this bundle's device_external_id -> Device.id map (built
                # by BundleSaver.save() and handed back here) so vendor-
                # native alerts (the primary path for PRTG "messages")
                # participate in device-based correlation instead of
                # always landing with device_id=None.
                resolved_device_id = None
                if signal.device_external_id is not None:
                    resolved_device_id = save_result.device_id_by_signal_device_external_id.get(
                        signal.device_external_id
                    )
                evaluation = await self.alert_service.upsert_vendor_native(
                    signal, source, device_id=resolved_device_id
                )
                if evaluation.action != "noop" and evaluation.alert is not None:
                    touched_alert_ids.add(evaluation.alert.id)
            alerts_upserted = len(touched_alert_ids)
            # Incidents created during this run (zero, one, or many). NOT
            # triggered here: the transaction owner fires RCA only after a
            # successful commit (see scheduler/jobs.py and monitoring route).
            created_incidents = self.alert_service.drain_created_incidents()

            run_record = await self.run_repo.finish(
                run_record,
                status="success",
                devices_upserted=save_result.devices_upserted,
                interfaces_upserted=save_result.interfaces_upserted,
                sensors_upserted=save_result.sensors_created + save_result.sensors_updated,
                metrics_recorded=save_result.metrics_recorded,
                events_recorded=save_result.events_recorded,
                alerts_upserted=alerts_upserted,
            )
            await self.source_repo.mark_sync_result(source, status="connected", error=None)
            await _meter()
            # Post-collection WAN Failover Intelligence (detect -> trace ->
            # verify). Deliberately AFTER the run's rows are written so the
            # detector reads the state this run just produced, and deliberately
            # isolated: bounded, timeout-capped and unable to raise (see
            # services/failover_intelligence/hook.py). A detection problem must
            # never turn a healthy collection run into a failed one.
            await self._run_failover_intelligence(source.tenant_id)
            return CollectionOutcome(run_record, created_incidents)

        except CollectorConfigError as exc:
            run_record = await self.run_repo.finish(run_record, status="error", error=str(exc))
            await self.source_repo.mark_sync_result(source, status="error", error=str(exc))
            await _meter()
            # Incidents created before this vendor-side failure still commit
            # (the run row is committed too), so they must be reported - and
            # drained here so they can never leak into a later run's result.
            return CollectionOutcome(run_record, self.alert_service.drain_created_incidents())
        except Exception as exc:  # noqa: BLE001 - catch-all: never propagate vendor-side failures
            run_record = await self.run_repo.finish(run_record, status="error", error=str(exc))
            await self.source_repo.mark_sync_result(source, status="error", error=str(exc))
            await _meter()
            return CollectionOutcome(run_record, self.alert_service.drain_created_incidents())

        finally:
            # Release whatever the collector owns (an SNMP engine's dispatcher and
            # its timers, for example). A collector that leaks them per run
            # exhausts the process on a scheduled fleet, so this is closed on
            # EVERY path - success, config error, vendor failure. Cleanup failure
            # is logged and never allowed to change the run's outcome.
            close = getattr(collector, "aclose", None)
            if close is not None:
                try:
                    await close()
                except Exception:  # noqa: BLE001 - cleanup must not fail a run
                    logger.debug("collector cleanup failed", exc_info=True)

    async def _run_failover_intelligence(self, tenant_id) -> None:
        """Best-effort post-collection WAN failover detection.

        Wrapped twice on purpose: ``run_after_collection`` already isolates its
        own failures, and this belt-and-braces ``try`` guarantees that even an
        import error or a bug in the hook's own reporting cannot affect the
        collection run that just succeeded.
        """
        try:
            from app.services.failover_intelligence.hook import run_after_collection

            report = await run_after_collection(self.run_repo.db, tenant_id)
            if report.get("status") == "ok":
                interesting = (
                    report.get("opened", 0),
                    report.get("advanced", 0),
                    report.get("recovered", 0),
                )
                if any(interesting):
                    logger.info(
                        "Failover intelligence (tenant %s): %s opened, %s advanced, "
                        "%s recovered across %s scope(s)",
                        tenant_id, *interesting, report.get("scopes"),
                    )
            elif report.get("status") in {"error", "timeout"}:
                logger.warning(
                    "Failover intelligence (tenant %s) %s: %s",
                    tenant_id, report.get("status"), report.get("reason"),
                )
        except Exception:  # noqa: BLE001 - defence in depth
            logger.debug("Failover intelligence hook failed", exc_info=True)
