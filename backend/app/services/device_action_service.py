"""
DeviceActionService - the Action Orchestrator (Phase 1).

Owns the full request lifecycle:

    request -> policy evaluate -> (auto-execute | approval) -> execute
            -> verify -> audit

REUSE notes:
  * Approvals ride the EXISTING ApprovalRequest/ApprovalHistory tables -
    no second approval engine.
  * Execution audit rides the EXISTING ExecutionJob/ExecutionLog tables
    (source_type="device_action") - no second execution engine.
  * Credentials are decrypted per-execution via the existing
    decrypt_connection_info seam and never leave this process: not in
    results, not in audit context, never in API responses.
  * The caller owns the commit (same convention as every other service).
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

import app.actions  # noqa: F401 - registers the built-in providers
from app.actions.base import (
    ACTION_CATALOG,
    ActionNotSupported,
    ActionProviderRegistry,
    ActionTarget,
    DeviceActionType,
)
from app.actions.policy import PolicyDecision, evaluate_action_policy
from app.actions.verification import plan_for, run_verification
from app.core.security import decrypt_connection_info
from app.models.device import Device
from app.models.device_action import DeviceActionRequest
from app.models.execution import ExecutionJob, ExecutionLog
from app.models.monitoring_source import MonitoringSource
from app.repositories.approval_repository import ApprovalRequestRepository
from app.repositories.device_action_repository import DeviceActionRequestRepository
from app.repositories.device_capability_repository import (
    CAPABILITY_FLAGS,
    DeviceCapabilityRepository,
    derive_capabilities,
)
from app.repositories.device_repository import DeviceRepository
from app.repositories.maintenance_window_repository import MaintenanceWindowRepository
from app.repositories.monitoring_source_repository import MonitoringSourceRepository
from app.services.audit_service import AuditService

logger = logging.getLogger(__name__)


class DeviceActionError(Exception):
    """Base error mapped to a 4xx/5xx by the route layer."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class DeviceActionNotFound(DeviceActionError):
    def __init__(self, message: str = "Device action request not found"):
        super().__init__(message, status_code=404)


class ApprovalDecisionError(DeviceActionError):
    """Invalid approval decision or state transition attempt."""

    def __init__(self, message: str):
        super().__init__(message, status_code=409)


VALID_DECISIONS = frozenset({"ALLOW", "DENY", "MODIFY"})


