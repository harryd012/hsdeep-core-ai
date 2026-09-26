"""Discovery API — exposes the discovery core through the existing API stack.

Routes:
  POST /infrastructure/discovery             - probe + recognise + persist targets
  GET  /infrastructure/discovery/jobs        - discovery job history
  GET  /infrastructure/discovery/jobs/{id}   - one discovery job

Reuses the EXISTING run-tracking model (`CollectorRun`) as the discovery job
record, so no new table or migration is introduced. Reuses the existing
Device / DeviceCapabilityRepository / ResourceRelationship stack through
`DiscoveryService` — the frontend never inserts a device directly.

Secret handling: an SNMP community may be supplied per request and is used only
to build a probe. It is never echoed back, never stored on the job row, and
never written to logs.

Execution model: probes run inside the request (bounded by target count and the
per-protocol timeouts in probes.py). Queued/background execution via the
existing scheduler is NOT wired yet — the job row is a durable record of what
ran, not a queue handle.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, get_tenant_id, require_permission
from app.core.db import get_db
from app.models.collector_run import CollectorRun
from app.models.monitoring_source import MonitoringSource
from app.services.discovery import DiscoveryService
from app.services.discovery.probes import expand_targets, probe_target
from app.services.discovery.profiles import (
    DEFAULT_PROFILE,
    UnknownDiscoveryProfile,
    profile_catalog,
    resolve_profile,
)
from app.services.resource_context import build_resource_context

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/infrastructure/discovery", tags=["discovery"])

#: monitoring source that owns discovered resources when no collector is named.
DISCOVERY_SOURCE_NAME = "network-discovery"


class DiscoveryRequest(BaseModel):
    target: str = Field(..., min_length=1, description="IP, hostname, or CIDR")
    #: Phase 9.4: name a DiscoveryProfile to derive the protocol set for a
    #: request that does not state one. An explicit ``protocols`` list always
    #: wins, and naming no profile leaves today's defaults in place.
    profile: str | None = Field(
        default=None, max_length=32, description="basic | balanced | deep | cloud | topology"
    )
    protocols: list[str] = Field(
        default_factory=lambda: ["icmp", "snmp", "https", "ssh"]
    )
    monitoring_source_id: uuid.UUID | None = None
    site_id: uuid.UUID | None = None
    #: SNMPv2c community for THIS probe only. Never persisted, never returned.
    snmp_community: str | None = None
    snmp_version: int = 1


class DiscoveryJobOut(BaseModel):
    job_id: str
    status: str
    started_at: str
    finished_at: str | None = None
    devices_upserted: int = 0
    error: str | None = None


@router.post("")
async def run_discovery(
    payload: DiscoveryRequest,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    principal: Principal = Depends(require_permission("devices.write")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Probe the target(s), recognise, and persist into the existing inventory."""
    # Phase 9.4: profile resolution is explicit and fails loudly. Only the
    # PROTOCOL SET is taken here - this endpoint already bounds host count and
    # timeouts itself, and re-implementing scope limits here would create the
    # second execution path the architecture forbids.
    try:
        profile = resolve_profile(payload.profile) if payload.profile else None
    except UnknownDiscoveryProfile as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    protocols = payload.protocols
    if profile is not None and "protocols" not in payload.model_fields_set:
        protocols = list(profile.allowed_protocols)

    targets = expand_targets(payload.target)
    if not targets:
        raise HTTPException(
            status_code=422, detail="target did not resolve to any address"
        )

    source = await _resolve_source(db, tenant_id, payload.monitoring_source_id)

    job = CollectorRun(
        tenant_id=tenant_id,
        monitoring_source_id=source.id,
        trigger="manual",
        status="running",
        started_at=datetime.now(timezone.utc),
    )
    db.add(job)
    await db.flush()

    bundles = []
    evidence_batch = []
    for target in targets:
        bundle = await probe_target(
            target,
            protocols=protocols,
            community=payload.snmp_community or "public",
            snmp_version=payload.snmp_version,
        )
        bundles.append(bundle)
        if bundle.evidence is not None:
            evidence_batch.append(bundle.evidence)

    service = DiscoveryService(db)
    result = await service.discover(
        tenant_id=tenant_id,
        monitoring_source_id=source.id,
        evidence=evidence_batch,
        discovered_by="discovery-api",
        site_id=payload.site_id,
    )

    job.status = {
        "SUCCESS": "success", "PARTIAL": "partial", "FAILED": "error",
    }.get(result.status, "partial")
    job.devices_upserted = result.created
    job.finished_at = datetime.now(timezone.utc)
    job.error = "; ".join(result.errors)[:2000] or None
    await db.commit()

    logger.info(
        "discovery job %s by %s: target=%s created=%s updated=%s unknown=%s",
        job.id, principal.user_id, payload.target, result.created,
        result.updated, result.unknown,
    )

    return {
        "job_id": str(job.id),
        "status": job.status,
        "target": payload.target,
        # Effective configuration actually used (Phase 9.4): the caller can see
        # which profile ran and which protocols it resolved to.
        "profile": profile.name if profile else None,
        "protocols": list(protocols),
        "monitoring_source_id": str(source.id),
        "probe_results": [b.as_dict() for b in bundles],
        "result": result.as_dict(),
    }


