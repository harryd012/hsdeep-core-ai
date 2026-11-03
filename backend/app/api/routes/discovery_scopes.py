"""Tenant-scoped DiscoveryScope API (Phase 6.3 §10/§11).

Every operation is scoped to the caller's tenant from the verified token; a
tenant can never read or mutate another tenant's scope. Responses carry no
credential material - only whether a credential reference is configured.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, get_tenant_id, require_permission
from app.core.db import get_db
from app.models.discovery_scope import SCOPE_TYPES, DiscoveryScope
from app.repositories.discovery_probe_repository import DiscoveryProbeRepository
from app.repositories.discovery_scope_repository import DiscoveryScopeRepository
from app.schemas.discovery_scope import (
    DiscoveryScopeCreate,
    DiscoveryScopeOut,
    DiscoveryScopeUpdate,
    ScopeProbeHostOut,
    ScopeProbeResultOut,
)
from app.services.discovery.profiles import (
    UnknownDiscoveryProfile,
    apply_profile,
)
from app.services.audit_service import AuditService
from app.services.discovery.schedule import ScheduleInvalid, next_run_after, normalize_schedule
from app.services.discovery.scope_probe_service import ScopeProbeFailed, run_scope_probe
from app.services.discovery.scope_runner import ScopeNotProbeable
from app.services.discovery.scope_target import (
    ScopeTargetInvalid,
    validate_protocols,
    validate_target,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/infrastructure/discovery/scopes", tags=["discovery"])


def _get_repo(db: AsyncSession = Depends(get_db)) -> DiscoveryScopeRepository:
    return DiscoveryScopeRepository(db)


def _get_probe_repo(db: AsyncSession = Depends(get_db)) -> DiscoveryProbeRepository:
    return DiscoveryProbeRepository(db)


def to_out(scope: DiscoveryScope) -> DiscoveryScopeOut:
    """Safe projection. Credentials are reported as configured/not, never read."""
    return DiscoveryScopeOut(
        id=scope.id,
        tenant_id=scope.tenant_id,
        name=scope.name,
        scope_type=scope.scope_type,
        target=scope.target,
        enabled=scope.enabled,
        credential_profile_id=scope.credential_profile_id,
        credential_configured=scope.credential_profile_id is not None,
        allowed_protocols=list(scope.allowed_protocols or []),
        max_depth=scope.max_depth,
        max_hosts=scope.max_hosts,
        max_concurrency=scope.max_concurrency,
        timeout=float(scope.timeout),
        excluded_targets=list(scope.excluded_targets or []),
        schedule=scope.schedule,
        next_run_at=scope.next_run_at,
        status=scope.status,
        last_run_at=scope.last_run_at,
        last_error=scope.last_error,
        created_at=scope.created_at,
        updated_at=scope.updated_at,
    )


def _validate(scope_type: str, target: str, protocols: list[str] | None):
    """Normalise + validate, translating to a 422 for the API caller."""
    try:
        clean_target = validate_target(scope_type, target)
        clean_protocols = validate_protocols(protocols)
    except ScopeTargetInvalid as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return clean_target, clean_protocols


def _compose_profile(body: DiscoveryScopeCreate) -> dict:
    """Compose a named DiscoveryProfile UNDER the request's own values (9.4).

    The schema's own defaults are used when no profile is named, so existing
    callers keep exactly today's behaviour. An unknown profile is a 422 that
    names the valid profiles - never a silent fall-back to a broader one.
    """
    fields = body.model_dump()
    profile_name = fields.pop("profile", None)
    try:
        return apply_profile(
            profile_name,
            fields,
            explicit=set(body.model_fields_set) - {"profile"},
        )
    except UnknownDiscoveryProfile as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


async def _get_or_404(
    repo: DiscoveryScopeRepository, scope_id: uuid.UUID, tenant_id: uuid.UUID
) -> DiscoveryScope:
    scope = await repo.get(scope_id, tenant_id)
    if scope is None:
        # 404 (not 403) so the API never confirms another tenant's scope exists.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scope not found")
    return scope


def _schedule(value: str | None) -> str | None:
    """Normalized schedule, or a 422 naming the accepted form."""
    try:
        return normalize_schedule(value)
    except ScheduleInvalid as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


#: Scope fields recorded in the audit trail (never credential material).
_AUDITED = (
    "name", "scope_type", "target", "enabled", "credential_profile_id", "allowed_protocols",
    "max_depth", "max_hosts", "max_concurrency", "timeout", "excluded_targets", "schedule",
)


def _audit_value(value):
    return str(value) if isinstance(value, uuid.UUID) else value


async def _audit(
    db: AsyncSession, principal: Principal, scope: DiscoveryScope, action: str, description: str,
    old: dict | None = None, new: dict | None = None,
) -> None:
    await AuditService(db).record_entity(
        tenant_id=scope.tenant_id,
        action=action,
        entity_type="discovery_scope",
        entity_id=scope.id,
        actor_id=principal.user_id,
        actor_email=principal.email,
        description=description,
        old_values={k: _audit_value(v) for k, v in (old or {}).items()} or None,
        new_values={k: _audit_value(v) for k, v in (new or {}).items()} or None,
    )


@router.get("", response_model=list[DiscoveryScopeOut])
async def list_scopes(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _principal: Principal = Depends(require_permission("devices.read")),
    repo: DiscoveryScopeRepository = Depends(_get_repo),
):
    """All scopes belonging to the caller's tenant."""
    return [to_out(s) for s in await repo.list(tenant_id)]


