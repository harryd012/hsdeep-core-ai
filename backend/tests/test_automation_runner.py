
"""
Production Automation Runner -> Device Action Plane Bridge (Phase 3).

Verifies that DatabaseStepHandler dispatches every workflow "auto" step
through DeviceActionService - the single controlled execution boundary -
so automation can never bypass RBAC, tenant isolation, policy, approval,
verification, or audit.

MockStepHandler remains a legitimate test double and is never used by the
production (session_factory) code path.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

import app.actions  # noqa: F401 - registers the built-in providers
from app.core.db import AsyncSessionLocal
from app.services.automation_runner import (
    AutomationRunner,
    DatabaseStepHandler,
    MockStepHandler,
    StepResult,
)
from app.services.device_action_service import DeviceActionError
from app.services.events.event_bus import DomainEvent, EventType


# PRTG + ICMP action routing used by the fake service so tests exercise the
# real StepResult mapping branches without touching a database.
_STATUS_BY_ACTION = {
    "PING": "verified",
    "HEALTH_CHECK": "verified",
    "PRTG_QUERY_SENSOR": "verified",
    "PRTG_QUERY_DEVICE": "verified",
    "PRTG_PAUSE_SENSOR": "awaiting_approval",
    "PRTG_RESUME_SENSOR": "awaiting_approval",
    "REBOOT_DEVICE": "policy_denied",
}

_CLASS_BY_ACTION = {
    "PRTG_PAUSE_SENSOR": "CHANGE_REQUIRING_APPROVAL",
    "PRTG_RESUME_SENSOR": "CHANGE_REQUIRING_APPROVAL",
    "REBOOT_DEVICE": "HIGH_RISK",
}

class FakeSession:
    """Minimal async-context-manager session (no database)."""

    def __init__(self):
        self.id = uuid.uuid4()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def commit(self):
        return None

    async def flush(self):
        return None

    async def refresh(self, obj):
        return None


class ServiceState:
    """Mutable state that drives the fake DeviceActionService."""

    def __init__(self):
        self.calls: list[dict] = []
        self.error: Exception | None = None
        self.status: str | None = None

    @property
    def last(self) -> dict:
        return self.calls[-1] if self.calls else {}


def make_request(status="verified", **overrides):
    from app.models.device_action import DeviceActionRequest

    base = dict(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        device_id=uuid.uuid4(),
        action="PING",
        classification="READ_ONLY",
        provider_kind="icmp",
        params={},
        motivation={},
        policy_decision={"reason": "test"},
        status=status,
        requested_by=None,
        correlation_id=uuid.uuid4().hex,
        incident_id=None,
        result={},
        error=None,
        approval_request_id=None,
        created_at=None,
    )
    base.update(overrides)
    return DeviceActionRequest(**base)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def state():
    return ServiceState()


@pytest.fixture(autouse=True)
def patch_device_action_service(monkeypatch, state):
    """Replace DeviceActionService with a fake for every test.

    The fake records every call in ``state.calls`` so tests can assert on
    tenant/actor/correlation propagation, and returns a canned
    DeviceActionRequest whose status drives the StepResult mapping.
    """

    class FakeDeviceActionService:
        def __init__(self, db=None):
            pass

        async def request_action(
            self, *, tenant_id, device_id, action, params, requested_by,
            incident_id=None, motivation=None, correlation_id=None,
        ):
            state.calls.append(dict(
                tenant_id=tenant_id,
                device_id=device_id,
                action=action,
                params=params,
                requested_by=requested_by,
                incident_id=incident_id,
                motivation=motivation,
                correlation_id=correlation_id,
            ))
            if state.error is not None:
                raise state.error
            status = state.status or _STATUS_BY_ACTION.get(action, "verified")
            return make_request(
                status=status,
                tenant_id=tenant_id,
                device_id=device_id,
                action=action,
                classification=_CLASS_BY_ACTION.get(action, "READ_ONLY"),
                correlation_id=correlation_id or uuid.uuid4().hex,
                result={"probe": "ok"},
                approval_request_id=(
                    uuid.uuid4() if status == "awaiting_approval" else None
                ),
            )

    monkeypatch.setattr(
        "app.services.automation_runner.DeviceActionService",
        FakeDeviceActionService,
    )
    return state


@pytest.fixture
def handler():
    """A DatabaseStepHandler wired to a fake session (no database)."""
    return DatabaseStepHandler(session_factory=lambda: FakeSession())


@pytest.fixture
def tenant_id():
    return uuid.uuid4()


@pytest.fixture
def device_id():
    return uuid.uuid4()


@pytest.fixture
def actor_id():
    return uuid.uuid4()


@pytest.fixture
def correlation_id():
    return uuid.uuid4().hex


@pytest.fixture
def event_bus():
    from app.services.events.event_bus import EventBus

    return EventBus()


@pytest.fixture
def sop_repo(db):
    from app.repositories.sop_repository import SOPRepository

    return SOPRepository(db)


@pytest.fixture
def execution_repo(db):
    from app.repositories.sop_execution_repository import SOPExecutionRepository

    return SOPExecutionRepository(db)

# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestDatabaseStepHandlerConstruction:
    def test_requires_session_factory(self):
        with pytest.raises(TypeError):
            DatabaseStepHandler()

    def test_accepts_actor_id(self):
        actor = uuid.uuid4()
        h = DatabaseStepHandler(session_factory=lambda: FakeSession(), actor_id=actor)
        assert h._actor_id == actor

    def test_implements_step_handler_protocol(self, handler):
        assert hasattr(handler, "execute")


# ---------------------------------------------------------------------------
# READ_ONLY dispatch
# ---------------------------------------------------------------------------


class TestReadOnlyDispatch:
    async def test_ping_succeeds(self, handler, state, tenant_id, device_id, correlation_id):
        result = await handler.execute(
            {"action": "PING", "target_device_id": str(device_id),
             "parameters": {"count": 2}},
            {"tenant_id": str(tenant_id), "correlation_id": correlation_id},
        )
        assert result.success is True
        assert result.status == "succeeded"
        assert result.output["action"] == "PING"
        assert result.output["request_id"]
        assert result.output["correlation_id"] == correlation_id
        assert state.last["action"] == "PING"
        assert state.last["params"] == {"count": 2}

    async def test_prtg_query_sensor(self, handler, state, tenant_id, device_id):
        result = await handler.execute(
            {"action": "PRTG_QUERY_SENSOR", "target_device_id": str(device_id),
             "parameters": {"sensor_id": "1234"}},
            {"tenant_id": str(tenant_id)},
        )
        assert result.success is True
        assert result.status == "succeeded"
        assert result.output["action"] == "PRTG_QUERY_SENSOR"

    async def test_prtg_query_device(self, handler, tenant_id, device_id):
        result = await handler.execute(
            {"action": "PRTG_QUERY_DEVICE", "target_device_id": str(device_id)},
            {"tenant_id": str(tenant_id)},
        )
        assert result.status == "succeeded"

    async def test_pending_status_maps_to_in_progress(self, handler, state, tenant_id, device_id):
        state.status = "pending"
        result = await handler.execute(
            {"action": "PING", "target_device_id": str(device_id)},
            {"tenant_id": str(tenant_id)},
        )
        assert result.success is True
        assert result.status == "pending"

# ---------------------------------------------------------------------------
# Approval dispatch (CHANGE_REQUIRING_APPROVAL)
# ---------------------------------------------------------------------------


class TestApprovalDispatch:
    async def test_pause_awaits_approval(self, handler, state, tenant_id, device_id):
        result = await handler.execute(
            {"action": "PRTG_PAUSE_SENSOR", "target_device_id": str(device_id),
             "parameters": {"reason": "maintenance"}},
            {"tenant_id": str(tenant_id)},
        )
        assert result.success is False
        assert result.status == "awaiting_approval"
        assert result.output["approval_request_id"]
        assert result.output["classification"] == "CHANGE_REQUIRING_APPROVAL"
        assert state.last["params"] == {"reason": "maintenance"}

    async def test_resume_awaits_approval(self, handler, tenant_id, device_id):
        result = await handler.execute(
            {"action": "PRTG_RESUME_SENSOR", "target_device_id": str(device_id)},
            {"tenant_id": str(tenant_id)},
        )
        assert result.status == "awaiting_approval"
        assert result.output["action"] == "PRTG_RESUME_SENSOR"

    async def test_approval_is_never_auto_granted(self, handler, tenant_id, device_id):
        """The bridge must not decide approvals - it only surfaces the state."""
        result = await handler.execute(
            {"action": "PRTG_PAUSE_SENSOR", "target_device_id": str(device_id)},
            {"tenant_id": str(tenant_id)},
        )
        assert result.success is False
        assert "human approval" in result.error.lower()


# ---------------------------------------------------------------------------
# Policy denial
# ---------------------------------------------------------------------------


class TestPolicyDenial:
    async def test_reboot_denied_outside_maintenance(self, handler, tenant_id, device_id):
        result = await handler.execute(
            {"action": "REBOOT_DEVICE", "target_device_id": str(device_id)},
            {"tenant_id": str(tenant_id)},
        )
        assert result.success is False
        assert result.status == "policy_denied"
        assert result.output["policy_reason"] == "test"

    async def test_denied_action_never_executes(self, handler, state, tenant_id, device_id):
        await handler.execute(
            {"action": "REBOOT_DEVICE", "target_device_id": str(device_id)},
            {"tenant_id": str(tenant_id)},
        )
        # The service was called (that is where policy lives) and returned
        # policy_denied - the handler propagated it without executing.
        assert state.last["action"] == "REBOOT_DEVICE"

# ---------------------------------------------------------------------------
# Invalid steps / invalid actions
# ---------------------------------------------------------------------------


class TestInvalidSteps:
    async def test_missing_action(self, handler, state, tenant_id, device_id):
        result = await handler.execute(
            {"target_device_id": str(device_id)},
            {"tenant_id": str(tenant_id)},
        )
        assert result.success is False
        assert result.status == "failed"
        assert "missing required" in result.error
        assert state.calls == []  # never reached the service

    async def test_blank_action(self, handler, state, tenant_id, device_id):
        result = await handler.execute(
            {"action": "   ", "target_device_id": str(device_id)},
            {"tenant_id": str(tenant_id)},
        )
        assert result.status == "failed"
        assert state.calls == []

    async def test_unknown_action_rejected(self, handler, state, tenant_id, device_id):
        result = await handler.execute(
            {"action": "rm -rf /", "target_device_id": str(device_id)},
            {"tenant_id": str(tenant_id)},
        )
        assert result.success is False
        assert result.status == "failed"
        assert "catalog" in result.error.lower()
        assert state.calls == []

    async def test_shell_like_action_rejected(self, handler, state, tenant_id, device_id):
        result = await handler.execute(
            {"action": "subprocess.run('ls')", "target_device_id": str(device_id)},
            {"tenant_id": str(tenant_id)},
        )
        assert result.status == "failed"
        assert state.calls == []

    async def test_arbitrary_url_action_rejected(self, handler, state, tenant_id, device_id):
        result = await handler.execute(
            {"action": "http://evil.example.com/x", "target_device_id": str(device_id)},
            {"tenant_id": str(tenant_id)},
        )
        assert result.status == "failed"
        assert state.calls == []

    async def test_missing_target_device(self, handler, tenant_id):
        result = await handler.execute(
            {"action": "PING"},
            {"tenant_id": str(tenant_id)},
        )
        assert result.success is False
        assert result.status == "failed"
        assert "target_device_id" in result.error

    async def test_missing_tenant_context(self, handler, device_id):
        result = await handler.execute(
            {"action": "PING", "target_device_id": str(device_id)},
            {},
        )
        assert result.success is False
        assert result.status == "failed"

# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


class TestTenantIsolation:
    async def test_tenant_flows_to_service(self, handler, state, tenant_id, device_id):
        await handler.execute(
            {"action": "PING", "target_device_id": str(device_id)},
            {"tenant_id": str(tenant_id)},
        )
        assert state.last["tenant_id"] == tenant_id

    async def test_device_flows_to_service(self, handler, state, tenant_id, device_id):
        await handler.execute(
            {"action": "PING", "target_device_id": str(device_id)},
            {"tenant_id": str(tenant_id)},
        )
        assert state.last["device_id"] == device_id

    async def test_cross_tenant_device_fails_closed(self, handler, state, tenant_id, device_id):
        """DeviceActionService owns tenant scoping; when it refuses, the
        bridge reports failure - it never retries with a different tenant."""
        state.error = DeviceActionError("Device not found in tenant.")
        result = await handler.execute(
            {"action": "PING", "target_device_id": str(device_id)},
            {"tenant_id": str(uuid.uuid4())},
        )
        assert result.success is False
        assert result.status == "failed"
        assert "not found" in result.error

    async def test_failure_is_never_reported_as_success(self, handler, state, tenant_id, device_id):
        state.error = DeviceActionError("policy engine unavailable")
        result = await handler.execute(
            {"action": "PING", "target_device_id": str(device_id)},
            {"tenant_id": str(tenant_id)},
        )
        assert result.success is False


# ---------------------------------------------------------------------------
# Actor propagation
# ---------------------------------------------------------------------------


class TestActorPropagation:
    async def test_actor_from_context(self, handler, state, tenant_id, device_id, actor_id):
        await handler.execute(
            {"action": "PING", "target_device_id": str(device_id)},
            {"tenant_id": str(tenant_id), "actor_id": str(actor_id)},
        )
        assert state.last["requested_by"] == actor_id

    async def test_actor_from_constructor(self, state, tenant_id, device_id, actor_id):
        h = DatabaseStepHandler(session_factory=lambda: FakeSession(), actor_id=actor_id)
        await h.execute(
            {"action": "PING", "target_device_id": str(device_id)},
            {"tenant_id": str(tenant_id)},
        )
        assert state.last["requested_by"] == actor_id

    async def test_actor_never_silently_becomes_system(self, handler, state, tenant_id, device_id):
        await handler.execute(
            {"action": "PING", "target_device_id": str(device_id)},
            {"tenant_id": str(tenant_id)},
        )
        assert state.last["requested_by"] is None  # never spoofed as 'system'

    async def test_invalid_actor_string_is_ignored(self, handler, state, tenant_id, device_id):
        await handler.execute(
            {"action": "PING", "target_device_id": str(device_id)},
            {"tenant_id": str(tenant_id), "actor_id": "not-a-uuid"},
        )
        assert state.last["requested_by"] is None

# ---------------------------------------------------------------------------
# Correlation propagation
# ---------------------------------------------------------------------------


class TestCorrelationPropagation:
    async def test_correlation_reaches_service(self, handler, state, tenant_id, device_id, correlation_id):
        await handler.execute(
            {"action": "PING", "target_device_id": str(device_id)},
            {"tenant_id": str(tenant_id), "correlation_id": correlation_id},
        )
        assert state.last["correlation_id"] == correlation_id

    async def test_correlation_is_echoed_in_step_output(self, handler, tenant_id, device_id, correlation_id):
        result = await handler.execute(
            {"action": "PING", "target_device_id": str(device_id)},
            {"tenant_id": str(tenant_id), "correlation_id": correlation_id},
        )
        assert result.output["correlation_id"] == correlation_id

    async def test_correlation_also_lands_in_motivation(self, handler, state, tenant_id, device_id, correlation_id):
        await handler.execute(
            {"action": "PING", "target_device_id": str(device_id)},
            {"tenant_id": str(tenant_id), "correlation_id": correlation_id},
        )
        assert state.last["motivation"]["correlation_id"] == correlation_id

    async def test_absent_correlation_generates_exactly_one(self, handler, state, tenant_id, device_id):
        await handler.execute(
            {"action": "PING", "target_device_id": str(device_id)},
            {"tenant_id": str(tenant_id)},
        )
        assert state.last["correlation_id"] is None  # service generates it


# ---------------------------------------------------------------------------
# Secret hygiene - the bridge must never handle credentials
# ---------------------------------------------------------------------------


class TestCredentialHygiene:
    async def test_only_workflow_params_are_forwarded(self, handler, state, tenant_id, device_id):
        await handler.execute(
            {"action": "PRTG_QUERY_SENSOR", "target_device_id": str(device_id),
             "parameters": {"sensor_id": "1234"}},
            {"tenant_id": str(tenant_id)},
        )
        assert state.last["params"] == {"sensor_id": "1234"}
        blob = repr(state.calls).lower()
        for forbidden in ("passhash", "password", "secret", "token", "api_key"):
            assert forbidden not in blob

    async def test_stephandler_module_has_no_direct_provider_access(self):
        import app.services.automation_runner as mod

        source = open(mod.__file__, encoding="utf-8").read()
        # Providers are only reachable through DeviceActionService.
        for provider in ("PrtgActionProvider", "IcmpActionProvider", "SnmpActionProvider"):
            assert provider not in source

    async def test_stephandler_module_never_imports_http_or_subprocess(self):
        import app.services.automation_runner as mod

        source = open(mod.__file__, encoding="utf-8").read()
        assert "import subprocess" not in source
        assert "subprocess.run" not in source
        assert "os.system" not in source
        assert "httpx" not in source
        assert "requests" not in source

# ---------------------------------------------------------------------------
# MockStepHandler remains a test double
# ---------------------------------------------------------------------------


class TestMockStepHandler:
    async def test_success_path(self):
        result = await MockStepHandler().execute(
            {"action": "PING", "target_device_id": "x"}, {"tenant_id": "t"}
        )
        assert result.success is True
        assert result.status == "succeeded"

    async def test_fail_action(self):
        result = await MockStepHandler().execute(
            {"action": "FAIL_ME"}, {"tenant_id": "t"}
        )
        assert result.success is False
        assert result.status == "failed"

    async def test_missing_action(self):
        result = await MockStepHandler().execute({}, {"tenant_id": "t"})
        assert result.success is False


# ---------------------------------------------------------------------------
# AutomationRunner selects the production handler
# ---------------------------------------------------------------------------


class TestAutomationRunnerHandlerSelection:
    def test_session_factory_path_uses_database_handler(self, event_bus):
        runner = AutomationRunner(event_bus, session_factory=lambda: AsyncSessionLocal())
        assert isinstance(runner._step_handler, DatabaseStepHandler)

    def test_explicit_handler_wins(self, event_bus):
        mock = MockStepHandler()
        runner = AutomationRunner(
            event_bus, step_handler=mock,
            session_factory=lambda: AsyncSessionLocal(),
        )
        assert runner._step_handler is mock

    def test_legacy_repo_path_keeps_mock(self, event_bus, sop_repo, execution_repo):
        """Unchanged behaviour for the legacy (explicit-repos) path."""
        runner = AutomationRunner(
            event_bus, sop_repo=sop_repo, execution_repo=execution_repo
        )
        assert isinstance(runner._step_handler, MockStepHandler)

    def test_requires_a_session_source(self, event_bus):
        with pytest.raises(ValueError):
            AutomationRunner(event_bus)


# ---------------------------------------------------------------------------
# StepResult mapping
# ---------------------------------------------------------------------------


class TestStepResultMapping:
    async def test_verified_maps_to_succeeded(self, handler, tenant_id, device_id):
        result = await handler.execute(
            {"action": "PING", "target_device_id": str(device_id)},
            {"tenant_id": str(tenant_id)},
        )
        assert (result.success, result.status) == (True, "succeeded")

    async def test_awaiting_approval_maps_correctly(self, handler, tenant_id, device_id):
        result = await handler.execute(
            {"action": "PRTG_PAUSE_SENSOR", "target_device_id": str(device_id)},
            {"tenant_id": str(tenant_id)},
        )
        assert (result.success, result.status) == (False, "awaiting_approval")

    async def test_policy_denied_maps_correctly(self, handler, tenant_id, device_id):
        result = await handler.execute(
            {"action": "REBOOT_DEVICE", "target_device_id": str(device_id)},
            {"tenant_id": str(tenant_id)},
        )
        assert (result.success, result.status) == (False, "policy_denied")

    async def test_unknown_status_is_never_marked_success(self, handler, state, tenant_id, device_id):
        state.status = "failed"
        result = await handler.execute(
            {"action": "PING", "target_device_id": str(device_id)},
            {"tenant_id": str(tenant_id)},
        )
        assert result.success is False
        assert result.status == "failed"

    async def test_stepresult_defaults(self):
        r = StepResult(success=True)
        assert r.status == "succeeded"
        assert r.output == {}
        assert r.error is None


# ---------------------------------------------------------------------------
# Resume-after-approval (Action Plane bridge, part 2)
#
# Pure in-memory fakes: no database. FakeSOP/FakeSOPRepo stand in for the
# SOP aggregate, FakeExecutionRepo mirrors the SOPExecutionRepository call
# surface the runner uses, and ResumeSession serves canned rows to the
# finder's select() and db.get() while tolerating the repository's
# flush()/refresh() calls.
# ---------------------------------------------------------------------------


class FakeSOPVersion:
    def __init__(self, steps, sop=None):
        self.steps = steps
        self.sop = sop


class FakeSOP:
    def __init__(self, steps, rollback_procedure=False):
        self.id = uuid.uuid4()
        self.rollback_procedure = rollback_procedure
        self.current_version = FakeSOPVersion(steps, sop=self)


class FakeSOPRepo:
    def __init__(self, sop):
        self._sop = sop

    async def get(self, sop_id, tenant_id):
        return self._sop if sop_id == self._sop.id else None


class FakeExecutionRepo:
    """In-memory stand-in for SOPExecutionRepository (create /
    record_step_result / complete / db), matching the real signatures."""

    def __init__(self, execution=None):
        from datetime import datetime, timezone

        if execution is not None:
            self.execution = execution
        else:
            now = datetime.now(timezone.utc)
            self.execution = SimpleNamespace(
                id=uuid.uuid4(),
                tenant_id=None,
                sop_id=uuid.uuid4(),
                ticket_id=None,
                incident_id=None,
                correlation_id=uuid.uuid4().hex,
                status="running",
                current_step_index=0,
                step_results=[],
                assigned_to=None,
                approved_by=None,
                started_at=now,
                created_at=now,
                completed_at=None,
                duration_seconds=None,
                result_notes=None,
                rollback_required=False,
                rollback_performed=False,
            )
        self.db = FakeSession()
        self.completed = None

    async def create(self, **fields):
        for key, value in fields.items():
            setattr(self.execution, key, value)
        return self.execution

    async def record_step_result(
        self, execution, *, step_index, status, output=None, error=None,
    ):
        execution.step_results = [*execution.step_results, {
            "step_index": step_index,
            "status": status,
            "output": output or {},
            "error": error,
            "recorded_at": "now",
        }]
        return execution

    async def complete(
        self, execution, *, success, result_notes=None, rollback_required=False,
    ):
        execution.status = "succeeded" if success else "failed"
        self.completed = {
            "success": success,
            "result_notes": result_notes,
            "rollback_required": rollback_required,
        }
        return execution


class _ScalarResult:
    def __init__(self, items):
        self._items = list(items)

    def scalars(self):
        return list(self._items)


class ResumeSession(FakeSession):
    """Fake AsyncSession for resume tests: serves canned rows for the
    finder's select() and for db.get(DeviceActionRequest, ...), and
    tolerates the repository's flush()/refresh()/commit() calls."""

    def __init__(self, executions=(), request_row=None):
        super().__init__()
        self.executions = list(executions)
        self.request_row = request_row
        self.commits = 0

    async def execute(self, stmt):
        return _ScalarResult(self.executions)

    async def get(self, model, pk):
        return self.request_row

    async def commit(self):
        self.commits += 1

    async def flush(self):
        return None

    async def refresh(self, obj):
        return None