@router.get("/jobs", response_model=list[DiscoveryJobOut])
async def list_discovery_jobs(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _principal: Principal = Depends(require_permission("devices.read")),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> list[DiscoveryJobOut]:
    stmt = (
        select(CollectorRun)
        .where(
            CollectorRun.tenant_id == tenant_id,
            CollectorRun.trigger == "manual",
        )
        .order_by(CollectorRun.started_at.desc())
        .limit(limit)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return [_job_out(r) for r in rows]


@router.get("/profiles")
async def list_discovery_profiles(
    tenant_id: uuid.UUID = Depends(get_tenant_id),  # noqa: ARG001 - authz only
    _principal: Principal = Depends(require_permission("devices.read")),
) -> dict:
    """The declared DiscoveryProfiles and their effective configuration.

    Global configuration, like the capability/provider catalogues, so the body
    is tenant-independent - but it still requires an authenticated principal and
    the standard read permission, so an unauthenticated caller cannot enumerate
    how this deployment scans.
    """
    return {
        "default_profile": DEFAULT_PROFILE,
        "profiles": profile_catalog(),
    }


@router.get("/profiles/{name}")
async def get_discovery_profile(
    name: str,
    tenant_id: uuid.UUID = Depends(get_tenant_id),  # noqa: ARG001 - authz only
    _principal: Principal = Depends(require_permission("devices.read")),
) -> dict:
    """One profile by name. An unknown name is a 404 that lists the valid set."""
    try:
        return resolve_profile(name).to_dict()
    except UnknownDiscoveryProfile as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/resources/{device_id}/context")
async def get_resource_context(
    device_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Normalized context for one resource: identity, capabilities, discovery
    provenance, relationships, and the compact `ai_context` block.

    Read-only and tenant-scoped. Serves the Device Details view and the existing
    AI/RCA engines — no secrets are included, and write-capability flags are
    reported separately so no unsupported action is ever offered.
    """
    context = await build_resource_context(db, tenant_id, device_id)
    if context is None:
        raise HTTPException(status_code=404, detail=f"Resource {device_id} not found")
    return context


@router.get("/topology/relationships")
async def list_topology_relationships(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    limit: int = Query(default=200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Real relationship edges for the topology view.

    Only evidence-backed edges exist in this table (each carries
    `discovered_by` + `confidence`), so the graph is never hard-coded.
    """
    from app.models.resource_relationship import ResourceRelationship

    stmt = (
        select(ResourceRelationship)
        .where(
            ResourceRelationship.tenant_id == tenant_id,
            ResourceRelationship.is_deleted.is_(False),
        )
        .limit(limit)
    )
    edges = list((await db.execute(stmt)).scalars().all())
    return {
        "count": len(edges),
        "edges": [
            {
                "source_id": str(e.source_resource_id),
                "target_id": str(e.target_resource_id),
                "relationship_type": e.relationship_type,
                "label": e.label,
                "confidence": e.confidence,
                "discovered_by": e.discovered_by,
                "is_inferred": e.is_inferred,
                "status": e.status,
                "first_seen": e.first_seen.isoformat() if e.first_seen else None,
                "last_seen": e.last_seen.isoformat() if e.last_seen else None,
            }
            for e in edges
        ],
    }


@router.get("/jobs/{job_id}", response_model=DiscoveryJobOut)
async def get_discovery_job(
    job_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _principal: Principal = Depends(require_permission("devices.read")),
    db: AsyncSession = Depends(get_db),
) -> DiscoveryJobOut:
    stmt = select(CollectorRun).where(
        CollectorRun.id == job_id, CollectorRun.tenant_id == tenant_id
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"Discovery job {job_id} not found"
        )
    return _job_out(row)


def _job_out(row: CollectorRun) -> DiscoveryJobOut:
    return DiscoveryJobOut(
        job_id=str(row.id),
        status=row.status,
        started_at=row.started_at.isoformat() if row.started_at else "",
        finished_at=row.finished_at.isoformat() if row.finished_at else None,
        devices_upserted=row.devices_upserted or 0,
        error=row.error,
    )


async def _resolve_source(
    db: AsyncSession, tenant_id: uuid.UUID, source_id: uuid.UUID | None
) -> MonitoringSource:
    """The caller's collector, or the tenant's discovery source (created once)."""
    if source_id is not None:
        stmt = select(MonitoringSource).where(
            MonitoringSource.id == source_id,
            MonitoringSource.tenant_id == tenant_id,
        )
        source = (await db.execute(stmt)).scalar_one_or_none()
        if source is None:
            raise HTTPException(status_code=404, detail="monitoring source not found")
        return source

    stmt = select(MonitoringSource).where(
        MonitoringSource.tenant_id == tenant_id,
        MonitoringSource.name == DISCOVERY_SOURCE_NAME,
    )
    source = (await db.execute(stmt)).scalar_one_or_none()
    if source is None:
        source = MonitoringSource(
            tenant_id=tenant_id,
            name=DISCOVERY_SOURCE_NAME,
            source_type="snmp",
            status="pending",
            is_active=True,
            poll_interval_seconds=300,
        )
        db.add(source)
        await db.flush()
    return source

