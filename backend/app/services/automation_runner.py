"""
Automation Runner — Sprint Next: Close the Automation Loop, STAGE 2.

Event listener for ``SOP_STARTED``. Loads the SOP's current version steps,
normalises the ``type`` field (auto / manual / gated — defaults to
``manual`` when unset, per the sprint brief), and executes them:

- **auto** steps  → dispatched to a pluggable ``StepHandler`` (a
  ``MockStepHandler`` logs and returns success/fail by default; real
  device-integrations — PRTG/Meraki/Kaseya/FortiGate — are swapped in
  later without touching the wiring).
- **gated** steps  → an approval request is recorded on the
  ``SOPExecution`` row (status → ``awaiting_approval``); the runner
  pauses and waits for an external ``approve`` / ``deny`` call before
  resuming.
- **manual** steps → the runner pauses (status → ``awaiting_manual``),
  notifies the assigned engineer, and waits for an external
  ``complete_step`` call to resume.

On all steps succeeding, ``SOP_COMPLETED`` is published with
``ticket_id``, ``incident_id``, and ``sop_id`` so downstream validation
and closure flows continue.

Session strategy mirrors RCAService: a fresh ``AsyncSession`` per event
via the injected ``session_factory``, so the handler sees rows committed
after startup and a failed run rolls back cleanly instead of leaking a
half-dirty transaction into the next incident's handler.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from sqlalchemy import select

from app.core.db import AsyncSessionLocal
from app.actions.base import DeviceActionType
from app.services.device_action_service import DeviceActionService, DeviceActionError
from app.models.device_action import DeviceActionRequest
from app.models.sop import SOP, SOPVersion
from app.models.sop_execution import SOPExecution
from app.repositories.sop_execution_repository import SOPExecutionRepository
from app.repositories.sop_repository import SOPRepository
from app.services.events.event_bus import EventBus, EventType, DomainEvent, get_event_bus

logger = logging.getLogger(__name__)

# A session factory returns an async context manager yielding an AsyncSession
# (AsyncSessionLocal is one such factory). Mirrors AutonomousLifecycleOrchestrator's
# AsyncSessionFactory.
AsyncSessionFactory = Callable[[], Any]

# Valid step types after normalisation
_STEP_TYPES = {"auto", "manual", "gated"}
# Terminal execution statuses
_TERMINAL_STATUSES = {"succeeded", "failed", "abandoned"}
# Non-terminal (pausable) statuses — execution can be resumed from these
_PAUSABLE_STATUSES = {"awaiting_manual", "awaiting_approval"}

# DeviceActionRequest.status values that count as a completed, successful
# action when a paused execution resumes after an approval. "pending" counts
# as in-flight success, mirroring DatabaseStepHandler's StepResult mapping.
_ACTION_OK_STATUSES = {"verified", "succeeded", "pending"}


# ---------------------------------------------------------------------------
# Step handler protocol & default mock implementation
# ---------------------------------------------------------------------------

@dataclass
class StepResult:
    """Result of executing a single SOP step."""
    success: bool
    status: str = "succeeded"
    output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class StepHandler(Protocol):
    """Pluggable interface for executing "auto" SOP steps.

    A real implementation would call device APIs (PRTG/Meraki/Kaseya/
    FortiGate). The default ``MockStepHandler`` logs the step and returns
    success — enough to exercise the full event chain before real
    integrations exist.
    """

    async def execute(self, step: dict[str, Any], context: dict[str, Any]) -> StepResult: ...


class MockStepHandler:
    """Test double for the automation runner.

    Logs and returns success for every auto step. Steps whose ``action``
    contains ``"fail"`` (case-insensitive) return failure, so failure
    scenarios are testable without a real device or database.

    This handler is intentionally simple and MUST NOT be used in production
    code paths. Use DatabaseStepHandler for real execution.
    """

    async def execute(self, step: dict[str, Any], context: dict[str, Any]) -> StepResult:
        action = str(step.get("action", "")).strip()
        if not action:
            return StepResult(success=False, status="failed",
                              error="Workflow step missing required 'action' field.")
        if "fail" in action.lower():
            return StepResult(success=False, status="failed",
                              error=f"Mock failure for action '{action}'")
        return StepResult(success=True, status="succeeded",
                          output={"action": action, "message": f"Mock execution of '{action}' succeeded"})

# ---------------------------------------------------------------------------
# Production StepHandler - bridges the automation runner to the
# DeviceActionService (the single controlled execution boundary).
# ---------------------------------------------------------------------------


class DatabaseStepHandler:
    """Real StepHandler: every "auto" workflow step becomes a
    DeviceActionRequest through the existing Action Plane.

    Production replacement for MockStepHandler. The AutomationRunner
    (session_factory path) uses this handler by default; tests may inject
    MockStepHandler as a legitimate isolated test double.

    The handler NEVER calls providers directly. It calls
    DeviceActionService.request_action(), which enforces RBAC, tenant
    isolation, policy, approval, provider dispatch, verification, and audit.
    """

    def __init__(self, session_factory, actor_id=None):
        self._session_factory = session_factory
        self._actor_id = actor_id

    async def execute(self, step, context):
        """Execute one workflow "auto" step through DeviceActionService.

        Parameters
        ----------
        step: dict
            Workflow step with at minimum ``action`` and ``target_device_id``.
            ``action`` is a DeviceActionType value string (e.g. ``PING``,
            ``PRTG_QUERY_SENSOR``, ``REBOOT_DEVICE``).
        context: dict
            Execution context from the AutomationRunner: ``tenant_id``,
            ``correlation_id``, optional ``incident_id``, ``ticket_id``,
            ``sop_id``, and optional ``actor_id``.
        """
        action_name = str(step.get("action", "")).strip()
        if not action_name:
            return StepResult(
                success=False, status="failed",
                error="Workflow step missing required 'action' field.",
            )

        try:
            action_type = DeviceActionType(action_name)
        except ValueError:
            return StepResult(
                success=False, status="failed",
                error=f"Unknown action {action_name!r}. Use GET /api/device-actions/catalog.",
            )

        try:
            async with self._session_factory() as db:
                service = DeviceActionService(db)
                actor = self._actor_id
                if actor is None:
                    actor_str = context.get("actor_id")
                    if actor_str is not None:
                        try:
                            actor = uuid.UUID(str(actor_str))
                        except (ValueError, TypeError):
                            actor = None
                request = await service.request_action(
                    tenant_id=uuid.UUID(str(context["tenant_id"])),
                    device_id=uuid.UUID(str(step["target_device_id"])),
                    action=action_type.value,
                    params=step.get("parameters"),
                    requested_by=actor,
                    incident_id=(
                        uuid.UUID(str(context["incident_id"]))
                        if context.get("incident_id") else None
                    ),
                    motivation={
                        "source": "automation_step",
                        "step_action": action_name,
                        "step_description": str(step.get("description", "") or ""),
                        "correlation_id": str(context.get("correlation_id", "")),
                    },
                    correlation_id=(str(context.get("correlation_id") or "") or None),
                )
                await db.commit()
        except DeviceActionError as exc:
            return StepResult(success=False, status="failed", error=str(exc))
        except KeyError as exc:
            return StepResult(
                success=False, status="failed",
                error=f"Workflow step missing required field {exc.args[0]!r}.",
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("DatabaseStepHandler.execute failed: %s", exc, exc_info=True)
            return StepResult(
                success=False, status="failed",
                error=f"Automation execution error: {type(exc).__name__}.",
            )

        # Map DeviceActionRequest.status -> StepResult.
        status = request.status
        if status == "verified":
            return StepResult(
                success=True, status="succeeded",
                output={
                    "action": action_name,
                    "request_id": str(request.id),
                    "correlation_id": request.correlation_id,
                    "result": request.result or {},
                },
            )
        elif status == "policy_denied":
            return StepResult(
                success=False, status="policy_denied",
                error=request.error or "Action denied by policy.",
                output={
                    "action": action_name,
                    "request_id": str(request.id),
                    "correlation_id": request.correlation_id,
                    "policy_reason": (request.policy_decision or {}).get("reason"),
                },
            )
        elif status == "awaiting_approval":
            return StepResult(
                success=False, status="awaiting_approval",
                error="Action requires human approval before execution.",
                output={
                    "action": action_name,
                    "request_id": str(request.id),
                    "correlation_id": request.correlation_id,
                    "approval_request_id": (
                        str(request.approval_request_id)
                        if request.approval_request_id else None
                    ),
                    "classification": request.classification,
                },
            )
        elif status == "pending":
            return StepResult(
                success=True, status="pending",
                output={
                    "action": action_name,
                    "request_id": str(request.id),
                    "correlation_id": request.correlation_id,
                    "result": request.result or {},
                },
            )
        else:
            return StepResult(
                success=False, status=status,
                error=request.error or f"Action {action_name} ended with status {status}.",
                output={
                    "action": action_name,
                    "request_id": str(request.id),
                    "correlation_id": request.correlation_id,
                    "result": request.result or {},
                },
            )

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return steps with a normalised ``type`` field.

    Per the sprint brief: if SOP steps don't carry a ``type`` tag, default
    to ``"manual"`` so operators aren't silently auto-remediated. This is
    applied at read-time so existing rows don't need a data migration.
    """
    normalized: list[dict[str, Any]] = []
    for i, step in enumerate(steps):
        s = dict(step)
        s["order"] = s.get("order", i + 1)
        step_type = s.get("type", "manual")
        if step_type not in _STEP_TYPES:
            logger.warning("Unknown step type '%s' for order=%s, defaulting to 'manual'", step_type, s["order"])
            step_type = "manual"
        s["type"] = step_type
        normalized.append(s)
    return normalized