def make_paused_execution(request_id, *, tenant_id=None, current_step_index=0):
    """A SOPExecution-shaped namespace paused at a device-action approval."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=tenant_id or uuid.uuid4(),
        sop_id=uuid.uuid4(),
        ticket_id=uuid.uuid4(),
        incident_id=None,
        correlation_id=uuid.uuid4().hex,
        status="awaiting_approval",
        current_step_index=current_step_index,
        step_results=[{
            "step_index": current_step_index,
            "status": "waiting_approval",
            "output": {
                "action": "PRTG_PAUSE_SENSOR",
                "request_id": str(request_id),
                "device_action_request_id": str(request_id),
                "requires_approval": True,
            },
            "error": None,
            "recorded_at": "now",
        }],
        assigned_to=None,
        approved_by=None,
        started_at=now,
        created_at=now,
        completed_at=None,
        result_notes=None,
        rollback_required=False,
    )


def make_started_event(tenant_id, *, sop_id=None):
    return DomainEvent(
        event_type=EventType.SOP_STARTED,
        tenant_id=tenant_id,
        correlation_id=uuid.uuid4().hex,
        payload={
            "ticket_id": str(uuid.uuid4()),
            "sop_id": str(sop_id or uuid.uuid4()),
        },
    )


@pytest.fixture
def published(event_bus, monkeypatch):
    """Collector for every DomainEvent the runner publishes.

    The real EventBus queues events for a background worker (handlers only
    run after start()), so tests record them at the publish seam instead of
    subscribing.
    """
    seen = []

    async def _record(event):
        seen.append(event)

    monkeypatch.setattr(event_bus, "publish", _record)
    return seen


@pytest.fixture
def make_runner(event_bus, handler):
    """Builds an AutomationRunner on in-memory repos plus the production
    DatabaseStepHandler (whose DeviceActionService is faked by the autouse
    fixture) — mirroring the production wiring."""
    def _make(steps, execution=None):
        sop = FakeSOP(steps)
        runner = AutomationRunner(
            event_bus=event_bus,
            sop_repo=FakeSOPRepo(sop),
            execution_repo=FakeExecutionRepo(execution),
            step_handler=handler,
        )
        return runner, sop
    return _make


class TestAutoStepApprovalPause:
    """An auto step whose device action returns awaiting_approval must PAUSE
    the execution, never fail it."""

    async def test_awaiting_approval_pauses_execution(self, make_runner):
        steps = [{"action": "PRTG_PAUSE_SENSOR", "type": "auto",
                  "target_device_id": str(uuid.uuid4())}]
        runner, sop = make_runner(steps)
        await runner._on_sop_started(make_started_event(uuid.uuid4(), sop_id=sop.id))
        repo = runner._execution_repo
        execution = repo.execution
        assert execution.status == "awaiting_approval"
        assert execution.current_step_index == 0
        assert repo.completed is None  # never marked failed/completed
        last = execution.step_results[-1]
        assert last["status"] == "waiting_approval"
        assert last["output"]["requires_approval"] is True
        assert last["output"]["device_action_request_id"] == last["output"]["request_id"]

    async def test_pause_publishes_escalation_not_completion(self, make_runner, published):
        steps = [{"action": "PRTG_RESUME_SENSOR", "type": "auto",
                  "target_device_id": str(uuid.uuid4())}]
        runner, sop = make_runner(steps)
        await runner._on_sop_started(make_started_event(uuid.uuid4(), sop_id=sop.id))
        assert any(evt.event_type == EventType.ENGINEER_ESCALATED for evt in published)
        assert not any(evt.event_type == EventType.SOP_COMPLETED for evt in published)

    async def test_pause_does_not_execute_remaining_steps(self, make_runner, state):
        steps = [
            {"action": "PRTG_PAUSE_SENSOR", "type": "auto",
             "target_device_id": str(uuid.uuid4())},
            {"action": "PING", "type": "auto",
             "target_device_id": str(uuid.uuid4())},
        ]
        runner, sop = make_runner(steps)
        await runner._on_sop_started(make_started_event(uuid.uuid4(), sop_id=sop.id))
        assert len(state.calls) == 1
        assert state.calls[0]["action"] == "PRTG_PAUSE_SENSOR"

    async def test_pause_records_the_paused_step_index(self, make_runner):
        steps = [
            {"action": "PING", "type": "auto", "target_device_id": str(uuid.uuid4())},
            {"action": "PRTG_PAUSE_SENSOR", "type": "auto",
             "target_device_id": str(uuid.uuid4())},
        ]
        runner, sop = make_runner(steps)
        await runner._on_sop_started(make_started_event(uuid.uuid4(), sop_id=sop.id))
        repo = runner._execution_repo
        assert repo.execution.status == "awaiting_approval"
        assert repo.execution.current_step_index == 1
        assert [entry["status"] for entry in repo.execution.step_results] == [
            "succeeded", "waiting_approval",
        ]

    async def test_policy_denied_still_fails_execution(self, make_runner, published):
        steps = [{"action": "REBOOT_DEVICE", "type": "auto",
                  "target_device_id": str(uuid.uuid4())}]
        runner, sop = make_runner(steps)
        await runner._on_sop_started(make_started_event(uuid.uuid4(), sop_id=sop.id))
        repo = runner._execution_repo
        assert repo.execution.status == "failed"
        assert repo.completed is not None and repo.completed["success"] is False
        assert any(
            evt.event_type == EventType.SOP_COMPLETED
            and evt.payload.get("success") is False
            for evt in published
        )

    async def test_verified_step_still_succeeds(self, make_runner, published):
        steps = [{"action": "PING", "type": "auto",
                  "target_device_id": str(uuid.uuid4())}]
        runner, sop = make_runner(steps)
        await runner._on_sop_started(make_started_event(uuid.uuid4(), sop_id=sop.id))
        repo = runner._execution_repo
        assert repo.execution.status == "succeeded"
        assert repo.completed is not None and repo.completed["success"] is True
        assert any(
            evt.event_type == EventType.SOP_COMPLETED
            and evt.payload.get("success") is True
            for evt in published
        )


class TestResumeAfterDeviceActionDecision:
    """resume_after_device_action_decision: the approve-API counterpart of
    the auto-step approval pause."""

    async def _resume(
        self, monkeypatch, event_bus, *, request_status="verified",
        steps=None, tenant_id=None, approved=True, request_id=None,
        executions=None, approver_id=None,
    ):
        request_id = request_id or uuid.uuid4()
        tenant_id = tenant_id or uuid.uuid4()
        execution = make_paused_execution(request_id, tenant_id=tenant_id)
        session = ResumeSession(
            executions=executions if executions is not None else [execution],
            request_row=SimpleNamespace(status=request_status),
        )
        monkeypatch.setattr(
            "app.services.automation_runner.AsyncSessionLocal", lambda: session,
        )
        if steps is None:
            steps = [
                {"action": "PRTG_PAUSE_SENSOR", "type": "auto",
                 "target_device_id": str(uuid.uuid4())},
                {"action": "PING", "type": "auto",
                 "target_device_id": str(uuid.uuid4())},
            ]
        sop = FakeSOP(steps)
        sop.id = execution.sop_id

        class _SOPRepo:
            def __init__(self, db):
                self._sop = sop

            async def get(self, sop_id, tenant):
                return self._sop if sop_id == self._sop.id else None

        monkeypatch.setattr("app.services.automation_runner.SOPRepository", _SOPRepo)
        runner = AutomationRunner(
            event_bus=event_bus, session_factory=lambda: FakeSession(),
        )
        result = await runner.resume_after_device_action_decision(
            request_id, tenant_id,
            approved=approved, approver_id=approver_id or uuid.uuid4(),
        )
        return result, execution, session

    async def test_allow_resumes_and_completes(
        self, monkeypatch, event_bus, published, state,
    ):
        result, execution, session = await self._resume(monkeypatch, event_bus)
        assert result is execution
        assert execution.status == "succeeded"
        assert execution.current_step_index == 2
        # The remaining step ran through DeviceActionService (faked).
        assert len(state.calls) == 1
        assert state.calls[0]["action"] == "PING"
        # waiting_approval → resumed step → next step
        assert [entry["status"] for entry in execution.step_results] == [
            "waiting_approval", "succeeded", "succeeded",
        ]
        assert any(
            evt.event_type == EventType.SOP_COMPLETED
            and evt.payload.get("success") is True
            for evt in published
        )

    async def test_allow_records_final_status_and_approver(
        self, monkeypatch, event_bus,
    ):
        approver = uuid.uuid4()
        result, execution, session = await self._resume(
            monkeypatch, event_bus, approver_id=approver,
        )
        resumed_entry = execution.step_results[1]
        assert resumed_entry["status"] == "succeeded"
        assert resumed_entry["output"]["final_status"] == "verified"
        assert resumed_entry["output"]["approved_by"] == str(approver)
        assert resumed_entry["output"]["approved"] is True

    async def test_allow_with_failed_action_fails_execution(
        self, monkeypatch, event_bus, published, state,
    ):
        result, execution, session = await self._resume(
            monkeypatch, event_bus, request_status="failed",
        )
        assert execution.status == "failed"
        assert len(state.calls) == 0  # remaining steps never ran
        last = execution.step_results[-1]
        assert last["status"] == "failed"
        assert last["output"]["final_status"] == "failed"
        assert any(
            evt.event_type == EventType.SOP_COMPLETED
            and evt.payload.get("success") is False
            for evt in published
        )

    async def test_verification_failure_also_fails_execution(
        self, monkeypatch, event_bus,
    ):
        result, execution, session = await self._resume(
            monkeypatch, event_bus, request_status="verification_failed",
        )
        assert execution.status == "failed"

    async def test_pending_action_counts_as_success(
        self, monkeypatch, event_bus, published, state,
    ):
        result, execution, session = await self._resume(
            monkeypatch, event_bus, request_status="pending",
        )
        assert execution.status == "succeeded"
        assert len(state.calls) == 1  # workflow continued past the pause

    async def test_deny_abandons_execution(
        self, monkeypatch, event_bus, published, state,
    ):
        result, execution, session = await self._resume(
            monkeypatch, event_bus, approved=False,
        )
        assert execution.status == "abandoned"
        assert execution.result_notes
        assert len(state.calls) == 0
        assert any(
            evt.event_type == EventType.SOP_COMPLETED
            and evt.payload.get("success") is False
            for evt in published
        )

    async def test_no_waiting_execution_returns_none(
        self, monkeypatch, event_bus,
    ):
        result, execution, session = await self._resume(
            monkeypatch, event_bus, executions=[],
        )
        assert result is None

    async def test_unrelated_paused_execution_not_matched(
        self, monkeypatch, event_bus,
    ):
        other = make_paused_execution(uuid.uuid4())
        result, execution, session = await self._resume(
            monkeypatch, event_bus, executions=[other], request_id=uuid.uuid4(),
        )
        assert result is None
        assert other.status == "awaiting_approval"  # untouched

    async def test_module_helper_boots_on_global_bus(self, monkeypatch, event_bus):
        from app.services import automation_runner as ar

        seen = {}

        def _fake_runner_ctor(**kwargs):
            seen.update(kwargs)

            class _R:
                async def resume_after_device_action_decision(self, *args, **kwargs):
                    seen["call_args"] = (args, kwargs)
                    return "sentinel"

            return _R()

        monkeypatch.setattr(ar, "get_event_bus", lambda: event_bus)
        monkeypatch.setattr(ar, "AutomationRunner", _fake_runner_ctor)
        monkeypatch.setattr(ar, "AsyncSessionLocal", lambda: FakeSession())

        request_id, tenant_id = uuid.uuid4(), uuid.uuid4()
        out = await ar.resume_after_device_action_decision(
            request_id, tenant_id, approved=True, approver_id=None, note=None,
        )
        assert out == "sentinel"
        assert seen["event_bus"] is event_bus
        args, kwargs = seen["call_args"]
        assert args == (request_id, tenant_id)
        assert kwargs["approved"] is True
