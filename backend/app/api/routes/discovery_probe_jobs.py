"""DiscoveryProbeJob review + materialization API.

This is the REVIEW/MATERIALIZE half of Universal Discovery's two-stage flow.
The probe endpoint (``/infrastructure/discovery/scopes/{id}/probe``) persists
observation-only results; these routes let an operator review them and turn
ELIGIBLE reachable hosts into canonical devices through the existing
``DiscoveryService.discover()`` path. The probe endpoint's observation-only
contract is untouched.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.discovery import _resolve_source
from app.core.auth import Principal, get_tenant_id, require_permission
from app.core.db import get_db
from app.core.entitlements import get_billing_service
from app.repositories.discovery_probe_repository import DiscoveryProbeRepository
from app.schemas.discovery_probe import (
    DiscoveryProbeHostOut,
    DiscoveryProbeJobDetailOut,
    DiscoveryProbeJobOut,
    MaterializeHostOutcome,
    MaterializeRequest,
    MaterializeResultOut,
)
from app.services.billing_service import BillingService
from app.services.discovery.materialization import (
    ProbeJobNotMaterializable,
    ProbeJobNotFound,
    UnknownHostsError,
    materialize_probe_job,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/infrastructure/discovery/probe-jobs", tags=["discovery"]
)


def _get_repo(db: AsyncSession = Depends(get_db)) -> DiscoveryProbeRepository:
    return DiscoveryProbeRepository(db)


@router.get("", response_model=list[DiscoveryProbeJobOut])
async def list_probe_jobs(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _principal: Principal = Depends(require_permission("devices.read")),
    scope_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    repo: DiscoveryProbeRepository = Depends(_get_repo),
) -> list[DiscoveryProbeJobOut]:
    """Completed/attempted probe jobs for this tenant, newest first."""
    jobs = await repo.list_jobs(tenant_id, scope_id=scope_id, limit=limit)
    return [DiscoveryProbeJobOut.model_validate(j) for j in jobs]


@router.get("/{job_id}", response_model=DiscoveryProbeJobDetailOut)
async def get_probe_job(
    job_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _principal: Principal = Depends(require_permission("devices.read")),
    repo: DiscoveryProbeRepository = Depends(_get_repo),
) -> DiscoveryProbeJobDetailOut:
    """One job with its persisted per-host evidence."""
    job = await repo.get_job(job_id, tenant_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Probe job {job_id} not found")
    hosts = await repo.get_hosts(job_id, tenant_id)
    base = DiscoveryProbeJobOut.model_validate(job)
    return DiscoveryProbeJobDetailOut(
        **base.model_dump(),
        hosts=[DiscoveryProbeHostOut.model_validate(h) for h in hosts],
    )


@router.post("/{job_id}/materialize", response_model=MaterializeResultOut)
async def materialize_probe_job_route(
    job_id: uuid.UUID,
    payload: MaterializeRequest,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _principal: Principal = Depends(require_permission("devices.write")),
    db: AsyncSession = Depends(get_db),
    billing: BillingService = Depends(get_billing_service),
) -> MaterializeResultOut:
    """Materialize selected persisted hosts into canonical devices.

    Every address must already be a host on the completed, tenant-scoped job;
    arbitrary client-supplied IPs are rejected. Device creation, identity
    resolution, deduplication and quota are all delegated to
    ``DiscoveryService.discover()`` - this route never persists a device itself.
    """
    try:
        report = await materialize_probe_job(
            db=db,
            tenant_id=tenant_id,
            job_id=job_id,
            addresses=payload.hosts,
            monitoring_source_id=(await _resolve_source(db, tenant_id, None)).id,
            billing=billing,
        )
    except ProbeJobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProbeJobNotMaterializable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except UnknownHostsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    await db.commit()

    return MaterializeResultOut(
        job_id=job_id,
        materialization_state=report.state,
        created=report.created,
        existing=report.existing,
        rejected=report.rejected,
        failed=report.failed,
        hosts=[
            MaterializeHostOutcome(
                address=o.address,
                outcome=o.outcome,
                device_id=uuid.UUID(o.device_id) if o.device_id else None,
                error=o.error,
            )
            for o in report.hosts
        ],
    )
