"""

Monitoring Engine service layer.

Owns the business rules for monitoring sources and sensors: which collector
to use, how a raw sensor reading becomes a stored Sensor row, and how sync
results/errors get reported back. The API layer depends only on this class;
it never touches the repositories or collectors directly.
"""
from __future__ import annotations

import uuid
from typing import Any

from app.collectors.base import (
    READINESS_UNSUPPORTED,
    STAGE_CONFIGURATION,
    BaseCollector,
    CollectorConfigError,
    CollectorRegistry,
    ReadinessResult,
)
from app.core.security import SecretCrypto, decrypt_connection_info
from app.models.monitoring_source import MonitoringSource
from app.repositories.monitoring_source_repository import MonitoringSourceRepository
from app.repositories.sensor_repository import SensorRepository
from app.services.monitoring.readiness import (
    READINESS_PROTOCOLS,
    build_result,
    classify_exception,
    classify_failure,
    configuration_problem,
    not_configured_result,
    ready_result,
)


class MonitoringSourceNotFound(Exception):
    pass


class MonitoringService:
    def __init__(self, source_repo: MonitoringSourceRepository, sensor_repo: SensorRepository):
        self.source_repo = source_repo
        self.sensor_repo = sensor_repo

    # -- sources ----------------------------------------------------------
    async def create_source(self, **fields) -> MonitoringSource:
        # Validates the source_type is real *before* hitting the DB, so a
        # typo doesn't create an orphaned, un-syncable row.
        CollectorRegistry.get(fields["source_type"])
        self._encrypt_credential_fields(fields)
        return await self.source_repo.create(**fields)

    async def get_source(self, source_id: uuid.UUID, tenant_id: uuid.UUID) -> MonitoringSource:
        source = await self.source_repo.get(source_id, tenant_id)
        if source is None:
            raise MonitoringSourceNotFound(f"MonitoringSource {source_id} not found")
        return source

    async def list_sources(self, tenant_id: uuid.UUID, **filters) -> list[MonitoringSource]:
        return await self.source_repo.list(tenant_id, **filters)

    async def update_source(self, source: MonitoringSource, **fields) -> MonitoringSource:
        self._encrypt_credential_fields(fields)
        return await self.source_repo.update(source, **fields)

    @staticmethod
    def _encrypt_credential_fields(fields: dict) -> None:
        """Pop plaintext `api_key`/`password` out of `fields` (in place) and,
        if a non-None value was supplied, encrypt it into
        `api_key_encrypted`/`password_encrypted`.

        The `MonitoringSource` model has no `api_key`/`password` columns -
        only the `_encrypted` variants - so these keys must never reach the
        repository layer. A `None` value (meaning "don't change" on an
        update) is popped but intentionally not re-added as an `_encrypted`
        key, so an unrelated partial update never wipes an existing stored
        credential. Plaintext values are never logged here.
        """
        crypto = SecretCrypto()

        api_key = fields.pop("api_key", None)
        if api_key is not None:
            fields["api_key_encrypted"] = crypto.encrypt(api_key)

        password = fields.pop("password", None)
        if password is not None:
            fields["password_encrypted"] = crypto.encrypt(password)

    async def delete_source(self, source: MonitoringSource) -> None:
        await self.source_repo.soft_delete(source)

    @staticmethod
    def supported_source_types() -> list[dict]:
        return [
            {
                "source_type": collector_cls.source_type,
                "display_name": getattr(collector_cls, "display_name", collector_cls.source_type),
                "implemented": collector_cls.implemented,
                "category": collector_cls.category,
                "firewall_capabilities": list(collector_cls.firewall_capabilities),
            }
            for collector_cls in CollectorRegistry.list_types()
        ]

    def _build_collector(self, source: MonitoringSource) -> BaseCollector:
        collector_cls = CollectorRegistry.get(source.source_type)
        connection_info = decrypt_connection_info(source)
        return collector_cls(connection_info)

    # -- connection / sync --------------------------------------------------
    async def test_connection(self, source: MonitoringSource) -> tuple[bool, str]:
        collector = self._build_collector(source)
        try:
            result = await collector.authenticate()
        except CollectorConfigError as exc:
            return False, str(exc)
        return result.success, result.message

    # -- readiness (Phase 3B §10) -------------------------------------------
    async def check_readiness(self, source: MonitoringSource) -> ReadinessResult:
        """READ-ONLY readiness probe: can this source be polled right now?

        Deliberately NOT a second implementation. It reuses `_build_collector`
        (so the same CollectorRegistry lookup and the same credential decryption
        run as in production) and calls `collector.authenticate()`, the existing
        safe handshake. `collect()`/`normalize()`/`save()` are never called, so
        this probe cannot create Devices, Interfaces, Capabilities,
        Relationships or topology edges.

        Ordering matters and is enforced here:
          1. registry lookup and credential decryption (`_build_collector`)
          2. configuration/credential validation  - BEFORE any network I/O, so a
             missing hostname can never masquerade as "unreachable"
          3. authentication/protocol handshake
          4. classify the outcome from the evidence actually available

        Never raises for an expected failure: every path returns a
        `ReadinessResult` so a caller can render a precise blocker.
        """
        try:
            collector = self._build_collector(source)
        except CollectorConfigError as exc:
            # e.g. malformed stored credentials that cannot be decrypted.
            status, stage, message = classify_exception(exc, source)
            return build_result(source, status=status, stage=stage, message=message)
        except ValueError as exc:
            # No collector registered for this source_type.
            return build_result(
                source,
                status=READINESS_UNSUPPORTED,
                stage=STAGE_CONFIGURATION,
                message=f"No collector is registered for source_type "
                        f"{getattr(source, 'source_type', None)!r}.",
            )
        except Exception as exc:  # noqa: BLE001 - diagnostics must not explode
            status, stage, message = classify_exception(exc, source)
            return build_result(source, status=status, stage=stage, message=message)

        connection_info = collector.source

        # Step 2: configuration and credentials, with no network call yet.
        problem = configuration_problem(connection_info)
        if problem is not None:
            status, stage, message = problem
            return build_result(
                connection_info, status=status, stage=stage, message=message
            )

        # Step 3: the existing safe handshake.
        try:
            check = await collector.authenticate()
        except CollectorConfigError as exc:
            status, stage, message = classify_exception(exc, connection_info)
            return build_result(
                connection_info, status=status, stage=stage, message=message
            )
        except Exception as exc:  # noqa: BLE001
            status, stage, message = classify_exception(exc, connection_info)
            return build_result(
                connection_info, status=status, stage=stage, message=message
            )

        # Step 4: classify. `success` means transport answered AND credentials
        # were accepted - NOT that any discovery data was collected.
        if check.success:
            return ready_result(
                connection_info,
                check.message or "Target responded successfully.",
            )
        status, stage, message = classify_failure(check.message, connection_info)
        return build_result(
            connection_info,
            status=status,
            stage=stage,
            message=message,
            extra_details={"collector_message": check.message},
        )

    async def readiness_report(self, tenant_id: uuid.UUID) -> dict[str, Any]:
        """Readiness for every configured source, plus per-protocol coverage.

        Coverage entries make "nothing is configured" visible instead of an
        absent line, which is what an operator needs when SNMP or WMI is simply
        missing from the estate.
        """
        sources = await self.list_sources(tenant_id)
        results = [await self.check_readiness(source) for source in sources]

        coverage: list[ReadinessResult] = []
        for source_type in READINESS_PROTOCOLS:
            matching = [
                r for r in results
                if r.safe_details.get("source_type") == source_type
            ]
            coverage.extend(matching or [not_configured_result(source_type)])

        return {
            "sources_checked": len(results),
            "results": results,
            "coverage": coverage,
        }

    # NOTE: the transitional `sync_source()` method that used to live here
    # (task 10.3 scaffolding, predating `BundleSaver`/`CollectionOrchestrator`)
    # has been removed as duplicate logic (Sprint: Enterprise Monitoring
    # Engine Completion, goal 5). It only upserted Sensor rows and never
    # created Device/Interface/Metric/Event/CollectorRun rows or routed
    # through AlertService - `CollectionOrchestrator.run()` is the complete,
    # single source of truth for running a collection cycle now, used by
    # both the manual `POST /sources/{id}/sync` endpoint and the scheduler
    # (`app/scheduler/jobs.py`). It had zero remaining callers at removal
    # time (verified via workspace-wide search).
