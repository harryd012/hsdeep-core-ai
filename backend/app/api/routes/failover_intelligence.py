"""
WAN Failover Intelligence API — mounts at ``/api/failover``.

Read-mostly surface over the observed-failover record. Every endpoint resolves
the tenant from the authenticated principal (``Depends(get_tenant_id)``), never
from a query parameter, so cross-tenant reads are structurally impossible; a
resource belonging to another tenant is indistinguishable from one that does not
exist (404, never 403).

Permissions follow the existing vocabulary:

* ``devices.read``   - episodes, timelines, blast radius, policies, dry-run
                       evaluate, counterfactual simulation. These are reads or
                       pure computations; the POSTs write nothing.
* ``incidents.write`` - the ONE state-changing endpoint: manual recovery
                       verification, which records a verification result on the
                       episode. It is deliberately gated at incident level
                       because it writes into the operational record.

Nothing here can create an incident, execute a device change, or drive a
failover. See ``app/services/failover_intelligence`` for the reasoning.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_tenant_id, require_permission
from app.core.db import get_db
from app.models.failover_event import STATUSES, TERMINAL_STATUSES
from app.models.operational_sla_policy import METRICS, SCOPES
from app.repositories.failover_event_repository import FailoverEventRepository
from app.repositories.incident_repository import IncidentRepository
from app.repositories.operational_sla_policy_repository import (
    OperationalSLAPolicyRepository,
)
from app.services.failover_intelligence.blast_radius import BlastRadiusService
from app.services.failover_intelligence.detector import FailoverDetectorService
from app.services.failover_intelligence.loader import load_scope, list_scopes
from app.services.failover_intelligence.policy_admin import (
    PolicyAdminService,
    PolicyConflictError,
    PolicyValidationError,
)
from app.services.failover_intelligence.replay import ReplayService
from app.services.failover_intelligence.simulation import CounterfactualSimulator
from app.services.failover_intelligence.verify import RecoveryVerificationService

router = APIRouter(prefix="/failover", tags=["failover-intelligence"])

MAX_LIMIT = 200
DEFAULT_LIMIT = 50
#: Cap on scopes a tenant-wide dry run walks (bounded, like the detector).
MAX_DRY_RUN_SCOPES = 10


def get_db_session(db: AsyncSession = Depends(get_db)) -> AsyncSession:
    return db


def _validate(value: str | None, allowed: Any, field: str) -> None:
    if value is None:
        return
    if value not in allowed:
        raise HTTPException(
            status_code=422,
            detail=f"invalid {field}={value!r}; expected one of {sorted(allowed)}",
        )


def _clamp(limit: int, default: int = DEFAULT_LIMIT) -> int:
    if limit is None or limit <= 0:
        return default
    return min(int(limit), MAX_LIMIT)


def _event_io(event) -> dict:
    """List projection: no JSONB payloads (those belong to the detail view)."""
    return {
        "id": str(event.id),
        "sequence_no": event.sequence_no,
        "status": event.status,
        "status_changed_at": (
            event.status_changed_at.isoformat() if event.status_changed_at else None
        ),
        "site_id": str(event.site_id) if event.site_id else None,
        "edge_device_id": (
            str(event.edge_device_id) if event.edge_device_id else None
        ),
        "scope_key": event.scope_key,
        "link_from_id": str(event.link_from_id) if event.link_from_id else None,
        "link_from_name": event.link_from_name,
        "link_to_id": str(event.link_to_id) if event.link_to_id else None,
        "link_to_name": event.link_to_name,
        "detected_at": event.detected_at.isoformat() if event.detected_at else None,
        "failed_over_at": (
            event.failed_over_at.isoformat() if event.failed_over_at else None
        ),
        "recovering_at": (
            event.recovering_at.isoformat() if event.recovering_at else None
        ),
        "recovered_at": (
            event.recovered_at.isoformat() if event.recovered_at else None
        ),
        "incident_id": str(event.incident_id) if event.incident_id else None,
        "is_open": event.is_open,
        "blast_radius_total": (event.blast_radius or {}).get("total", 0),
        "blast_radius_critical": (
            (event.blast_radius or {}).get("by_severity") or {}
        ).get("critical", 0),
        # Counts, NOT the arrays: the detail view exposes the full JSONB under
        # `evidence` / `route_transitions` / `bgp_transitions`, and one key must
        # not change type between two views of the same resource.
        "evidence_count": len(event.evidence or []),
        "route_transition_count": len(event.route_transitions or []),
        "bgp_transition_count": len(event.bgp_transitions or []),
        "verification_status": (event.verification or {}).get("status"),
    }


def _event_detail(event) -> dict:
    payload = _event_io(event)
    payload.update(
        {
            "trigger": event.trigger or {},
            "decision": event.decision or {},
            "transition_log": event.transition_log or [],
            "route_transitions": event.route_transitions or [],
            "bgp_transitions": event.bgp_transitions or [],
            "blast_radius": event.blast_radius or {},
            "evidence": event.evidence or [],
            "verification": event.verification,
            "attributes": event.attributes or {},
        }
    )
    return payload


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class EvaluateIn(BaseModel):
    """Dry-run target. Omit ``site_id`` to sweep the tenant's first scopes."""

    site_id: uuid.UUID | None = None


