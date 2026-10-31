"""
Monitoring Engine API.

NOTE(auth): every endpoint here derives its tenant from the authenticated
principal via `Depends(get_tenant_id)` - the client cannot select a tenant.
Where a write schema still requires a `tenant_id` field for backwards
compatibility, the route OVERRIDES it with the principal's tenant before
persisting (see `create_source`).

NOTE(data source): sensor reads here serve the CANONICAL persisted rows written
by the Collector Framework (`CollectionOrchestrator` -> `BundleSaver` ->
`SensorRepository`). There is no provider-specific passthrough in this module:
PRTG - like every other vendor - is read through `/sources/{source_id}/sensors`
and `/sensors`, and the per-source read proves the source belongs to the
caller's tenant before exposing anything.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from app.core.auth import get_tenant_id, require_permission
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.entitlements import require_active_subscription, require_entitlement
from app.repositories.alert_repository import AlertRepository
from app.repositories.collector_run_repository import CollectorRunRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.event_repository import EventRepository
from app.repositories.interface_repository import InterfaceRepository
from app.repositories.metric_repository import MetricRepository
from app.repositories.monitoring_source_repository import MonitoringSourceRepository
from app.repositories.sensor_repository import SensorRepository
from app.schemas.monitoring import (
    ConnectionTestResult,
    MonitoringSourceCreate,
    MonitoringSourceOut,
    MonitoringSourceUpdate,
    ReadinessReportOut,
    ReadinessResultOut,
    SensorOut,
    SupportedSourceType,
    SyncResult,
)
from app.services.alert_service import AlertService
from app.services.monitoring.bundle_saver import BundleSaver
from app.services.monitoring.collection_orchestrator import CollectionOrchestrator
from app.services.monitoring.monitoring_service import MonitoringService, MonitoringSourceNotFound
from app.services.monitoring.source_health_manager import SourceHealthManager
from app.repositories.source_health_audit_repository import SourceHealthAuditRepository
from app.services.events.event_bus import get_event_bus
from app.core.auth import Principal, generate_refresh_token, get_current_user, hash_refresh_token
from app.core.entitlements import evaluate_resource_limit, get_billing_service
from app.models.collector_credential import CollectorCredential
from app.models.collector_enrollment_token import CollectorEnrollmentToken
from app.models.site import Site
from app.services.audit_service import AuditService
from app.services.billing_service import BillingService

router = APIRouter(prefix="/monitoring", tags=["monitoring"])



# Suspended/canceled/incomplete tenants cannot WRITE (402);
# reads stay open and past_due keeps its grace period.
_active_sub_guard = Depends(require_active_subscription)
def get_monitoring_service(db: AsyncSession = Depends(get_db)) -> MonitoringService:
    return MonitoringService(
        source_repo=MonitoringSourceRepository(db),
        sensor_repo=SensorRepository(db),
    )


def get_collection_orchestrator(db: AsyncSession = Depends(get_db)) -> CollectionOrchestrator:
    return CollectionOrchestrator(
        run_repo=CollectorRunRepository(db),
        saver=BundleSaver(
            DeviceRepository(db),
            InterfaceRepository(db),
            SensorRepository(db),
            MetricRepository(db),
            EventRepository(db),
        ),
        alert_service=AlertService(AlertRepository(db)),
        source_repo=MonitoringSourceRepository(db),
        health_manager=SourceHealthManager(
            MonitoringSourceRepository(db),
            SourceHealthAuditRepository(db),
            get_event_bus(),
        ),
    )


@router.get("/source-types", response_model=list[SupportedSourceType])
def list_source_types(service: MonitoringService = Depends(get_monitoring_service)):
    """Every monitoring platform the registry currently knows about, and
    whether it has a real (non-scaffolded) implementation yet."""
    return service.supported_source_types()


@router.post("/sources", dependencies=[Depends(require_permission("monitoring.create")), _active_sub_guard, Depends(require_entitlement("collectors"))], response_model=MonitoringSourceOut, status_code=201)
async def create_source(
    payload: MonitoringSourceCreate,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: MonitoringService = Depends(get_monitoring_service),
):
    try:
        # Tenant is taken from the authenticated principal, never the body -
        # a client-supplied tenant_id would be a tenant-isolation hole.
        fields = payload.model_dump()
        fields["tenant_id"] = tenant_id
        source = await service.create_source(**fields)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await db.commit()
    return source


@router.get("/sources", response_model=list[MonitoringSourceOut])
async def list_sources(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    site_id: uuid.UUID | None = None,
    source_type: str | None = None,
    is_active: bool | None = None,
    service: MonitoringService = Depends(get_monitoring_service),
):
    return await service.list_sources(
        tenant_id, site_id=site_id, source_type=source_type, is_active=is_active
    )


@router.get("/sources/{source_id}", response_model=MonitoringSourceOut)
async def get_source(
    source_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: MonitoringService = Depends(get_monitoring_service),
):
    try:
        return await service.get_source(source_id, tenant_id)
    except MonitoringSourceNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/sources/{source_id}", dependencies=[Depends(require_permission("monitoring.update")), _active_sub_guard], response_model=MonitoringSourceOut)
async def update_source(
    source_id: uuid.UUID,
    payload: MonitoringSourceUpdate,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: MonitoringService = Depends(get_monitoring_service),
):
    try:
        source = await service.get_source(source_id, tenant_id)
    except MonitoringSourceNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    source = await service.update_source(source, **updates)
    await db.commit()
    return source


@router.delete("/sources/{source_id}", dependencies=[Depends(require_permission("monitoring.delete")), _active_sub_guard], status_code=204)
async def delete_source(
    source_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: MonitoringService = Depends(get_monitoring_service),
):
    try:
        source = await service.get_source(source_id, tenant_id)
    except MonitoringSourceNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await service.delete_source(source)
    await db.commit()


@router.post("/sources/{source_id}/test-connection", dependencies=[Depends(require_permission("monitoring.update")), _active_sub_guard], response_model=ConnectionTestResult)
async def test_connection(
    source_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: MonitoringService = Depends(get_monitoring_service),
):
    try:
        source = await service.get_source(source_id, tenant_id)
    except MonitoringSourceNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    success, message = await service.test_connection(source)
    return ConnectionTestResult(success=success, message=message, checked_at=datetime.now(timezone.utc))


@router.post("/sources/{source_id}/readiness", dependencies=[Depends(require_permission("monitoring.update")), _active_sub_guard], response_model=ReadinessResultOut)
async def source_readiness(
    source_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: MonitoringService = Depends(get_monitoring_service),
):
    """READ-ONLY readiness diagnostic for one source (Phase 3B §10).

    Answers "can this source be polled right now, and if not why?" without
    collecting anything: no Device, Interface, Capability, Relationship or
    topology row is created. `status="ready"` means the transport answered and
    credentials were accepted - NOT that discovery data was collected.
    """
    try:
        source = await service.get_source(source_id, tenant_id)
    except MonitoringSourceNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    result = await service.check_readiness(source)
    return ReadinessResultOut(**asdict(result))


@router.get("/readiness", dependencies=[_active_sub_guard], response_model=ReadinessReportOut)
async def readiness_report(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: MonitoringService = Depends(get_monitoring_service),
):
    """Readiness for every configured source, plus per-protocol coverage.

    Protocols with no source (currently WMI in this environment) appear as an
    explicit "not configured" entry rather than being silently absent.
    """
    report = await service.readiness_report(tenant_id)
    return ReadinessReportOut(
        sources_checked=report["sources_checked"],
        results=[ReadinessResultOut(**asdict(r)) for r in report["results"]],
        coverage=[ReadinessResultOut(**asdict(r)) for r in report["coverage"]],
    )


@router.post("/sources/{source_id}/sync", dependencies=[Depends(require_permission("monitoring.update")), _active_sub_guard], response_model=SyncResult)
async def sync_source(
    source_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: MonitoringService = Depends(get_monitoring_service),
    orchestrator: CollectionOrchestrator = Depends(get_collection_orchestrator),
):
    try:
        source = await service.get_source(source_id, tenant_id)
    except MonitoringSourceNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    run = await orchestrator.run(source, trigger="manual")
    await db.commit()
    # RCA activation: ONLY after the commit above, mirroring the scheduler path
    # (app/scheduler/jobs.py). `run.created_incidents` holds zero, one, or many
    # minimal incident references; a trigger failure must never fail this
    # request, because the Incident itself is already committed.
    from app.services.rca_trigger import trigger_rca_for_incident

    for reference in run.created_incidents:
        try:
            await trigger_rca_for_incident(reference.incident_id, reference.tenant_id)
        except Exception:  # noqa: BLE001 - incident row is already committed
            import logging

            logging.getLogger(__name__).exception(
                "RCA trigger failed for committed incident %s", reference.incident_id
            )
    # Source-health domain events (SOURCE_RECOVERED / SOURCE_HEALTH_DEGRADED) are
    # published AFTER the commit above so a rolled-back collection never emits a
    # spurious recovery/degradation signal. Best-effort, like the RCA triggers.
    for event in run.recovery_events:
        try:
            await get_event_bus().publish(event)
        except Exception:  # noqa: BLE001 - health row is already committed
            import logging

            logging.getLogger(__name__).exception(
                "Source-health event publish failed for source %s", source_id
            )
    return SyncResult(
        source_id=source_id,
        status=run.status,
        # CollectorRun only tracks a combined "sensors_upserted" count, not
        # a created/updated split (that split lives in BundleSaver.SaveResult
        # internally but isn't carried onto the CollectorRun audit row).
        # Best-effort mapping onto the pre-existing SyncResult shape: report
        # the whole upserted count as "created" and leave "updated" at 0
        # rather than guessing at a split.
        sensors_created=run.sensors_upserted,
        sensors_updated=0,
        sensors_total=run.sensors_upserted,
        synced_at=run.finished_at or run.started_at,
        error=run.error,
    )


@router.get("/sources/{source_id}/sensors", response_model=list[SensorOut])
async def list_source_sensors(
    source_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: MonitoringService = Depends(get_monitoring_service),
):
    # Two independent layers, deliberately: the ownership check below decides
    # whether this tenant may see the source at all (404 if not), and passing
    # the tenant into the repository keeps the row filter tenant-aware as well -
    # so a corrupt/duplicated source link could not leak rows across tenants.
    try:
        await service.get_source(source_id, tenant_id)
    except MonitoringSourceNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return await service.sensor_repo.list_for_source(source_id, tenant_id=tenant_id)


@router.get("/sensors", response_model=list[SensorOut])
async def list_sensors(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    site_id: uuid.UUID | None = None,
    device_id: uuid.UUID | None = None,
    status: str | None = None,
    metric_type: str | None = None,
    service: MonitoringService = Depends(get_monitoring_service),
):
    return await service.sensor_repo.list_for_tenant(
        tenant_id,
        site_id=site_id,
        device_id=device_id,
        status=status,
        metric_type=metric_type,
    )


# ---------------------------------------------------------------------------
# REMOVED (PHASE 5B): GET /prtg/root-sensors
#
# This endpoint was a legacy passthrough that logged into ONE
# environment-configured PRTG probe (PRTG_DEV_URL / PRTG_DEV_USERNAME /
# PRTG_DEV_PASSHASH) with a SYNCHRONOUS `requests` session, parsed PRTG's
# tabledata XML inside the API layer, and returned the result to ANY
# authenticated caller. It was therefore:
#
#   * not tenant-scoped - every authenticated tenant saw the same probe's
#     sensors, with no MonitoringSource ownership check;
#   * credential-global - one process-wide credential rather than a per-source
#     encrypted credential owned by a tenant;
#   * a SECOND PRTG transport and a SECOND PRTG status map
#     (`_normalize_prtg_status` disagreed with the collector's
#     `_PRTG_STATUS_MAP`, so the UI could contradict the recorded
#     `Sensor.status`);
#   * outside the Collector Framework entirely - no MonitoringSource, no
#     CollectionOrchestrator, no CollectorRun audit row, no SourceHealthManager
#     health state, no per-source rate limiting.
#
# PRTG sensor data is now read ONLY through the canonical path:
#
#   PRTGCollector.collect() -> CollectionOrchestrator -> BundleSaver
#     -> Sensor rows -> GET /sources/{source_id}/sensors   (tenant-checked)
#
# PRTG HTTP transport therefore lives in exactly one place
# (app/collectors/prtg.py), with the action plane
# (app/actions/providers/prtg.py) reusing that same transport. The regression
# tests that keep this closed live in
# tests/test_monitoring_crud.py::TestSourceSensorsTenantIsolation.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Collector enrollment: one-time, tenant-scoped registration tokens
#
# Trust path:
#   tenant admin -> POST /collectors/enrollment-tokens   (session-authenticated)
#                -> plaintext token returned ONCE; only its hash is stored
#   collector    -> POST /collectors/register            (token IS the credential)
#                -> token validated + atomically consumed, tenant taken FROM THE
#                   TOKEN, MonitoringSource created under that tenant.
#
# Reuses MonitoringService.create_source, MonitoringSource, the canonical
# entitlement layer and AuditService. No second collector entity, no second
# entitlement mechanism, no MonitoringSource schema change.
# ---------------------------------------------------------------------------

ENROLLMENT_TOKEN_DEFAULT_TTL_MINUTES = 60
ENROLLMENT_TOKEN_MAX_TTL_MINUTES = 60 * 24 * 30  # 30 days


class EnrollmentTokenCreate(BaseModel):
    #: Optional TTL. Bounded so a token can never be effectively immortal.
    expires_in_minutes: int | None = Field(
        default=None, ge=1, le=ENROLLMENT_TOKEN_MAX_TTL_MINUTES
    )


class EnrollmentTokenCreated(BaseModel):
    """Returned ONCE at creation. Never retrievable again.

    Carries no token_hash and no other secret.
    """

    token: str
    expires_at: datetime
    expires_in_seconds: int
    registration_url: str
    tenant_id: uuid.UUID


class CollectorRegisterPayload(BaseModel):
    """Collector self-registration.

    Deliberately has NO tenant_id - the tenant is taken EXCLUSIVELY from the
    validated enrollment token, so a caller cannot choose a tenant.
    """

    token: str = Field(min_length=16, max_length=512)
    name: str = Field(min_length=1, max_length=255)
    source_type: str = Field(min_length=1, max_length=50)
    site_id: uuid.UUID | None = None
    config: dict = Field(default_factory=dict)
    vendor: str | None = Field(default=None, max_length=100)
    hostname: str | None = Field(default=None, max_length=255)
    ip_address: str | None = Field(default=None, max_length=45)
    port: int | None = Field(default=None, ge=1, le=65535)
    protocol: str | None = Field(default=None, max_length=20)
    verify_ssl: bool | None = False
    poll_interval_seconds: int = Field(default=60, ge=10, le=86400)
    #: Reported by the collector; stored on the credential for diagnostics only.
    collector_version: str | None = Field(default=None, max_length=50)


class CollectorRegistrationResult(BaseModel):
    """Successful collector registration.

    ``collector_credential`` is the ONE-TIME plaintext credential the collector
    uses for all subsequent authenticated requests. It is returned exactly here
    and is never retrievable again.

    Deliberately contains NO enrollment token, NO credential hash, no encrypted
    material and no PRTG/customer credentials. ``source`` is the existing
    MonitoringSource response, unchanged.
    """

    source: MonitoringSourceOut
    collector_credential: str


#: ONE identical response for every invalid-token case, so a caller can never
#: learn whether a token exists, who owns it, or whether it is spent.
_INVALID_TOKEN_DETAIL = "Invalid or unusable enrollment token"


@router.post(
    "/collectors/enrollment-tokens",
    dependencies=[Depends(require_permission("monitoring.create")), _active_sub_guard],
    response_model=EnrollmentTokenCreated,
    status_code=201,
)
async def create_enrollment_token(
    payload: EnrollmentTokenCreate,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    principal: Principal = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mint a one-time collector enrollment token for the CALLER'S tenant.

    The tenant comes from the authenticated principal, never the request body.
    The plaintext token is returned exactly once; only its hash is persisted.
    """
    minutes = payload.expires_in_minutes or ENROLLMENT_TOKEN_DEFAULT_TTL_MINUTES
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=minutes)

    plaintext = generate_refresh_token()
    row = CollectorEnrollmentToken(
        tenant_id=tenant_id,
        token_hash=hash_refresh_token(plaintext),
        expires_at=expires_at,
        created_by=getattr(principal, "user_id", None),
    )
    db.add(row)
    await db.flush()

    await AuditService(db).record(
        tenant_id=tenant_id,
        action="collector.enrollment_token_created",
        actor_id=getattr(principal, "user_id", None),
        actor_email=getattr(principal, "email", None),
        entity_type="collector_enrollment_token",
        entity_id=row.id,
        # The plaintext token is NEVER audited - only its expiry.
        description="Collector enrollment token created",
        new_values={"expires_at": expires_at.isoformat()},
    )
    await db.commit()

    return EnrollmentTokenCreated(
        token=plaintext,
        expires_at=expires_at,
        expires_in_seconds=int(minutes * 60),
        registration_url="/api/monitoring/collectors/register",
        tenant_id=tenant_id,
    )


