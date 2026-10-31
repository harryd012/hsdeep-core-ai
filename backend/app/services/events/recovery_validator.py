"""
Recovery validation for the autonomous lifecycle (VALIDATION_STARTED).

The orchestrator publishes VALIDATION_STARTED after an SOP completes and only
resolves the ticket and incident on VALIDATION_PASSED. Nothing used to consume
VALIDATION_STARTED, so automated remediation could never be verified. This
consumer decides PASSED or FAILED from fresh telemetry, never from the SOP's
own success flag:

  - evidence must be newer than the moment validation started (the end of the
    remediation), via app.services.recovery_check.check_incident_recovery;
  - PASSED needs `consecutive_passes` recovered evaluations in a row (a basic
    stability window), re-evaluating every `interval_seconds`;
  - a failing check, or running out of `attempts` while still inconclusive,
    publishes VALIDATION_FAILED with the checks as evidence, which the
    orchestrator turns into an engineer escalation. The incident is never
    closed without a pass.

Each validation runs in its own task so a multi-minute observation window does
not hold up the event bus (which awaits every handler of an event). The tasks
live in this process only: a restart drops in-flight validations, which leaves
the incident open rather than wrongly resolved.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any, Awaitable, Callable

from app.core.db import AsyncSessionLocal
from app.services.events.event_bus import DomainEvent, EventType
from app.services.recovery_check import (
    RecoveryCheckResult,
    RecoveryIncidentNotFound,
    check_incident_recovery,
)

logger = logging.getLogger(__name__)

AsyncSessionFactory = Callable[[], Any]
Sleep = Callable[[float], Awaitable[None]]


def _evidence(result: RecoveryCheckResult) -> dict[str, Any]:
    return {
        "verdict": result.verdict,
        "since": result.since.isoformat(),
        "evaluated_at": result.evaluated_at.isoformat(),
        "checks": [
            {"kind": c.kind, "id": str(c.id), "name": c.name, "result": c.result, "detail": c.detail}
            for c in result.checks
        ],
    }


class RecoveryValidator:
    def __init__(
        self,
        event_bus: Any,
        *,
        session_factory: AsyncSessionFactory = AsyncSessionLocal,
        attempts: int = 10,
        interval_seconds: float = 60.0,
        consecutive_passes: int = 2,
        sleep: Sleep = asyncio.sleep,
    ):
        self.event_bus = event_bus
        self._session_factory = session_factory
        self._attempts = max(1, attempts)
        self._interval = interval_seconds
        self._passes_needed = max(1, consecutive_passes)
        self._sleep = sleep
        self._tasks: set[asyncio.Task] = set()

    def register(self) -> None:
        self.event_bus.subscribe(EventType.VALIDATION_STARTED)(self._on_validation_started)

    async def _on_validation_started(self, event: DomainEvent) -> None:
        task = asyncio.create_task(self.validate(event))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def wait_idle(self) -> None:
        """Wait for in-flight validations (tests, graceful shutdown)."""
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def validate(self, event: DomainEvent) -> str:
        """Run the observation window; publish and return the outcome."""
        payload = event.payload
        incident_id = payload.get("incident_id")
        if not incident_id:
            return await self._publish(event, passed=False, reason="No incident to verify recovery against.")

        since: datetime = event.timestamp
        passes = 0
        result: RecoveryCheckResult | None = None
        for attempt in range(1, self._attempts + 1):
            try:
                async with self._session_factory() as db:
                    result = await check_incident_recovery(
                        db, uuid.UUID(str(incident_id)), event.tenant_id, since=since
                    )
            except RecoveryIncidentNotFound:
                return await self._publish(event, passed=False, reason="Incident not found for this tenant.")

            if result.verdict == "not_recovered":
                failing = [c.detail for c in result.checks if c.result == "fail"]
                return await self._publish(
                    event, passed=False, result=result,
                    reason="Telemetry does not show recovery: " + "; ".join(failing[:5]),
                )
            passes = passes + 1 if result.verdict == "recovered" else 0
            if passes >= self._passes_needed:
                return await self._publish(event, passed=True, result=result)
            if attempt < self._attempts:
                await self._sleep(self._interval)

        return await self._publish(
            event, passed=False, result=result,
            reason=(
                f"Recovery not verified after {self._attempts} checks: "
                "fresh telemetry did not confirm recovery."
            ),
        )

    async def _publish(
        self,
        event: DomainEvent,
        *,
        passed: bool,
        reason: str | None = None,
        result: RecoveryCheckResult | None = None,
    ) -> str:
        payload = {
            "ticket_id": event.payload.get("ticket_id"),
            "incident_id": event.payload.get("incident_id"),
            "sop_id": event.payload.get("sop_id"),
            "evidence": _evidence(result) if result is not None else None,
        }
        if not passed:
            payload["failure_reason"] = reason
        await self.event_bus.publish(DomainEvent(
            event_type=EventType.VALIDATION_PASSED if passed else EventType.VALIDATION_FAILED,
            tenant_id=event.tenant_id,
            correlation_id=event.correlation_id,
            payload=payload,
        ))
        outcome = "passed" if passed else "failed"
        logger.info("Recovery validation %s for incident %s", outcome, event.payload.get("incident_id"))
        return outcome