class PerturbationIn(BaseModel):
    """Counterfactual knobs. All optional; absent means "change nothing"."""

    links: dict[str, dict[str, Any]] = Field(default_factory=dict)
    scale: dict[str, float] = Field(default_factory=dict)
    weights: dict[str, float] | None = None


class SimulateIn(BaseModel):
    site_id: uuid.UUID
    perturbation: PerturbationIn = Field(default_factory=PerturbationIn)


class VerifyIn(BaseModel):
    """Manual verification. ``incident_id`` only LINKS an existing incident."""

    incident_id: uuid.UUID | None = None


class PolicyCreateIn(BaseModel):
    """Create an operational SLA policy.

    Thresholds are validated against the metric's direction (higher-is-worse vs
    availability), so a contradictory pair is refused with a named field rather
    than stored.
    """

    name: str = Field(min_length=1, max_length=150)
    metric: str
    scope: str = "all_wan"
    scope_id: uuid.UUID | None = None
    description: str | None = None
    warning_threshold: float | None = None
    critical_threshold: float | None = None
    clear_threshold: float | None = None
    min_consecutive_samples: int = 2
    is_active: bool = True
    attributes: dict[str, Any] = Field(default_factory=dict)


class PolicyUpdateIn(BaseModel):
    """Partial update. Unset fields are left alone (explicit ``null`` clears).

    ``model_fields_set`` is what makes this a true PATCH: ``clear_threshold:
    null`` means "remove the clear level", while omitting it means "keep it".
    """

    name: str | None = None
    metric: str | None = None
    scope: str | None = None
    scope_id: uuid.UUID | None = None
    description: str | None = None
    warning_threshold: float | None = None
    critical_threshold: float | None = None
    clear_threshold: float | None = None
    min_consecutive_samples: int | None = None
    is_active: bool | None = None
    attributes: dict[str, Any] | None = None


def _policy_io(policy) -> dict:
    """One policy, in the exact shape the list endpoint returns."""
    return {
        "id": str(policy.id),
        "name": policy.name,
        "description": policy.description,
        "scope": policy.scope,
        "scope_id": str(policy.scope_id) if policy.scope_id else None,
        "metric": policy.metric,
        "warning_threshold": policy.warning_threshold,
        "critical_threshold": policy.critical_threshold,
        "clear_threshold": policy.clear_threshold,
        "min_consecutive_samples": policy.min_consecutive_samples,
        "is_active": policy.is_active,
        "attributes": policy.attributes or {},
        "created_at": policy.created_at.isoformat() if policy.created_at else None,
        "updated_at": policy.updated_at.isoformat() if policy.updated_at else None,
    }


