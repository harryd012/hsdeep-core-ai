"""Action-plane concurrency and idempotency guards.

The device-action approval path read the request, validated that it was still
``awaiting_approval``, and only then executed. Two approvers acting at the same
time could both pass that check and both fire an infrastructure action. The fix
is an atomic claim - one conditional ``UPDATE ... RETURNING`` - whose winner is
committed BEFORE the provider is invoked and before any external call.

These tests are deterministic and use no sleeps: they prove the claim really is
a conditional, tenant-scoped, single-statement claim; that a refused claim stops
the service before it can reach the provider or the execution path; and that the
provider is not invoked when the claim is refused.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.dialects import postgresql

from app.repositories.device_action_repository import DeviceActionRequestRepository
from app.services.device_action_service import (
    ApprovalDecisionError,
    DeviceActionService,
)

pytestmark = pytest.mark.asyncio


class _CapturingResult:
    def scalar_one_or_none(self):
        return None


class _CapturingSession:
    """Captures the statement without touching a database."""

    def __init__(self) -> None:
        self.statements: list[object] = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _CapturingResult()


class TestClaimPrimitive:
    """The claim is one atomic, tenant-scoped conditional update."""

    async def test_claim_is_a_single_conditional_update_returning_the_row(self):
        session = _CapturingSession()
        repo = DeviceActionRequestRepository(session)
        await repo.claim_status(
            uuid.uuid4(),
            uuid.uuid4(),
            from_status="awaiting_approval",
            to_status="executing",
        )
        sql = str(session.statements[0].compile(dialect=postgresql.dialect())).upper()
        assert sql.startswith("UPDATE ")
        assert "RETURNING DEVICE_ACTION_REQUESTS.ID" in sql
        # The status predicate is what makes this compare-and-set rather than a
        # blind write, and the tenant predicate is what keeps the claim inside
        # one tenant. Assert on the WHERE clause specifically - counting the
        # substring "UPDATE" would also match the updated_at column.
        where_clause = sql.split(" WHERE ", 1)[1].split(" RETURNING ", 1)[0]
        assert "STATUS = " in where_clause
        assert "TENANT_ID = " in where_clause
        assert where_clause.count(" AND ") >= 2


class _StubRequests:
    """Records claim attempts and answers with a scripted outcome."""

    def __init__(self, *, claimed: bool, status: str = "awaiting_approval") -> None:
        self.claimed = claimed
        self.calls: list[dict] = []
        self._request = SimpleNamespace(
            id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            device_id=uuid.uuid4(),
            status=status,
            action="icmp_ping",
            params={},
            correlation_id="corr-1",
            approval_request_id=None,
            incident_id=None,
            requested_by=None,
            resolved_by=None,
            resolved_at=None,
        )

    async def get(self, request_id, tenant_id):
        return self._request

    async def claim_status(self, request_id, tenant_id, *, from_status, to_status):
        self.calls.append(
            {
                "request_id": request_id,
                "tenant_id": tenant_id,
                "from_status": from_status,
                "to_status": to_status,
            }
        )
        return self.claimed


class TestRefusedClaimCannotExecute:
    """Losing the claim is refused before anything external can happen."""

    async def test_refused_claim_raises_and_never_reaches_execution(self):
        service = DeviceActionService.__new__(DeviceActionService)
        service.requests = _StubRequests(claimed=False)  # someone else won

        executed = {"count": 0}

        async def _must_not_run(*args, **kwargs):  # pragma: no cover - safety net
            executed["count"] += 1
            raise AssertionError("execution path must not be reached")

        service._execute = _must_not_run  # type: ignore[method-assign]

        with pytest.raises(ApprovalDecisionError) as exc:
            await service.resolve_approval(
                tenant_id=uuid.uuid4(),
                request_id=uuid.uuid4(),
                decision="ALLOW",
                approver_id=uuid.uuid4(),
                approver_name="harry@hsdeep.io",
            )

        assert "already decided" in str(exc.value).lower()
        assert executed["count"] == 0
        # The claim was attempted with the exact compare-and-set contract.
        assert service.requests.calls[0]["from_status"] == "awaiting_approval"
        assert service.requests.calls[0]["to_status"] == "executing"

    async def test_claim_is_attempted_with_the_compare_and_set_contract(self):
        """The refused path still proves how the claim was attempted."""
        service = DeviceActionService.__new__(DeviceActionService)
        stub = _StubRequests(claimed=False)
        service.requests = stub

        with pytest.raises(ApprovalDecisionError):
            await service.resolve_approval(
                tenant_id=uuid.uuid4(),
                request_id=uuid.uuid4(),
                decision="ALLOW",
                approver_id=uuid.uuid4(),
                approver_name="harry@hsdeep.io",
            )

        assert len(stub.calls) == 1
        assert stub.calls[0]["from_status"] == "awaiting_approval"
        assert stub.calls[0]["to_status"] == "executing"