@router.post("", response_model=DiscoveryScopeOut, status_code=status.HTTP_201_CREATED)
async def create_scope(
    body: DiscoveryScopeCreate,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    principal: Principal = Depends(require_permission("devices.write")),
    db: AsyncSession = Depends(get_db),
    repo: DiscoveryScopeRepository = Depends(_get_repo),
):
    """Create a scope. Target is validated against its scope type; a schedule
    first runs one interval from now."""
    if body.scope_type not in SCOPE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"unknown scope_type {body.scope_type!r}; expected {sorted(SCOPE_TYPES)}",
        )
    if await repo.get_by_name(body.name, tenant_id) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A scope named {body.name!r} already exists for this tenant",
        )

    # Phase 9.4: a named profile supplies defaults for the fields this request
    # does not state itself; anything stated explicitly always wins.
    fields = _compose_profile(body)
    target, protocols = _validate(
        body.scope_type, body.target, fields["allowed_protocols"]
    )
    schedule = _schedule(body.schedule)
    scope = await repo.create(
        tenant_id=tenant_id,
        name=body.name,
        scope_type=body.scope_type,
        target=target,
        enabled=body.enabled,
        credential_profile_id=body.credential_profile_id,
        allowed_protocols=protocols,
        max_depth=fields["max_depth"],
        max_hosts=fields["max_hosts"],
        max_concurrency=fields["max_concurrency"],
        timeout=fields["timeout"],
        excluded_targets=list(fields["excluded_targets"] or []),
        schedule=schedule,
        next_run_at=next_run_after(schedule, datetime.now(timezone.utc)),
    )
    await _audit(
        db, principal, scope, "discovery_scope.created", f"Discovery scope {scope.name} created",
        new={k: getattr(scope, k) for k in _AUDITED},
    )
    await db.commit()
    return to_out(scope)


@router.get("/{scope_id}", response_model=DiscoveryScopeOut)
async def get_scope(
    scope_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _principal: Principal = Depends(require_permission("devices.read")),
    repo: DiscoveryScopeRepository = Depends(_get_repo),
):
    return to_out(await _get_or_404(repo, scope_id, tenant_id))