# ---------------------------------------------------------------------------
# The event-listener service
# ---------------------------------------------------------------------------

class AutomationRunner:
    """Event-driven automation runner subscribed to ``SOP_STARTED``.

    Loads the selected SOP's current-version steps, executes "auto" steps
    via an injected ``StepHandler``, pauses on "manual"/"gated" steps
    (recording state in a ``SOPExecution`` row), and publishes
    ``SOP_COMPLETED`` when all steps succeed.

    Paused executions can be resumed via :meth:`resume_manual_step` and
    :meth:`approve_gated_step`, which can be called from API endpoints or
    background tasks.
    """

    def __init__(
        self,
        event_bus: EventBus,
        sop_repo: SOPRepository | None = None,
        execution_repo: SOPExecutionRepository | None = None,
        step_handler: StepHandler | None = None,
        *,
        session_factory: AsyncSessionFactory | None = None,
    ):
        if session_factory is None and (sop_repo is None or execution_repo is None):
            raise ValueError(
                "AutomationRunner requires either session_factory (fresh session per event) "
                "or both sop_repo and execution_repo"
            )
        self.event_bus = event_bus
        self._sop_repo = sop_repo
        self._execution_repo = execution_repo
        self._session_factory = session_factory
        if step_handler is not None:
            self._step_handler: StepHandler = step_handler
        elif session_factory is not None:
            self._step_handler = DatabaseStepHandler(session_factory)
        else:
            self._step_handler = MockStepHandler()

    def register(self) -> None:
        """Subscribe this service's handler to SOP_STARTED."""
        self.event_bus.subscribe(EventType.SOP_STARTED)(self._on_sop_started)

    # -- Event handler -------------------------------------------------------

    async def _on_sop_started(self, event: DomainEvent) -> None:
        """Handle SOPStarted → execute steps → publish SOP_COMPLETED (on success)."""
        ticket_id = event.payload.get("ticket_id")
        sop_id = event.payload.get("sop_id")
        incident_id = event.payload.get("incident_id")

        if not ticket_id or not sop_id:
            logger.warning("SOPStarted event missing ticket_id or sop_id")
            return

        try:
            if self._session_factory is not None:
                async with self._session_factory() as db:
                    await self._run_automation(
                        event=event,
                        ticket_id=ticket_id,
                        incident_id=incident_id,
                        sop_id=sop_id,
                        sop_repo=SOPRepository(db),
                        execution_repo=SOPExecutionRepository(db),
                    )
            else:
                await self._run_automation(
                    event=event,
                    ticket_id=ticket_id,
                    incident_id=incident_id,
                    sop_id=sop_id,
                    sop_repo=self._sop_repo,  # type: ignore[arg-type]
                    execution_repo=self._execution_repo,  # type: ignore[arg-type]
                )
        except Exception as exc:
            logger.error("Automation runner failed for SOP %s: %s", sop_id, exc, exc_info=True)
            # Record the failure on the execution record if it exists
            await self._record_failure(event, sop_id, ticket_id, incident_id, str(exc))

    # -- Core automation logic (shared by both session paths) ----------------

    async def _run_automation(
        self,
        *,
        event: DomainEvent,
        ticket_id: str,
        incident_id: str | None,
        sop_id: str,
        sop_repo: SOPRepository,
        execution_repo: SOPExecutionRepository,
    ) -> None:
        """Load the SOP, create an execution record, and execute steps."""
        sop = await sop_repo.get(uuid.UUID(sop_id), event.tenant_id)
        if sop is None:
            logger.error("SOP %s not found for automation", sop_id)
            return

        version: SOPVersion | None = sop.current_version
        if version is None or not version.steps:
            logger.warning("SOP %s has no current version or no steps — treating as no-op automation", sop_id)
            await self._publish_completed(event, ticket_id, incident_id, sop_id, success=True, notes="No steps to execute")
            return

        steps = _normalize_steps(version.steps)

        # Create the execution record
        execution = await execution_repo.create(
            tenant_id=event.tenant_id,
            sop_id=sop.id,
            ticket_id=uuid.UUID(ticket_id) if ticket_id else None,
            incident_id=uuid.UUID(incident_id) if incident_id else None,
            correlation_id=event.correlation_id,
            status="running",
            current_step_index=0,
            step_results=[],
            started_at=datetime.now(timezone.utc),
        )
        await execution_repo.db.flush()

        # Process all "auto" steps until we hit a manual/gated step or finish
        rollback_required = False
        result_notes_parts: list[str] = []

        for i, step in enumerate(steps):
            if step["type"] == "auto":
                context = {
                    "incident_id": incident_id,
                    "ticket_id": ticket_id,
                    "sop_id": sop_id,
                    "tenant_id": str(event.tenant_id),
                    "correlation_id": event.correlation_id,
                }
                result = await self._step_handler.execute(step, context)

                if not result.success and result.status == "awaiting_approval":
                    # Device-action approval pending — a PAUSE, not a
                    # failure. The execution stays on this step and resumes
                    # via resume_after_device_action_decision() when the
                    # approval is resolved through the device-actions API.
                    output = dict(result.output or {})
                    execution.current_step_index = i
                    execution.status = "awaiting_approval"
                    await execution_repo.record_step_result(
                        execution, step_index=i, status="waiting_approval",
                        output={
                            **output,
                            "requires_approval": True,
                            "device_action_request_id": output.get("request_id"),
                        },
                    )
                    await execution_repo.db.commit()
                    logger.info(
                        "SOP %s execution paused at auto step %d — device action awaiting approval",
                        sop_id, i + 1,
                    )
                    await self.event_bus.publish(DomainEvent(
                        event_type=EventType.ENGINEER_ESCALATED,
                        tenant_id=event.tenant_id,
                        correlation_id=event.correlation_id,
                        payload={
                            "ticket_id": ticket_id,
                            "incident_id": incident_id,
                            "sop_execution_id": str(execution.id),
                            "reason": (
                                f"Step {i+1} ({step.get('action')}) requires "
                                "approval before the device action can run"
                            ),
                        },
                    ))
                    return

                if result.success:
                    await execution_repo.record_step_result(
                        execution, step_index=i, status="succeeded", output=result.output or {}
                    )
                    execution.current_step_index = i + 1
                    result_notes_parts.append(f"Step {i+1} ({step.get('action')}): succeeded")
                else:
                    await execution_repo.record_step_result(
                        execution, step_index=i, status="failed", error=result.error
                    )
                    execution.current_step_index = i
                    result_notes_parts.append(f"Step {i+1} ({step.get('action')}): failed — {result.error}")
                    rollback_required = step.get("rollback_procedure", False) or False
                    # Check if SOP has a rollback procedure defined
                    if version.sop.rollback_procedure:
                        rollback_required = True
                    await execution_repo.db.commit()

                    # Step failure → fail the execution, publish SOP_COMPLETED
                    # with success=False so downstream knows it didn't complete.
                    await execution_repo.complete(
                        execution, success=False, result_notes="; ".join(result_notes_parts),
                        rollback_required=rollback_required,
                    )
                    await execution_repo.db.commit()
                    logger.warning("SOP %s automation failed at step %d", sop_id, i + 1)
                    # Publish SOP_COMPLETED with success=False so downstream
                    # validation/CLI can handle the failure path.
                    await self._publish_completed(
                        event, ticket_id, incident_id, sop_id,
                        success=False, notes="; ".join(result_notes_parts),
                    )
                    return

            elif step["type"] == "gated":
                # Pause for approval — record state and return; resume via
                # approve_gated_step() when an operator approves/denies.
                execution.current_step_index = i
                execution.status = "awaiting_approval"
                await execution_repo.record_step_result(
                    execution, step_index=i, status="waiting_approval",
                    output={"action": step.get("action"), "requires_approval": True},
                )
                await execution_repo.db.commit()
                logger.info("SOP %s execution paused at gated step %d — awaiting approval", sop_id, i + 1)
                # Publish an ENGINEER_ESCALATED-style event so the notification
                # listener can alert the approver.
                await self.event_bus.publish(DomainEvent(
                    event_type=EventType.ENGINEER_ESCALATED,
                    tenant_id=event.tenant_id,
                    correlation_id=event.correlation_id,
                    payload={
                        "ticket_id": ticket_id,
                        "incident_id": incident_id,
                        "sop_execution_id": str(execution.id),
                        "reason": f"Approval required for gated SOP step {i+1} ({step.get('action')})",
                    },
                ))
                return

            elif step["type"] == "manual":
                # Pause for manual completion — record state and return;
                # resume via complete_manual_step() when the engineer marks it done.
                execution.current_step_index = i
                execution.status = "awaiting_manual"
                await execution_repo.record_step_result(
                    execution, step_index=i, status="waiting_manual",
                    output={
                        "action": step.get("action"),
                        "assigned_to": str(execution.assigned_to) if execution.assigned_to else None,
                    },
                )
                await execution_repo.db.commit()
                logger.info("SOP %s execution paused at manual step %d — awaiting engineer action", sop_id, i + 1)
                # Notify the assigned engineer
                await self.event_bus.publish(DomainEvent(
                    event_type=EventType.ENGINEER_ESCALATED,
                    tenant_id=event.tenant_id,
                    correlation_id=event.correlation_id,
                    payload={
                        "ticket_id": ticket_id,
                        "incident_id": incident_id,
                        "sop_execution_id": str(execution.id),
                        "reason": f"Manual step {i+1} ({step.get('action')}) requires engineer action",
                    },
                ))
                return

        # All steps executed successfully
        await execution_repo.complete(
            execution, success=True, result_notes="; ".join(result_notes_parts),
            rollback_required=rollback_required,
        )
        await execution_repo.db.commit()
        await self._publish_completed(
            event, ticket_id, incident_id, sop_id,
            success=True, notes="; ".join(result_notes_parts),
        )

    # -- Resume methods (called from API endpoints) --------------------------

    async def approve_gated_step(
        self, execution_id: uuid.UUID, tenant_id: uuid.UUID, *,
        approved: bool, approved_by: uuid.UUID | None, reason: str | None,
    ) -> SOPExecution | None:
        """Approve or deny a gated step, resuming execution.

        Called by the API endpoint ``POST /api/tickets/{ticket_id}/sop-executions/{execution_id}/approve``.
        On denial, the execution is abandoned and ENGINEER_ESCALATED-style
        notification is skipped (the engineer is escalated via the denial itself).
        """
        async with AsyncSessionLocal() as db:
            execution_repo = SOPExecutionRepository(db)
            execution = await execution_repo.get(execution_id, tenant_id)
            if execution is None:
                logger.warning("SOPExecution %s not found for approval", execution_id)
                return None

            if execution.status != "awaiting_approval":
                logger.warning("Cannot approve execution %s: status is %s, expected awaiting_approval",
                               execution_id, execution.status)
                return execution

            if not approved:
                execution.status = "abandoned"
                execution.result_notes = reason or "Gated step denied by approver"
                await db.commit()
                logger.info("SOP execution %s abandoned (gated step denied)", execution_id)
                return execution

            # Approve — record and resume from the current step
            execution.approved_by = approved_by
            execution.status = "running"
            await execution_repo.record_step_result(
                execution, step_index=execution.current_step_index,
                status="approved", output={"approved_by": str(approved_by) if approved_by else None},
            )
            await db.flush()
            # Advance past the approved step and re-run from there
            execution.current_step_index = execution.current_step_index + 1
            await db.commit()

            # Resume the automation loop from the next step
            await self._resume_from(execution, db)
            return execution

    async def complete_manual_step(
        self, execution_id: uuid.UUID, tenant_id: uuid.UUID, *,
        completed_by: uuid.UUID | None, notes: str | None,
    ) -> SOPExecution | None:
        """Mark a manual step as done and resume execution.

        Called by the API endpoint ``POST /api/tickets/{ticket_id}/sop-executions/{execution_id}/complete-step``.
        """
        async with AsyncSessionLocal() as db:
            execution_repo = SOPExecutionRepository(db)
            execution = await execution_repo.get(execution_id, tenant_id)
            if execution is None:
                logger.warning("SOPExecution %s not found for manual completion", execution_id)
                return None

            if execution.status != "awaiting_manual":
                logger.warning("Cannot complete manual step for execution %s: status is %s, expected awaiting_manual",
                               execution_id, execution.status)
                return execution

            await execution_repo.record_step_result(
                execution, step_index=execution.current_step_index,
                status="completed", output={"completed_by": str(completed_by) if completed_by else None, "notes": notes},
            )
            execution.current_step_index = execution.current_step_index + 1
            execution.status = "running"
            await db.commit()

            await self._resume_from(execution, db)
            return execution

    # -- Device-action approval resume (Action Plane bridge) -----------------

    async def resume_after_device_action_decision(
        self,
        device_action_request_id: uuid.UUID,
        tenant_id: uuid.UUID,
        *,
        approved: bool,
        approver_id: uuid.UUID | None = None,
        note: str | None = None,
    ) -> SOPExecution | None:
        """Resume a SOPExecution paused on a device-action approval.

        The counterpart of the pause ``_run_automation`` records when an
        "auto" step dispatched through the Device Action Plane returns
        ``awaiting_approval``: once a human resolves that approval via the
        device-actions approve API, the paused execution continues here.

        ALLOW / MODIFY: the action already executed inside
        ``DeviceActionService.resolve_approval`` — its FINAL status decides
        the step outcome. verified/succeeded/pending → the step succeeded
        and the remaining steps run; anything else → the execution fails
        (a failed action is never reported as a completed step).
        DENY: the execution is abandoned (mirrors the gated-step deny path)
        and SOP_COMPLETED(success=False) is published so the lifecycle
        chain does not hang.

        Returns the resumed execution, or None when no paused execution is
        waiting on this request (e.g. operator-initiated actions).
        """
        async with AsyncSessionLocal() as db:
            execution = await find_execution_waiting_for_device_action(
                db, tenant_id, device_action_request_id,
            )
            if execution is None:
                logger.info(
                    "No SOP execution waiting on device action %s — nothing to resume",
                    device_action_request_id,
                )
                return None

            execution_repo = SOPExecutionRepository(db)
            correlation = execution.correlation_id or str(uuid.uuid4())

            async def _publish_finished(*, success: bool, notes: str | None) -> None:
                await self.event_bus.publish(DomainEvent(
                    event_type=EventType.SOP_COMPLETED,
                    tenant_id=execution.tenant_id,
                    correlation_id=correlation,
                    payload={
                        "ticket_id": str(execution.ticket_id) if execution.ticket_id else None,
                        "incident_id": str(execution.incident_id) if execution.incident_id else None,
                        "sop_id": str(execution.sop_id),
                        "sop_execution_id": str(execution.id),
                        "success": success,
                        "result_notes": notes,
                    },
                ))

            if not approved:
                execution.status = "abandoned"
                execution.result_notes = (
                    note or f"Device action {device_action_request_id} denied by approver"
                )
                execution.completed_at = datetime.now(timezone.utc)
                await db.commit()
                logger.info(
                    "SOP execution %s abandoned — device action denied by approver",
                    execution.id,
                )
                await _publish_finished(success=False, notes=execution.result_notes)
                return execution

            # ALLOW / MODIFY — the action already executed; its final status
            # decides the step outcome.
            request = await db.get(DeviceActionRequest, device_action_request_id)
            final_status = request.status if request is not None else None
            ok = final_status in _ACTION_OK_STATUSES
            await execution_repo.record_step_result(
                execution, step_index=execution.current_step_index,
                status="succeeded" if ok else "failed",
                output={
                    "device_action_request_id": str(device_action_request_id),
                    "final_status": final_status,
                    "approved_by": str(approver_id) if approver_id else None,
                    "approved": True,
                },
                error=None if ok else (
                    note or f"Approved device action ended with status {final_status!r}"
                ),
            )

            if not ok:
                execution.status = "failed"
                execution.result_notes = (
                    f"Approved device action did not complete: {final_status!r}"
                )
                execution.completed_at = datetime.now(timezone.utc)
                await db.commit()
                logger.warning(
                    "SOP execution %s failed — approved device action ended with %r",
                    execution.id, final_status,
                )
                await _publish_finished(success=False, notes=execution.result_notes)
                return execution

            execution.approved_by = approver_id
            execution.current_step_index = execution.current_step_index + 1
            execution.status = "running"
            await db.commit()

            # Continue with the remaining steps (SOP_COMPLETED is published
            # by _resume_from when everything finishes).
            await self._resume_from(execution, db)
            return execution

    async def _resume_from(self, execution: SOPExecution, db) -> None:
        """Resume automation from the current_step_index, re-using the same
        execution record. Publishes SOP_COMPLETED on completion."""
        # We need to re-load the SOP and its version to get the steps
        sop_repo = SOPRepository(db)
        sop = await sop_repo.get(execution.sop_id, execution.tenant_id)
        if sop is None or sop.current_version is None:
            logger.error("Cannot resume SOP execution %s: SOP or version not found", execution.id)
            return

        steps = _normalize_steps(sop.current_version.steps)
        execution_repo = SOPExecutionRepository(db)
        event_bus = self.event_bus

        # Reconstruct a minimal event for publishing
        from app.services.events.event_bus import DomainEvent
        ticket_id = str(execution.ticket_id) if execution.ticket_id else None
        incident_id = str(execution.incident_id) if execution.incident_id else None

        for i in range(execution.current_step_index, len(steps)):
            step = steps[i]
            if step["type"] == "auto":
                context = {
                    "incident_id": incident_id,
                    "ticket_id": ticket_id,
                    "sop_id": str(execution.sop_id),
                    "tenant_id": str(execution.tenant_id),
                    "correlation_id": execution.correlation_id,
                }
                result = await self._step_handler.execute(step, context)
                if result.success:
                    await execution_repo.record_step_result(
                        execution, step_index=i, status="succeeded", output=result.output or {}
                    )
                    execution.current_step_index = i + 1
                    await db.flush()
                else:
                    await execution_repo.record_step_result(
                        execution, step_index=i, status="failed", error=result.error
                    )
                    execution.current_step_index = i
                    await db.commit()
                    await execution_repo.complete(
                        execution, success=False,
                        result_notes=f"Step {i+1} failed: {result.error}",
                    )
                    await db.commit()
                    await event_bus.publish(DomainEvent(
                        event_type=EventType.SOP_COMPLETED,
                        tenant_id=execution.tenant_id,
                        correlation_id=execution.correlation_id or str(uuid.uuid4()),
                        payload={
                            "ticket_id": ticket_id,
                            "incident_id": incident_id,
                            "sop_id": str(execution.sop_id),
                            "sop_execution_id": str(execution.id),
                            "success": False,
                        },
                    ))
                    return
            elif step["type"] in ("manual", "gated"):
                # Hit another pausable step — pause again
                wait_status = "awaiting_approval" if step["type"] == "gated" else "awaiting_manual"
                execution.status = wait_status
                await execution_repo.record_step_result(
                    execution, step_index=i,
                    status="waiting_" + step["type"],
                    output={"action": step.get("action")},
                )
                await db.commit()
                # Re-notify
                await event_bus.publish(DomainEvent(
                    event_type=EventType.ENGINEER_ESCALATED,
                    tenant_id=execution.tenant_id,
                    correlation_id=execution.correlation_id or str(uuid.uuid4()),
                    payload={
                        "ticket_id": ticket_id,
                        "incident_id": incident_id,
                        "sop_execution_id": str(execution.id),
                        "reason": f"Step {i+1} ({step.get('action')}) requires {'approval' if step['type'] == 'gated' else 'manual action'}",
                    },
                ))
                return

        # All steps done
        await execution_repo.complete(
            execution, success=True,
            result_notes="All steps completed after resume",
        )
        await db.commit()
        await event_bus.publish(DomainEvent(
            event_type=EventType.SOP_COMPLETED,
            tenant_id=execution.tenant_id,
            correlation_id=execution.correlation_id or str(uuid.uuid4()),
            payload={
                "ticket_id": ticket_id,
                "incident_id": incident_id,
                "sop_id": str(execution.sop_id),
                "sop_execution_id": str(execution.id),
                "success": True,
            },
        ))

    # -- Helpers -------------------------------------------------------------

    async def _publish_completed(
        self, event: DomainEvent, ticket_id: str, incident_id: str | None,
        sop_id: str, *, success: bool, notes: str | None = None,
    ) -> None:
        await self.event_bus.publish(DomainEvent(
            event_type=EventType.SOP_COMPLETED,
            tenant_id=event.tenant_id,
            correlation_id=event.correlation_id,
            payload={
                "ticket_id": ticket_id,
                "incident_id": incident_id,
                "sop_id": str(sop_id),
                "success": success,
                "result_notes": notes,
            },
        ))

    async def _record_failure(
        self, event: DomainEvent, sop_id: str, ticket_id: str,
        incident_id: str | None, error: str,
    ) -> None:
        """Best-effort recording of a failure on the SOPExecution row."""
        try:
            async with AsyncSessionLocal() as db:
                execution_repo = SOPExecutionRepository(db)
                # Find any in-progress execution for this ticket+sop
                from sqlalchemy import select
                stmt = select(SOPExecution).where(
                    SOPExecution.sop_id == uuid.UUID(sop_id),
                    SOPExecution.ticket_id == uuid.UUID(ticket_id),
                    SOPExecution.tenant_id == event.tenant_id,
                    SOPExecution.is_deleted.is_(False),
                    SOPExecution.status.in_("running", "awaiting_manual", "awaiting_approval"),
                )
                result = await db.execute(stmt)
                execution = result.scalar_one_or_none()
                if execution:
                    execution.status = "failed"
                    execution.result_notes = f"Automation runner error: {error}"
                    execution.completed_at = datetime.now(timezone.utc)
                    await db.commit()
        except Exception:
            logger.debug("Failed to record SOPExecution failure: %s", error, exc_info=True)