class DeviceActionService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.requests = DeviceActionRequestRepository(db)
        self.capabilities = DeviceCapabilityRepository(db)
        self.devices = DeviceRepository(db)
        self.sources = MonitoringSourceRepository(db)
        self.windows = MaintenanceWindowRepository(db)
        self.approvals = ApprovalRequestRepository(db)
        self.audit = AuditService(db)

    # ------------------------------------------------------------------
    # Catalog & capabilities (read paths)
    # ------------------------------------------------------------------

    @staticmethod
    def catalog() -> list[dict]:
        from app.actions.policy import catalog_for_api
        return catalog_for_api()

    async def get_capabilities(self, tenant_id: uuid.UUID, device_id: uuid.UUID) -> dict:
        device = await self._get_device(tenant_id, device_id)
        source = await self._get_source(device)
        explicit = await self.capabilities.get_for_device(device_id, tenant_id)
        resolved = self._resolved_capability_map(device, source, explicit)
        return {
            "device_id": device_id,
            "source": explicit.source if explicit else "derived",
            "capabilities": resolved,
            "firmware_version": explicit.firmware_version if explicit else (device.os_version or None),
            "management_address": (explicit.management_address if explicit else None)
            or device.ip_address or device.hostname,
        }

    # ------------------------------------------------------------------
    # Request -> policy -> (auto-execute | approval)
    # ------------------------------------------------------------------

    async def request_action(
        self,
        *,
        tenant_id: uuid.UUID,
        device_id: uuid.UUID,
        action: str,
        params: dict | None = None,
        requested_by: uuid.UUID | None = None,
        incident_id: uuid.UUID | None = None,
        motivation: dict | None = None,
        correlation_id: str | None = None,
    ) -> DeviceActionRequest:
        """Request a device action.

        ``correlation_id`` (optional) lets a caller that already has an
        end-to-end correlation identifier - e.g. the automation/SOP runner -
        propagate it into the request so the workflow trace, the action
        request, the execution audit and the verification all share ONE id.
        When omitted a fresh id is generated (existing API behaviour,
        unchanged).
        """
        try:
            action_type = DeviceActionType(action)
        except ValueError as exc:
            raise DeviceActionError(
                f"Unknown action {action!r}. Use GET /api/device-actions/catalog."
            ) from exc
        spec = ACTION_CATALOG[action_type]

        # Idempotent replay (Part 5): when the caller supplies a correlation id it
        # IS the request identity, so a repeated request must not create a second
        # action request and must not execute an external action twice. The
        # lookup is tenant scoped, so a correlation id belonging to another
        # tenant is invisible here and can never adopt another tenant's request.
        if correlation_id is not None:
            existing = await self.requests.get_by_correlation_id(
                correlation_id, tenant_id
            )
            if existing is not None:
                return existing

        device = await self._get_device(tenant_id, device_id)
        source = await self._get_source(device)
        explicit = await self.capabilities.get_for_device(device_id, tenant_id)
        capability_map = self._resolved_capability_map(device, source, explicit)

        # Maintenance windows scoped to this device (existing repository).
        now = datetime.now(timezone.utc)
        active_windows = await self.windows.list_active_for_scope(
            tenant_id, site_id=device.site_id, device_id=device.id, at=now
        )

        decision = evaluate_action_policy(
            action=action_type,
            capabilities=capability_map,
            maintenance_windows=[
                {"starts_at": w.starts_at, "ends_at": w.ends_at} for w in active_windows
            ],
            now=now,
        )

        request = await self.requests.create(
            tenant_id=tenant_id,
            device_id=device_id,
            action=action_type.value,
            classification=spec.classification.value,
            provider_kind=spec.provider_kind,
            params=self._sanitize_params(params or {}),
            motivation=motivation or {},
            policy_decision=self._decision_snapshot(decision),
            status=decision.status,
            requested_by=requested_by,
            correlation_id=correlation_id or uuid.uuid4().hex,
            incident_id=incident_id,
        )

        await self.audit.record(
            tenant_id=tenant_id,
            action="device_action.requested",
            actor_id=requested_by,
            entity_type="device_action_request",
            entity_id=request.id,
            description=f"{action_type.value} on device {device.name}",
            context=self._json_safe({
                "correlation_id": request.correlation_id,
                "action": action_type.value,
                "classification": spec.classification.value,
                "device_id": str(device_id),
                "policy_reason": decision.reason,
            }),
        )

        if not decision.allowed:
            return request  # terminal: policy_denied

        if decision.requires_approval:
            await self._create_approval(request, decision, requested_by)
            return request

        # READ_ONLY auto-execution path.
        return await self._execute(request, device=device, source=source)

    # ------------------------------------------------------------------
    # Human approval resolution (ALLOW / DENY / MODIFY)
    # ------------------------------------------------------------------

    async def resolve_approval(
        self,
        *,
        tenant_id: uuid.UUID,
        request_id: uuid.UUID,
        decision: str,
        approver_id: uuid.UUID | None,
        approver_name: str | None,
        note: str | None = None,
        modified_params: dict | None = None,
    ) -> DeviceActionRequest:
        if decision not in VALID_DECISIONS:
            raise DeviceActionError(
                f"Invalid decision {decision!r}; expected one of {sorted(VALID_DECISIONS)}."
            )
        request = await self.requests.get(request_id, tenant_id)
        if request is None:
            raise DeviceActionNotFound()
        if request.status != "awaiting_approval":
            raise ApprovalDecisionError(
                f"Request is {request.status!r}, not awaiting approval."
            )

        # CLAIM: a single conditional UPDATE decides the winner atomically. Two
        # approvers acting at the same time can no longer both pass the check
        # above and both execute an infrastructure action - the loser is refused
        # here, before any external call is made.
        claim_target = "denied" if decision == "DENY" else "executing"
        claimed = await self.requests.claim_status(
            request_id,
            tenant_id,
            from_status="awaiting_approval",
            to_status=claim_target,
        )
        if not claimed:
            raise ApprovalDecisionError(
                "This request was already decided by another approval."
            )

        approval = None
        if request.approval_request_id is not None:
            approval = await self.approvals.get(request.approval_request_id, tenant_id)

        if decision == "DENY":
            request.status = "denied"
            request.resolved_by = approver_id
            request.resolved_at = datetime.now(timezone.utc)
            request.resolution_note = note
            if approval is not None:
                await self.approvals.resolve(
                    approval, status="denied", resolved_by=approver_id, note=note
                )
                await self.approvals.record_history(
                    approval=approval, actor_id=approver_id, actor_name=approver_name,
                    action_type="denied", note=note,
                )
            await self.audit.record(
                tenant_id=tenant_id, action="device_action.approval.denied",
                actor_id=approver_id, actor_email=approver_name,
                entity_type="device_action_request", entity_id=request.id,
                description=f"Denied {request.action} (correlation {request.correlation_id})",
                context=self._json_safe({"correlation_id": request.correlation_id, "note": note}),
            )
            return request

        # ALLOW or MODIFY -> execute (MODIFY first applies parameter edits).
        if decision == "MODIFY":
            if modified_params is None:
                raise DeviceActionError("MODIFY requires modified_params.")
            request.params = self._sanitize_params({**request.params, **modified_params})
        if approval is not None:
            await self.approvals.resolve(
                approval, status="approved", resolved_by=approver_id, note=note
            )
            await self.approvals.record_history(
                approval=approval, actor_id=approver_id, actor_name=approver_name,
                action_type="approved", note=note,
                context={"modified": decision == "MODIFY"},
            )
        # The durable claim already moved the status to "executing"; keep this
        # instance consistent with it. Execution itself records the final
        # succeeded / failed state, and verification the verified state.
        request.status = "executing"
        request.resolved_by = approver_id
        request.resolved_at = datetime.now(timezone.utc)
        request.resolution_note = note
        await self.audit.record(
            tenant_id=tenant_id, action="device_action.approval.approved",
            actor_id=approver_id, actor_email=approver_name,
            entity_type="device_action_request", entity_id=request.id,
            description=f"Approved {request.action} via {decision}",
            context=self._json_safe({"correlation_id": request.correlation_id, "decision": decision, "note": note}),
        )

        # Durability before the external effect: the claim and the recorded
        # approval decision are committed here, so a crash during execution can
        # never leave the action re-decidable, and the provider call runs
        # outside a long database transaction.
        await self.db.commit()
        # Re-fetch after committing: the previous instance is expired, and
        # touching an expired attribute in async SQLAlchemy would trigger a lazy
        # load outside an awaitable context.
        request = await self.requests.get(request_id, tenant_id)
        if request is None:
            raise DeviceActionNotFound()

        device = await self._get_device(tenant_id, request.device_id)
        source = await self._get_source(device)
        return await self._execute(request, device=device, source=source)

    # ------------------------------------------------------------------
    # List / get (read paths)
    # ------------------------------------------------------------------

    async def list_requests(
        self, tenant_id: uuid.UUID, *, device_id: uuid.UUID | None = None,
        status: str | None = None, limit: int = 50, offset: int = 0,
    ) -> list[DeviceActionRequest]:
        return await self.requests.list_for_tenant(
            tenant_id, device_id=device_id, status=status, limit=limit, offset=offset
        )

    async def get_request(self, tenant_id: uuid.UUID, request_id: uuid.UUID) -> DeviceActionRequest:
        request = await self.requests.get(request_id, tenant_id)
        if request is None:
            raise DeviceActionNotFound()
        return request

    # ------------------------------------------------------------------
    # Internals: device/source/capability resolution
    # ------------------------------------------------------------------

    async def _get_device(self, tenant_id: uuid.UUID, device_id: uuid.UUID) -> Device:
        device = await self.devices.get(device_id, tenant_id)
        if device is None:
            raise DeviceActionNotFound(f"Device {device_id} not found in this tenant.")
        return device

    async def _get_source(self, device: Device) -> MonitoringSource | None:
        if device.monitoring_source_id is None:
            return None
        return await self.sources.get(device.monitoring_source_id, device.tenant_id)

    def _resolved_capability_map(
        self, device: Device, source: MonitoringSource | None, explicit
    ) -> dict[str, bool | None]:
        """Explicit capability row ALWAYS wins; derivation fills the rest.
        Monitoring credentials never grant state-change capabilities."""
        derived = derive_capabilities(device, source)
        if explicit is None:
            return derived
        resolved = dict(derived)
        for flag in CAPABILITY_FLAGS:
            value = getattr(explicit, flag, None)
            if value is not None:
                resolved[flag] = value
        return resolved

    def _target(self, request: DeviceActionRequest, device: Device, source) -> ActionTarget:
        """Build the execution target, decrypting credentials ONLY here, for
        the duration of one execution (never persisted, logged, or returned)."""
        connection = decrypt_connection_info(source) if source is not None else None
        return ActionTarget(
            tenant_id=request.tenant_id,
            device_id=device.id,
            name=device.name,
            hostname=device.hostname,
            ip_address=device.ip_address,
            vendor=device.vendor,
            model=device.model,
            connection=connection,
        )

    @staticmethod
    def _sanitize_params(params: dict) -> dict:
        """Params are operator-supplied action parameters (counts, timeouts,
        interface names). Credential-shaped keys are stripped defensively -
        credentials only ever travel via the encrypted source columns."""
        forbidden = {"password", "api_key", "community", "secret", "token",
                     "api_key_encrypted", "password_encrypted", "private_key"}
        return {k: v for k, v in params.items() if k.lower() not in forbidden}

    @staticmethod
    def _json_safe(value):
        """Recursively coerce provider/adapter payloads into JSON-native
        values before they reach a JSONB column.

        Vendor adapters (SSH/REST/FortiGate/Meraki, Phases 3+) routinely
        return UUIDs, datetimes, Decimals and SDK objects; without this
        guard a single such value would fail the JSONB write and poison the
        caller's transaction instead of being recorded. Lossy by design:
        unknown objects become their str() form - never dropped silently.
        """
        if isinstance(value, uuid.UUID):
            return str(value)
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, dict):
            return {str(k): DeviceActionService._json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [DeviceActionService._json_safe(v) for v in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)

    @staticmethod
    def _decision_snapshot(decision: PolicyDecision) -> dict:
        return {
            "allowed": decision.allowed,
            "auto_execute": decision.auto_execute,
            "requires_approval": decision.requires_approval,
            "required_role": decision.required_role,
            "maintenance_window_required": decision.maintenance_window_required,
            "reason": decision.reason,
        }

    async def _create_approval(
        self, request: DeviceActionRequest, decision: PolicyDecision,
        requested_by: uuid.UUID | None,
    ) -> None:
        approval = await self.approvals.create(
            tenant_id=request.tenant_id,
            entity_type="device_action",
            entity_id=request.id,
            approval_type="device_action",
            requested_by=requested_by,
            required_role=decision.required_role,
            context={
                "correlation_id": request.correlation_id,
                "action": request.action,
                "classification": request.classification,
                "device_id": str(request.device_id),
                "params": request.params,
                "motivation": request.motivation,
                "policy_reason": decision.reason,
            },
        )
        request.approval_request_id = approval.id
        request.status = "awaiting_approval"
        await self.db.flush()
        await self.audit.record(
            tenant_id=request.tenant_id,
            action="device_action.approval.required",
            actor_id=requested_by,
            entity_type="device_action_request", entity_id=request.id,
            description=f"Approval required for {request.action}",
            context=self._json_safe({"correlation_id": request.correlation_id,
                                     "required_role": decision.required_role}),
        )

    # ------------------------------------------------------------------
    # Execution: provider dispatch -> verification -> audit
    # ------------------------------------------------------------------

    async def _execute(
        self, request: DeviceActionRequest, *, device: Device, source
    ) -> DeviceActionRequest:
        action_type = DeviceActionType(request.action)
        spec = ACTION_CATALOG[action_type]

        provider_cls = ActionProviderRegistry.try_get(spec.provider_kind or "")
        if provider_cls is None or not provider_cls.implemented:
            request.status = "failed"
            request.error = (
                f"No implemented provider for transport {spec.provider_kind!r}."
            )
            await self.db.flush()
            return request

        target = self._target(request, device, source)
        provider = provider_cls()

        # Execution audit rides the EXISTING ExecutionJob/ExecutionLog tables.
        now = datetime.now(timezone.utc)
        job = ExecutionJob(
            tenant_id=request.tenant_id,
            source_type="device_action",
            source_id=request.id,
            incident_id=request.incident_id,
            status="running",
            triggered_by=request.resolved_by or request.requested_by,
            triggered_at=now,
            # Target metadata only - never credentials.
            target=self._json_safe({"device_id": str(device.id), "device_name": device.name,
                                    "address": target.address, "action": request.action}),
            variables=self._json_safe(request.params),
            approval_request_id=request.approval_request_id,
            approved=request.approval_request_id is not None,
            approved_at=request.resolved_at,
            approved_by=request.resolved_by,
            started_at=now,
        )
        self.db.add(job)
        await self.db.flush()
        request.execution_job_id = job.id
        request.status = "executing"
        request.started_at = now
        # The execution claim (status=executing plus execution_job_id) is
        # committed BEFORE the provider is invoked: it is the durable record that
        # this governed action ran, and committing here releases the row lock so
        # the external call happens outside a database transaction.
        await self.db.commit()
        await self.db.refresh(request)

        started = time.monotonic()
        try:
            result = await provider.execute(action_type, target, request.params)
        except ActionNotSupported as exc:
            result = None
            request.status = "failed"
            request.error = str(exc)
        except Exception as exc:  # noqa: BLE001 - device-level failure, never a 500
            logger.warning("device action execution error: %s", exc)
            result = None
            request.status = "failed"
            request.error = f"Execution error: {type(exc).__name__}"
        elapsed_ms = int((time.monotonic() - started) * 1000)
        completed = datetime.now(timezone.utc)

        if result is not None:
            request.result = self._json_safe(result.output)
            request.error = result.error
            request.status = "succeeded" if result.success else "failed"
            self.db.add(ExecutionLog(
                tenant_id=request.tenant_id, execution_job_id=job.id,
                step_order=1, step_title=f"Execute {request.action}",
                executor=spec.provider_kind, action=request.action,
                input_data=self._sanitize_params(request.params),
                output_data=self._json_safe(result.output),
                status="succeeded" if result.success else "failed",
                started_at=now, completed_at=completed,
                duration_seconds=elapsed_ms / 1000,
                error=result.error,
            ))
            # ---- Post-action verification: never trust the command ----
            if request.status == "succeeded":
                outcome = await self._verify(action_type, target, provider, request)
                request.verification = self._json_safe(outcome.to_dict())
                request.status = "verified" if outcome.passed else "verification_failed"
                self.db.add(ExecutionLog(
                    tenant_id=request.tenant_id, execution_job_id=job.id,
                    step_order=2, step_title="Post-action verification",
                    executor="verifier", action="VERIFY",
                    input_data={"plan": plan_for(action_type, target)},
                    output_data=self._json_safe(outcome.to_dict()),
                    status="succeeded" if outcome.passed else "failed",
                    completed_at=datetime.now(timezone.utc),
                ))

        request.completed_at = completed
        request.duration_ms = elapsed_ms
        job.status = "succeeded" if request.status in ("succeeded", "verified") else "failed"
        job.completed_at = completed
        job.duration_seconds = elapsed_ms / 1000
        await self.db.flush()

        await self.audit.record(
            tenant_id=request.tenant_id,
            action=f"device_action.{request.status}",
            actor_id=request.resolved_by or request.requested_by,
            entity_type="device_action_request", entity_id=request.id,
            description=f"{request.action} -> {request.status}",
            context=self._json_safe({
                "correlation_id": request.correlation_id,
                "execution_job_id": str(job.id),
                "duration_ms": elapsed_ms,
                "verified": request.status == "verified",
            }),
        )
        return request

    async def _verify(self, action_type, target, provider, request):
        """Run the action's verification plan (app/actions/verification).

        PING/HEALTH_CHECK: an independent second probe must AGREE with the
        first result (guards a one-off fluke being recorded as verified).
        SNMP GET_*: the response must carry identifying device data.
        """
        from app.actions.verification import VerificationOutcome

        plan = plan_for(action_type, target)
        steps = []
        if "independent_reachability_recheck" in plan:
            async def recheck() -> tuple[bool, dict]:
                second = await provider.execute(DeviceActionType.PING, target, {"count": 1})
                agree = second.success == bool(request.result.get("ok"))
                return agree, {
                    "recheck_ok": second.success,
                    "first_ok": bool(request.result.get("ok")),
                }
            steps.append(("independent_reachability_recheck", recheck))
        if "response_carries_real_device_data" in plan:
            async def sanity() -> tuple[bool, dict]:
                out = request.result or {}
                has_data = bool(
                    out.get("sys_name")
                    or out.get("sys_descr")
                    or (out.get("interfaces") and out.get("count", 0) > 0)
                    or out.get("firmware")
                )
                return has_data, {"carries_device_data": has_data}
            steps.append(("response_carries_real_device_data", sanity))
        if "vendor_health_carries_real_device_data" in plan:
            async def vendor_sanity() -> tuple[bool, dict]:
                out = request.result or {}
                has_cpu = bool(
                    out.get("cpus") or out.get("processors")
                    or out.get("cpu_1min_pct") is not None
                    or out.get("cpu_5min_pct") is not None
                    or out.get("load_pct") is not None
                )
                has_mem = bool(
                    out.get("memory_pools") or out.get("memory")
                )
                has_data = has_cpu or has_mem
                return has_data, {"carries_vendor_health_data": has_data}

            steps.append(("vendor_health_carries_real_device_data", vendor_sanity))

        if not steps:
            # Nothing to independently verify (unreachable in Phase 1:
            # policy blocks unimplemented actions before execution).
            return VerificationOutcome(passed=True, steps=[])
        return await run_verification(steps)
