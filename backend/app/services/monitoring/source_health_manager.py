"""
Source Health Manager.

Encapsulates the health state machine for MonitoringSource:

    HEALTHY  ─(consecutive_failures >= threshold)→  DEGRADED
       ↑                                            │
       └─(consecutive_successes >= recovery)────────┘

Responsibilities:
  - Evaluate the outcome of a CollectorRun (success/failure) and update the
    source's health_status / consecutive_failures / consecutive_successes.
  - Emit a SOURCE_RECOVERED domain event when a DEGRADED source recovers
    to HEALTHY (so downstream consumers can react, e.g. clear a maintenance
    banner or re-enable an integration).
  - Emit a SOURCE_HEALTH_DEGRADED domain event when a HEALTHY source
    transitions to DEGRADED.
  - Append an immutable SourceHealthAudit row for every transition and
    operational event (timeout, retry, rate-limited, duplicate-skip).
  - Compute the next_run_at timestamp: normal poll interval on success,
    exponential backoff on failure.

The manager is intentionally stateless — it reads the current source state
from the ORM instance passed to it and writes back the mutated fields plus
audit rows via the repositories.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from app.core.config import get_settings
from app.models.monitoring_source import HEALTH_STATUS_DEGRADED, HEALTH_STATUS_HEALTHY
from app.models.source_health_audit import (
    SOURCE_HEALTH_EVENT_RECOVERY,
    SOURCE_HEALTH_EVENT_TRANSITION,
)
from app.services.events.event_bus import DomainEvent, EventType

if TYPE_CHECKING:
    from app.models.collector_run import CollectorRun
    from app.models.monitoring_source import MonitoringSource
    from app.services.events.event_bus import EventBus
    from app.repositories.monitoring_source_repository import MonitoringSourceRepository
    from app.repositories.source_health_audit_repository import SourceHealthAuditRepository
    from app.services.monitoring.retry_backoff import RetryConfig

logger = logging.getLogger(__name__)


@dataclass
class HealthEvalResult:
    """Outcome of evaluating one collection run against the health
    state machine.

    Attributes
    ----------
    health_changed:
        True if health_status transitioned (HEALTHY↔DEGRADED).
    recovered:
        True if the source went from DEGRADED → HEALTHY.
    degraded:
        True if the source went from HEALTHY → DEGRADED.
    recovery_events:
        DomainEvent objects the caller must publish *after* committing the
        DB transaction (same deferred-publishing pattern as
        CollectionOrchestrator's run events).
    """
    health_changed: bool = False
    recovered: bool = False
    degraded: bool = False
    recovery_events: list[DomainEvent] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.recovery_events is None:
            self.recovery_events = []


class SourceHealthManager:
    """Drives the MonitoringSource health state machine.

    Constructor dependencies follow the repository+event-bus convention
    used throughout the service layer.
    """

    def __init__(
        self,
        source_repo: "MonitoringSourceRepository",
        audit_repo: "SourceHealthAuditRepository",
        event_bus: "EventBus",
        *,
        retry_config: "RetryConfig | None" = None,
    ):
        self.source_repo = source_repo
        self.audit_repo = audit_repo
        self.event_bus = event_bus

        settings = get_settings()
        if retry_config is None:
            from app.services.monitoring.retry_backoff import RetryConfig

            retry_config = RetryConfig(
                max_attempts=settings.retry_max_attempts,
                base_delay_seconds=settings.retry_backoff_base_seconds,
                max_delay_seconds=settings.retry_max_delay_seconds,
                jitter=settings.retry_jitter,
            )
        self.retry_config = retry_config
        self._settings = settings

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def evaluate_and_update(
        self,
        source: "MonitoringSource",
        *,
        run_success: bool,
        error: str | None,
        run_id: uuid.UUID | None = None,
    ) -> HealthEvalResult:
        """Update the source's health fields based on the outcome of a
        CollectorRun and return a HealthEvalResult describing what changed.

        ``source`` is an ORM instance already loaded in the caller's session.
        The audit rows are created via ``audit_repo`` (same session) and the
        deferred events are returned for the caller to publish after commit.
        """
        from app.services.monitoring.retry_backoff import compute_next_run_after_failure

        settings = self._settings
        now = datetime.now(timezone.utc)
        result = HealthEvalResult()

        # Snapshot before-state for the audit trail.
        prev_health = source.health_status
        prev_failures = source.consecutive_failures
        prev_successes = source.consecutive_successes

        if run_success:
            # --- Success path -------------------------------------------------
            source.consecutive_successes += 1
            source.consecutive_failures = 0
            source.last_successful_run_at = now
            source.last_error = None if not error else source.last_error

            recovered = False
            if (
                prev_health == HEALTH_STATUS_DEGRADED
                and source.consecutive_successes >= settings.health_recovery_successes
            ):
                # DEGRADED → HEALTHY recovery transition
                source.health_status = HEALTH_STATUS_HEALTHY
                recovered = True
                result.health_changed = True
                result.recovered = True

                # Emit recovery domain event (deferred — caller publishes after commit)
                recovery_event = DomainEvent(
                    event_type=EventType.SOURCE_RECOVERED,
                    tenant_id=source.tenant_id,
                    payload={
                        "source_id": str(source.id),
                        "source_name": source.name,
                        "consecutive_successes": source.consecutive_successes,
                        "reason": error or "collection succeeded",
                    },
                )
                result.recovery_events.append(recovery_event)

                # Audit: recovery event
                await self.audit_repo.create(
                    tenant_id=source.tenant_id,
                    monitoring_source_id=source.id,
                    collector_run_id=run_id,
                    event_type=SOURCE_HEALTH_EVENT_RECOVERY,
                    reason=(
                        f"Source recovered to HEALTHY after "
                        f"{source.consecutive_successes} consecutive successes."
                    ),
                    from_health_status=prev_health,
                    to_health_status=source.health_status,
                    from_consecutive_failures=prev_failures,
                    to_consecutive_failures=source.consecutive_failures,
                )
                logger.info(
                    "Source %s (id=%s) RECOVERED: HEALTHY after %d consecutive successes.",
                    source.name,
                    source.id,
                    source.consecutive_successes,
                )
            else:
                # No health transition; still log a non-transition audit entry
                # so the trail is complete (helps debugging partial recoveries).
                await self.audit_repo.create(
                    tenant_id=source.tenant_id,
                    monitoring_source_id=source.id,
                    collector_run_id=run_id,
                    event_type=SOURCE_HEALTH_EVENT_TRANSITION,
                    reason=f"Run succeeded; failures reset to 0, successes {prev_successes}→{source.consecutive_successes}.",
                    from_health_status=prev_health,
                    to_health_status=source.health_status,
                    from_consecutive_failures=prev_failures,
                    to_consecutive_failures=source.consecutive_failures,
                )

            # next_run_at: normal poll interval + jitter
            delay = compute_next_run_after_failure(
                consecutive_failures=0,
                config=self.retry_config,
                poll_interval_seconds=source.poll_interval_seconds,
            )
            source.next_run_at = now + timedelta(seconds=delay)

        else:
            # --- Failure path -------------------------------------------------
            source.consecutive_failures += 1
            source.consecutive_successes = 0
            source.last_failed_run_at = now
            if error:
                source.last_error = error

            degraded = False
            if (
                prev_health == HEALTH_STATUS_HEALTHY
                and source.consecutive_failures >= settings.health_degraded_threshold
            ):
                # HEALTHY → DEGRADED transition
                source.health_status = HEALTH_STATUS_DEGRADED
                degraded = True
                result.health_changed = True
                result.degraded = True

                # Emit degraded domain event (deferred)
                from app.services.monitoring.retry_backoff import compute_backoff_delay

                backoff_delay = compute_backoff_delay(
                    attempt=source.consecutive_failures,
                    config=self.retry_config,
                )
                degraded_event = DomainEvent(
                    event_type=EventType.SOURCE_HEALTH_DEGRADED,
                    tenant_id=source.tenant_id,
                    payload={
                        "source_id": str(source.id),
                        "source_name": source.name,
                        "consecutive_failures": source.consecutive_failures,
                        "next_run_delay_seconds": round(backoff_delay, 1),
                        "reason": error or "collection failed",
                    },
                )
                result.recovery_events.append(degraded_event)

                await self.audit_repo.create(
                    tenant_id=source.tenant_id,
                    monitoring_source_id=source.id,
                    collector_run_id=run_id,
                    event_type=SOURCE_HEALTH_EVENT_TRANSITION,
                    reason=(
                        f"Source DEGRADED: {source.consecutive_failures} "
                        f"consecutive failures (threshold={settings.health_degraded_threshold})."
                    ),
                    from_health_status=prev_health,
                    to_health_status=source.health_status,
                    from_consecutive_failures=prev_failures,
                    to_consecutive_failures=source.consecutive_failures,
                )
                logger.warning(
                    "Source %s (id=%s) DEGRADED: %d consecutive failures (threshold=%d).",
                    source.name,
                    source.id,
                    source.consecutive_failures,
                    settings.health_degraded_threshold,
                )
            else:
                # Still failing but not crossing the threshold, or already DEGRADED
                await self.audit_repo.create(
                    tenant_id=source.tenant_id,
                    monitoring_source_id=source.id,
                    collector_run_id=run_id,
                    event_type=SOURCE_HEALTH_EVENT_TRANSITION,
                    reason=f"Run failed; consecutive_failures {prev_failures}→{source.consecutive_failures}.",
                    from_health_status=prev_health,
                    to_health_status=source.health_status,
                    from_consecutive_failures=prev_failures,
                    to_consecutive_failures=source.consecutive_failures,
                )

            # next_run_at: exponential backoff
            delay = compute_next_run_after_failure(
                consecutive_failures=source.consecutive_failures,
                config=self.retry_config,
                poll_interval_seconds=source.poll_interval_seconds,
            )
            source.next_run_at = now + timedelta(seconds=delay)

        # Also update the legacy last_sync_at for backward compat.
        source.last_sync_at = now
        source.status = "connected" if run_success else "error"

        # Flush source changes so they're visible to audit/repo writes
        # that may happen in the same session before the caller commits.
        await self.source_repo.db.flush()

        return result

    # ------------------------------------------------------------------
    # Operational audit helpers (called from jobs.py / orchestrator)
    # ------------------------------------------------------------------

    async def log_timeout(
        self,
        source: "MonitoringSource",
        *,
        timeout_seconds: int,
        run_id: uuid.UUID | None = None,
    ) -> None:
        """Record a timeout event in the audit trail."""
        from app.models.source_health_audit import SOURCE_HEALTH_EVENT_TIMEOUT

        await self.audit_repo.create(
            tenant_id=source.tenant_id,
            monitoring_source_id=source.id,
            collector_run_id=run_id,
            event_type=SOURCE_HEALTH_EVENT_TIMEOUT,
            reason=f"Collection timed out after {timeout_seconds}s.",
        )
        logger.warning(
            "Source %s (id=%s) collection timed out after %ds.",
            source.name,
            source.id,
            timeout_seconds,
        )

    async def log_rate_limited(
        self,
        source: "MonitoringSource",
        *,
        run_id: uuid.UUID | None = None,
    ) -> None:
        """Record a rate-limited skip event in the audit trail."""
        from app.models.source_health_audit import SOURCE_HEALTH_EVENT_RATE_LIMITED

        await self.audit_repo.create(
            tenant_id=source.tenant_id,
            monitoring_source_id=source.id,
            collector_run_id=run_id,
            event_type=SOURCE_HEALTH_EVENT_RATE_LIMITED,
            reason="Source skipped: rate limit exceeded.",
        )
        logger.info(
            "Source %s (id=%s) skipped: rate limit exceeded.",
            source.name,
            source.id,
        )

    async def log_retry(
        self,
        source: "MonitoringSource",
        *,
        attempt: int,
        error: str | None = None,
        run_id: uuid.UUID | None = None,
    ) -> None:
        """Record a retry-attempt event in the audit trail."""
        from app.models.source_health_audit import SOURCE_HEALTH_EVENT_RETRY

        reason = f"Retry attempt {attempt} scheduled."
        if error:
            reason = f"Retry attempt {attempt} scheduled after error: {error}"

        await self.audit_repo.create(
            tenant_id=source.tenant_id,
            monitoring_source_id=source.id,
            collector_run_id=run_id,
            event_type=SOURCE_HEALTH_EVENT_RETRY,
            reason=reason,
        )
        logger.info(
            "Source %s (id=%s) retry attempt %d scheduled.",
            source.name,
            source.id,
            attempt,
        )

    async def log_duplicate_skip(
        self,
        source: "MonitoringSource",
        *,
        run_id: uuid.UUID | None = None,
    ) -> None:
        """Record a duplicate-skip event in the audit trail."""
        from app.models.source_health_audit import SOURCE_HEALTH_EVENT_DUPLICATE_SKIPPED

        await self.audit_repo.create(
            tenant_id=source.tenant_id,
            monitoring_source_id=source.id,
            collector_run_id=run_id,
            event_type=SOURCE_HEALTH_EVENT_DUPLICATE_SKIPPED,
            reason="Source skipped: already scheduled/queued within this tick.",
        )