# ---------------------------------------------------------------------------
# Device-action approval resume — public API for the approve route
# ---------------------------------------------------------------------------

async def find_execution_waiting_for_device_action(
    db, tenant_id: uuid.UUID, device_action_request_id: uuid.UUID,
) -> SOPExecution | None:
    """Find the paused SOPExecution whose current step waits on this
    DeviceActionRequest's approval.

    Candidate rows are tenant-scoped, awaiting_approval and not deleted (SQL
    WHERE); the precise link is the ``device_action_request_id`` recorded in
    the paused step's ``step_results`` output by ``_run_automation``.
    """
    stmt = select(SOPExecution).where(
        SOPExecution.tenant_id == tenant_id,
        SOPExecution.status == "awaiting_approval",
        SOPExecution.is_deleted.is_(False),
    )
    result = await db.execute(stmt)
    wanted = str(device_action_request_id)
    for execution in result.scalars():
        for entry in execution.step_results or []:
            output = entry.get("output") or {}
            if output.get("device_action_request_id") == wanted:
                return execution
    return None


async def resume_after_device_action_decision(
    device_action_request_id: uuid.UUID,
    tenant_id: uuid.UUID,
    *,
    approved: bool,
    approver_id: uuid.UUID | None = None,
    note: str | None = None,
) -> SOPExecution | None:
    """Module-level entry point for the device-actions approve API.

    Boots a runner on the global event-bus singleton with a fresh session
    per call, so HTTP callers never need to hold an AutomationRunner
    instance. Returns None when nothing was waiting on this approval.
    """
    runner = AutomationRunner(
        event_bus=get_event_bus(), session_factory=AsyncSessionLocal,
    )
    return await runner.resume_after_device_action_decision(
        device_action_request_id, tenant_id,
        approved=approved, approver_id=approver_id, note=note,
    )
