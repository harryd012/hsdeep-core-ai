"""Palo Alto Firewall Diagnostic API.

Read-oriented diagnostic endpoints following the incidents router pattern
(tenant via get_tenant_id, session via get_db). Write operations are bounded:
workflow executions are created in-memory keyed by tenant until the migration
lands; approvals are recorded on the SafetyPolicy grant set. All risky steps
still require SafetyPolicy approval on the backend.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.auth import get_tenant_id, require_permission
from app.core.db import get_db
from app.schemas.paloalto import (
    CLIDiagnosticCommand,
    DiagnosticExecution,
    DiagnosticExecutionCreate,
    DiagnosticStepStatus,
    FirewallDiagnosticWorkspace,
    RCAAssessment,
    Runbook,
    RunbookMatch,
)
from app.services.paloalto.cli_reference import CLIReference
from app.services.paloalto.rca_engine import RCAEngine
from app.services.paloalto.runbooks import RunbookRegistry
from app.services.paloalto.safety import SafetyPolicy
from app.services.paloalto.workflow import DiagnosticWorkflowEngine

router = APIRouter(prefix="/paloalto", tags=["paloalto-diagnostic"])

_registry = RunbookRegistry()
_cli = CLIReference()
_rca = RCAEngine()
_engine = DiagnosticWorkflowEngine(safety_policy=SafetyPolicy())

_EXECUTIONS: dict[str, DiagnosticExecution] = {}


def _key(tenant_id: uuid.UUID, ex_id: uuid.UUID) -> str:
    return f"{tenant_id}:{ex_id}"


class RunDiagnosticStepIn(BaseModel):
    note: str | None = Field(default=None)


class SkipStepIn(BaseModel):
    reason: str = Field(default="not applicable to this incident")


class GrantApprovalIn(BaseModel):
    step_id: str


class AssessIn(BaseModel):
    incident_id: uuid.UUID
    evidence: list = Field(default_factory=list)


class MatchRunbooksIn(BaseModel):
    symptoms: list[str]


@router.get("/runbooks", response_model=list[Runbook])
async def list_runbooks(q: str | None = None):
    if q:
        return _registry.search(q)
    return _registry.all()


@router.post(
    "/runbooks/match",
    response_model=list[RunbookMatch],
    dependencies=[Depends(require_permission("devices.read"))],
)
async def match_runbooks(body: MatchRunbooksIn):
    return _registry.match(body.symptoms)


@router.get("/cli", response_model=list[CLIDiagnosticCommand])
async def list_cli_commands(backend_safe: bool | None = None):
    if backend_safe:
        return _cli.backend_safe()
    return _cli.all()


@router.post(
    "/executions",
    response_model=DiagnosticExecution,
    status_code=201,
    dependencies=[Depends(require_permission("runbooks.execute"))],
)
async def create_execution(
    req: DiagnosticExecutionCreate,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    ex = _engine.new_execution(tenant_id, req)
    _EXECUTIONS[_key(tenant_id, ex.id)] = ex
    return ex


@router.get("/executions/{ex_id}", response_model=DiagnosticExecution)
async def get_execution(ex_id: uuid.UUID, tenant_id: uuid.UUID = Depends(get_tenant_id)):
    ex = _EXECUTIONS.get(_key(tenant_id, ex_id))
    if ex is None:
        raise HTTPException(status_code=404, detail="diagnostic execution not found")
    return ex


@router.post(
    "/executions/{ex_id}/steps/{step_id}/run",
    response_model=DiagnosticExecution,
    dependencies=[Depends(require_permission("runbooks.execute"))],
)
async def run_step(
    ex_id: uuid.UUID,
    step_id: str,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    ex = _EXECUTIONS.get(_key(tenant_id, ex_id))
    if ex is None:
        raise HTTPException(status_code=404, detail="diagnostic execution not found")
    if ex.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="cross-tenant access denied")
    try:
        # The safety role is fixed server-side, never taken from the request:
        # a client-chosen "admin" would bypass the approval required for
        # MEDIUM+ risk steps. Approval comes only from the /approve route,
        # which requires runbooks.approve.
        return await _engine.run_step(ex, step_id, action=None, actor_role="engineer")
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown step: {step_id}")


@router.post(
    "/executions/{ex_id}/steps/{step_id}/skip",
    response_model=DiagnosticExecution,
    dependencies=[Depends(require_permission("runbooks.execute"))],
)
async def skip_step(
    ex_id: uuid.UUID,
    step_id: str,
    body: SkipStepIn,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    ex = _EXECUTIONS.get(_key(tenant_id, ex_id))
    if ex is None:
        raise HTTPException(status_code=404, detail="diagnostic execution not found")
    if ex.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="cross-tenant access denied")
    try:
        return _engine.skip_step(ex, step_id, body.reason)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown step: {step_id}")


@router.post(
    "/executions/{ex_id}/approve",
    status_code=204,
    dependencies=[Depends(require_permission("runbooks.approve"))],
)
async def grant_approval(
    ex_id: uuid.UUID,
    body: GrantApprovalIn,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    ex = _EXECUTIONS.get(_key(tenant_id, ex_id))
    if ex is None:
        raise HTTPException(status_code=404, detail="diagnostic execution not found")
    _engine._safety.grant(ex.id, body.step_id)
    return None


@router.post(
    "/rca/assess",
    response_model=RCAAssessment,
    dependencies=[Depends(require_permission("devices.read"))],
)
async def assess_rca(body: AssessIn):
    return _rca.assess(body.incident_id, body.evidence)


@router.get("/workspace", response_model=FirewallDiagnosticWorkspace)
async def diagnostic_workspace(
    device_id: uuid.UUID | None = None,
    incident_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    return FirewallDiagnosticWorkspace(
        device_id=device_id,
        vendor="Palo Alto Networks",
        incident_id=incident_id,
        data_quality={"logs": "uncollected", "config": "uncollected", "rca": "pending"},
    )