def _policy_error(exc: Exception) -> HTTPException:
    """Map policy errors onto status codes without leaking internals.

    422 = the policy cannot be evaluated as written (or names a scope target that
    does not exist here). 409 = the policy would make the stored set ambiguous.
    """
    if isinstance(exc, PolicyConflictError):
        return HTTPException(
            status_code=409, detail={"field": exc.field, "message": exc.message}
        )
    if isinstance(exc, PolicyValidationError):
        return HTTPException(
            status_code=422, detail={"field": exc.field, "message": exc.message}
        )
    raise exc


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------
@router.get(
    "/summary",
    dependencies=[Depends(require_permission("devices.read"))],
)
async def get_failover_summary(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Episode counts plus the currently open episodes (bounded to 10)."""
    repo = FailoverEventRepository(db)
    summary = await repo.summary(tenant_id)
    open_events, _total = await repo.list(tenant_id, open_only=True, limit=10)
    summary["open_episodes"] = [_event_io(event) for event in open_events]
    summary["terminal_statuses"] = sorted(TERMINAL_STATUSES)
    return summary


@router.get(
    "/policies",
    dependencies=[Depends(require_permission("devices.read"))],
)
async def list_operational_policies(
    scope: str | None = Query(None, description="all_wan | site | link"),
    metric: str | None = Query(None, description=" | ".join(sorted(METRICS))),
    is_active: bool | None = Query(None),
    limit: int = Query(DEFAULT_LIMIT),
    offset: int = Query(0),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """The operational thresholds the detector evaluates live telemetry against."""
    _validate(scope, SCOPES, "scope")
    _validate(metric, METRICS, "metric")
    repo = OperationalSLAPolicyRepository(db)
    rows = await repo.list(
        tenant_id,
        scope=scope,
        metric=metric,
        is_active=is_active,
        limit=_clamp(limit),
        offset=max(0, offset),
    )
    return {
        "total": await repo.count(tenant_id, is_active=is_active),
        "policies": [_policy_io(row) for row in rows],
    }


@router.get(
    "/policies/{policy_id}",
    dependencies=[Depends(require_permission("devices.read"))],
)
async def get_operational_policy(
    policy_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """One policy. A policy of another tenant is a 404, never a 403 or data."""
    policy = await PolicyAdminService(db).get(tenant_id, policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail=f"policy {policy_id} not found")
    return _policy_io(policy)


@router.post(
    "/policies",
    status_code=201,
    dependencies=[Depends(require_permission("monitoring.update"))],
)
async def create_operational_policy(
    payload: PolicyCreateIn,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Create an operational SLA policy for this tenant.

    Gated on ``monitoring.update``: these are the thresholds the monitoring and
    failover path evaluates, so changing them is a configuration act, not an
    operational one — an Operator who may verify a recovery cannot silently
    retune what counts as a breach.

    Nothing is written when validation fails: an unevaluatable policy (no level,
    contradictory levels, a scope_id that is not in this tenant) is refused with
    the offending field, and a policy that would duplicate an existing
    metric+scope is refused as a conflict.
    """
    try:
        policy = await PolicyAdminService(db).create(
            tenant_id,
            name=payload.name,
            metric=payload.metric,
            scope=payload.scope,
            scope_id=payload.scope_id,
            description=payload.description,
            warning_threshold=payload.warning_threshold,
            critical_threshold=payload.critical_threshold,
            clear_threshold=payload.clear_threshold,
            min_consecutive_samples=payload.min_consecutive_samples,
            is_active=payload.is_active,
            attributes=payload.attributes,
        )
    except (PolicyValidationError, PolicyConflictError) as exc:
        raise _policy_error(exc) from exc
    # Project BEFORE committing: the session expires instances on commit.
    body = _policy_io(policy)
    await db.commit()
    return body


@router.patch(
    "/policies/{policy_id}",
    dependencies=[Depends(require_permission("monitoring.update"))],
)
async def update_operational_policy(
    policy_id: uuid.UUID,
    payload: PolicyUpdateIn,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Partially update a policy.

    Only the supplied fields change (an explicit ``null`` clears a threshold).
    The merged result is re-validated as a whole, so moving one level can never
    leave a contradictory pair behind — and a failed update writes nothing.
    """
    service = PolicyAdminService(db)
    policy = await service.get(tenant_id, policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail=f"policy {policy_id} not found")

    changes = {
        field: getattr(payload, field) for field in payload.model_fields_set
    }
    if not changes:
        raise HTTPException(
            status_code=422,
            detail={
                "field": "policy",
                "message": "no updatable field was supplied",
            },
        )
    try:
        policy = await service.update(policy, **changes)
    except (PolicyValidationError, PolicyConflictError) as exc:
        raise _policy_error(exc) from exc
    body = _policy_io(policy)
    await db.commit()
    return body


@router.delete(
    "/policies/{policy_id}",
    dependencies=[Depends(require_permission("monitoring.update"))],
)
async def delete_operational_policy(
    policy_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Soft delete a policy.

    The row is retained (``is_deleted`` + ``deleted_at``) as an audit record and
    immediately stops being evaluated by the detector. Use ``PATCH
    {"is_active": false}`` to disable a policy while keeping it visible in the
    list.
    """
    service = PolicyAdminService(db)
    policy = await service.get(tenant_id, policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail=f"policy {policy_id} not found")
    body = _policy_io(policy)
    await service.delete(policy)
    await db.commit()
    return {"deleted": True, "policy": body}


# ---------------------------------------------------------------------------
# Episodes
# ---------------------------------------------------------------------------
@router.get(
    "/events",
    dependencies=[Depends(require_permission("devices.read"))],
)
async def list_failover_events(
    status: str | None = Query(None, description=" | ".join(sorted(STATUSES))),
    open_only: bool = Query(False, description="only detected/failed_over/recovering"),
    site_id: uuid.UUID | None = Query(None),
    edge_device_id: uuid.UUID | None = Query(None),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    limit: int = Query(DEFAULT_LIMIT),
    offset: int = Query(0),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Observed failover episodes, newest first (tenant-scoped)."""
    _validate(status, STATUSES, "status")
    repo = FailoverEventRepository(db)
    rows, total = await repo.list(
        tenant_id,
        status=status,
        site_id=site_id,
        edge_device_id=edge_device_id,
        open_only=open_only,
        since=since,
        until=until,
        limit=_clamp(limit),
        offset=max(0, offset),
    )
    return {
        "total": total,
        "limit": _clamp(limit),
        "offset": max(0, offset),
        "events": [_event_io(event) for event in rows],
    }


@router.get(
    "/events/{event_id}",
    dependencies=[Depends(require_permission("devices.read"))],
)
async def get_failover_event(
    event_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """One episode with its full captured record and provenance-tagged replay."""
    event = await FailoverEventRepository(db).get(event_id, tenant_id)
    if event is None:
        raise HTTPException(status_code=404, detail=f"failover event {event_id} not found")
    replay = await ReplayService(db).reconstruct(tenant_id, event_id)
    return {"event": _event_detail(event), "replay": replay}


@router.get(
    "/events/{event_id}/verification",
    dependencies=[Depends(require_permission("devices.read"))],
)
async def get_failover_verification(
    event_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """The stored verification, plus a fresh read-only check of right now.

    Returning both makes the distinction visible: ``stored`` is what was
    concluded when the episode closed; ``current`` is what telemetry says at
    this instant, computed without persisting anything.
    """
    event = await FailoverEventRepository(db).get(event_id, tenant_id)
    if event is None:
        raise HTTPException(status_code=404, detail=f"failover event {event_id} not found")
    current = await RecoveryVerificationService(db).verify_event(
        tenant_id, event, persist=False, trigger="on_demand"
    )
    return {
        "event_id": str(event.id),
        "status": event.status,
        "stored": event.verification,
        "current": current,
    }


@router.post(
    "/events/{event_id}/verify",
    dependencies=[Depends(require_permission("incidents.write"))],
)
async def verify_failover_recovery(
    event_id: uuid.UUID,
    payload: VerifyIn | None = None,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Manually verify recovery against live telemetry and record the result.

    The only state-changing endpoint in this surface. It records a verification
    result on the episode (and optionally LINKS an existing incident); it never
    creates an incident, never changes the episode's FSM status, and never
    touches the network. If the checks come back ``fail``/``unknown`` that is
    what gets recorded - there is no override.
    """
    repo = FailoverEventRepository(db)
    event = await repo.get(event_id, tenant_id)
    if event is None:
        raise HTTPException(status_code=404, detail=f"failover event {event_id} not found")

    incident_id = payload.incident_id if payload is not None else None
    if incident_id is not None:
        incident = await IncidentRepository(db).get(incident_id, tenant_id)
        if incident is None:
            raise HTTPException(status_code=404, detail=f"incident {incident_id} not found")

    verification = await RecoveryVerificationService(db).verify_event(
        tenant_id, event, persist=True, trigger="manual"
    )
    incident_value = None
    if incident_id is not None:
        event = await repo.update(event, incident_id=incident_id)
        incident_value = str(event.incident_id) if event.incident_id else None
    # Capture the response before committing: the session expires objects on
    # commit, and `get_db` does not commit for us (no route in this codebase
    # relies on an implicit commit).
    event_id_value = str(event.id)
    status_value = event.status
    await db.commit()
    return {
        "event_id": event_id_value,
        "status": status_value,
        "incident_id": incident_value,
        "verification": verification,
    }


# ---------------------------------------------------------------------------
# Dry run / counterfactual / topology impact (all read-only)
# ---------------------------------------------------------------------------
@router.post(
    "/evaluate",
    dependencies=[Depends(require_permission("devices.read"))],
)
async def evaluate_failover(
    payload: EvaluateIn | None = None,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Run one detection round per scope WITHOUT writing anything.

    With ``site_id`` set it evaluates that edge; without it, the tenant's first
    ``MAX_DRY_RUN_SCOPES`` scopes are swept so an operator can see which edges
    would open an episode right now.
    """
    detector = FailoverDetectorService(db, publish=False)
    site_id = payload.site_id if payload is not None else None
    if site_id is not None:
        scopes: list[Any] = [site_id]
        truncated = False
    else:
        all_scopes = await list_scopes(db, tenant_id)
        scopes = all_scopes[:MAX_DRY_RUN_SCOPES]
        truncated = len(all_scopes) > len(scopes)

    reports = [await detector.dry_run_scope(tenant_id, scope) for scope in scopes]
    return {
        "dry_run": True,
        "scopes_evaluated": len(reports),
        "scopes_truncated": truncated,
        "would_open": sum(
            1 for report in reports if str(report.get("action", "")).startswith("open_")
        ),
        "scopes": reports,
    }


@router.post(
    "/simulate",
    dependencies=[Depends(require_permission("devices.read"))],
)
async def simulate_failover(
    payload: SimulateIn,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Counterfactual: what the path decision would do under a scenario.

    Read-only by construction (``CounterfactualSimulator`` has no write path).
    The result describes the CURRENT scoring of the CURRENT snapshot, so it is a
    counterfactual, not a prediction.
    """
    perturbation: dict[str, Any] = payload.perturbation.model_dump(exclude_none=True)
    return await CounterfactualSimulator(db).simulate_for_site(
        tenant_id, payload.site_id, perturbation
    )


@router.get(
    "/blast-radius",
    dependencies=[Depends(require_permission("devices.read"))],
)
async def get_blast_radius(
    resource_id: uuid.UUID | None = Query(
        None, description="device/resource id to seed the traversal from"
    ),
    link_id: uuid.UUID | None = Query(
        None, description="topology link id to seed the traversal from"
    ),
    depth: int = Query(3, ge=1, le=6),
    max_nodes: int = Query(100, ge=1, le=500),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Who depends on this link/device, from the real topology graph."""
    if (resource_id is None) == (link_id is None):
        raise HTTPException(
            status_code=422, detail="supply exactly one of resource_id or link_id"
        )
    service = BlastRadiusService(db, max_depth=depth, max_nodes=max_nodes)
    if link_id is not None:
        from app.repositories.topology_link_repository import TopologyLinkRepository

        link = await TopologyLinkRepository(db).get(link_id, tenant_id)
        if link is None:
            raise HTTPException(status_code=404, detail=f"topology link {link_id} not found")
        return await service.compute_for_link(tenant_id, link)
    return await service.compute_for_device(tenant_id, resource_id)