@router.patch("/{scope_id}", response_model=DiscoveryScopeOut)
async def update_scope(
    scope_id: uuid.UUID,
    body: DiscoveryScopeUpdate,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    principal: Principal = Depends(require_permission("devices.write")),
    db: AsyncSession = Depends(get_db),
    repo: DiscoveryScopeRepository = Depends(_get_repo),
):
    scope = await _get_or_404(repo, scope_id, tenant_id)
    fields = body.model_dump(exclude_unset=True)
    # Phase 9.4: re-profiling is explicit. A named profile re-states the limits
    # this request did not mention; nothing happens when none is named, so
    # existing PATCH callers are unaffected.
    profile_name = fields.pop("profile", None)
    if profile_name is not None:
        try:
            fields = apply_profile(profile_name, fields, explicit=set(fields))
        except UnknownDiscoveryProfile as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc

    # A type/target change is re-validated as a PAIR, so a scope can never be
    # left holding a target that does not match its type.
    new_type = fields.get("scope_type", scope.scope_type)
    new_target = fields.get("target", scope.target)
    if "scope_type" in fields or "target" in fields or "allowed_protocols" in fields:
        if new_type not in SCOPE_TYPES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"unknown scope_type {new_type!r}",
            )
        protocols = fields.get("allowed_protocols", scope.allowed_protocols)
        target, clean_protocols = _validate(new_type, new_target, protocols)
        fields["scope_type"] = new_type
        fields["target"] = target
        fields["allowed_protocols"] = clean_protocols
    if "excluded_targets" in fields:
        fields["excluded_targets"] = list(fields["excluded_targets"] or [])
    if "schedule" in fields:
        fields["schedule"] = _schedule(fields["schedule"])
        if fields["schedule"] != scope.schedule:
            # A new interval counts from now; an unchanged one keeps its slot.
            fields["next_run_at"] = next_run_after(fields["schedule"], datetime.now(timezone.utc))
    if fields.get("enabled") is True and not scope.enabled:
        # Re-enabled: the interval restarts, so a long-paused scope does not
        # sweep at the next tick.
        fields["next_run_at"] = next_run_after(fields.get("schedule", scope.schedule), datetime.now(timezone.utc))

    old = {k: getattr(scope, k) for k in _AUDITED if k in fields and fields[k] != getattr(scope, k)}
    new = {k: fields[k] for k in old}
    scope = await repo.update(scope, **fields)
    if new:
        await _audit(
            db, principal, scope, "discovery_scope.updated",
            f"Discovery scope {scope.name} changed: {', '.join(sorted(new))}", old=old, new=new,
        )
    await db.commit()
    return to_out(scope)


@router.delete("/{scope_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_scope(
    scope_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    principal: Principal = Depends(require_permission("devices.write")),
    db: AsyncSession = Depends(get_db),
    repo: DiscoveryScopeRepository = Depends(_get_repo),
):
    scope = await _get_or_404(repo, scope_id, tenant_id)
    await repo.delete(scope)
    await _audit(db, principal, scope, "discovery_scope.deleted", f"Discovery scope {scope.name} deleted")
    await db.commit()


@router.post("/{scope_id}/probe", response_model=ScopeProbeResultOut)
async def probe_scope_target(
    scope_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _principal: Principal = Depends(require_permission("devices.write")),
    db: AsyncSession = Depends(get_db),
    repo: DiscoveryScopeRepository = Depends(_get_repo),
    probe_repo: DiscoveryProbeRepository = Depends(_get_probe_repo),
):
    """Run the existing bounded probe over this scope's target.

    OBSERVATION ONLY: it records when the scope last ran, persists the probe
    evidence as a durable ``DiscoveryProbeJob`` (for later review/materialize),
    and returns the evidence. It creates no canonical resources - device
    creation, identity resolution and quota all stay on the separate materialize
    step over the existing collection path.
    """
    scope = await _get_or_404(repo, scope_id, tenant_id)
    if not scope.enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Scope is disabled"
        )

    try:
        outcome = await run_scope_probe(repo, probe_repo, scope, trigger="manual")
    except ScopeNotProbeable as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ScopeProbeFailed as exc:
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="probe failed"
        ) from exc
    await db.commit()
    job, report = outcome.job, outcome.report

    return ScopeProbeResultOut(
        scope_id=scope.id,
        job_id=job.id,
        target=scope.target,
        hosts_probed=report.hosts_probed,
        hosts_total=report.hosts_total,
        truncated=report.truncated,
        reachable=len(report.reachable),
        protocol_counts=report.protocol_counts(),
        hosts=[
            ScopeProbeHostOut(
                address=h.address,
                reachable=h.reachable,
                latency_ms=h.latency_ms,
                protocols=dict(h.protocols),
                evidence=list(h.evidence),
            )
            for h in report.hosts
        ],
    )