@router.post(
    "/collectors/register",
    response_model=CollectorRegistrationResult,
    status_code=201,
)
async def register_collector(
    payload: CollectorRegisterPayload,
    db: AsyncSession = Depends(get_db),
    service: MonitoringService = Depends(get_monitoring_service),
    billing: BillingService = Depends(get_billing_service),
):
    """Register a collector using a one-time enrollment token.

    Intentionally NOT session-authenticated: the enrollment token IS the
    credential, which is what lets a collector running on customer hardware
    enrol itself without a human login.

    Validation order is existence -> hash match -> not expired -> not revoked ->
    not already used. Every one of those failures returns the SAME 401, so a
    caller learns nothing about the owning tenant.
    """
    now = datetime.now(timezone.utc)

    row = (
        await db.execute(
            select(CollectorEnrollmentToken).where(
                CollectorEnrollmentToken.token_hash == hash_refresh_token(payload.token)
            )
        )
    ).scalar_one_or_none()

    # Hash matching is inherent: the row is looked up BY hash. Expiry,
    # revocation and prior use are all checked here.
    if (
        row is None
        or row.expires_at <= now
        or row.revoked_at is not None
        or row.used_at is not None
    ):
        raise HTTPException(status_code=401, detail=_INVALID_TOKEN_DETAIL)

    # The ONLY source of tenant for this operation.
    tenant_id = row.tenant_id

    # A supplied site must belong to the token's tenant, so enrollment cannot
    # attach a collector into another tenant's site.
    if payload.site_id is not None:
        site_tenant = (
            await db.execute(select(Site.tenant_id).where(Site.id == payload.site_id))
        ).scalar_one_or_none()
        if site_tenant is None or site_tenant != tenant_id:
            raise HTTPException(
                status_code=422, detail="site_id does not belong to this tenant"
            )

    # Canonical entitlement check, BEFORE the token is consumed - so a tenant
    # with no collector capacity is rejected and the token stays usable.
    denial = await evaluate_resource_limit(billing, tenant_id, "collectors")
    if denial is not None:
        _code, detail, _audit_message = denial
        raise HTTPException(status_code=402, detail=detail)

    # Atomic single-use: this conditional UPDATE can succeed only once. Two
    # concurrent registrations race here and exactly one sees rowcount == 1.
    consumed = await db.execute(
        update(CollectorEnrollmentToken)
        .where(
            CollectorEnrollmentToken.id == row.id,
            CollectorEnrollmentToken.used_at.is_(None),
        )
        .values(used_at=now)
    )
    if consumed.rowcount != 1:
        # The losing request: consume failed, nothing created.
        await db.rollback()
        raise HTTPException(status_code=409, detail=_INVALID_TOKEN_DETAIL)

    try:
        # Reuse the existing MonitoringSource creation path (source_type is
        # validated against CollectorRegistry there, credentials encrypted there).
        # `token` is the one-time enrollment token and `collector_version` belongs
        # to the CREDENTIAL, not to a MonitoringSource column - neither may be
        # passed to create_source.
        fields = payload.model_dump(exclude={"token", "collector_version"})
        fields["tenant_id"] = tenant_id
        source = await service.create_source(**fields)

        # Permanent collector credential: this is what the collector uses from
        # now on. The enrollment token is NOT reused as a credential - it is
        # single-use and is consumed above.
        #
        # Created in the SAME transaction as the MonitoringSource and the token
        # consumption, and only the hash is persisted. If anything here fails the
        # whole transaction rolls back, so no partial credential and no partially
        # registered source can remain (and the token stays usable).
        plaintext_credential = generate_refresh_token()
        db.add(
            CollectorCredential(
                tenant_id=tenant_id,
                source_id=source.id,
                credential_hash=hash_refresh_token(plaintext_credential),
                collector_version=payload.collector_version,
            )
        )
        await db.flush()
    except ValueError as exc:
        # Unknown source_type etc. Roll back so the token is NOT consumed and
        # the collector can retry with a corrected payload.
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    await AuditService(db).record(
        tenant_id=tenant_id,
        action="collector.registered",
        actor_type="collector",
        entity_type="monitoring_source",
        entity_id=source.id,
        description=f"Collector {payload.name!r} registered via enrollment token",
        # Never includes the enrollment token, the credential or any credential
        # value - only non-secret facts.
        new_values={
            "name": payload.name,
            "source_type": payload.source_type,
            "credential_issued": True,
        },
    )
    await db.commit()

    # The plaintext credential is returned HERE, exactly once, and nowhere else.
    return CollectorRegistrationResult(
        source=MonitoringSourceOut.model_validate(source),
        collector_credential=plaintext_credential,
    )

