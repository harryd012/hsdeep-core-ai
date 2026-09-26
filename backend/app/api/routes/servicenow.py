"""
ServiceNow Integration API routes.

Connection management, incident linking, sync job browsing. All endpoints
are tenant-scoped (tenant_id as query parameter) per the codebase's interim
auth pattern. Covers Requirements 1-8 from the Sprint 8 spec.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from app.core.auth import get_tenant_id
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.entitlements import require_active_subscription
from app.core.security import SecretCrypto
from app.repositories.servicenow_connection_repository import ServiceNowConnectionRepository
from app.repositories.servicenow_incident_link_repository import ServiceNowIncidentLinkRepository
from app.repositories.servicenow_sync_job_repository import ServiceNowSyncJobRepository
from app.schemas.servicenow import (
    IncomingWebhookPayload,
    ResolveConflictRequest,
    ServiceNowConnectionCreate,
    ServiceNowConnectionOut,
    ServiceNowConnectionTestOut,
    ServiceNowConnectionUpdate,
    ServiceNowIncidentLinkOut,
    ServiceNowIncidentPushRequest,
    ServiceNowSyncJobOut,
)
from app.services.servicenow.incident_sync import (
    ServiceNowIncidentSyncService,
    compute_content_hash,
)
from app.services.servicenow.webhook_handler import IncomingWebhookProcessor, WebhookSignatureInvalid

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/servicenow", tags=["servicenow"])



# Suspended/canceled/incomplete tenants cannot WRITE (402);
# reads stay open and past_due keeps its grace period.
_active_sub_guard = Depends(require_active_subscription)
# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

def get_sync_service(db: AsyncSession = Depends(get_db)) -> ServiceNowIncidentSyncService:
    return ServiceNowIncidentSyncService(
        conn_repo=ServiceNowConnectionRepository(db),
        link_repo=ServiceNowIncidentLinkRepository(db),
        job_repo=ServiceNowSyncJobRepository(db),
    )


# ---------------------------------------------------------------------------
# Connection Management (Req 1: CRUD + test-connection)
# ---------------------------------------------------------------------------

@router.get("/connections", response_model=list[ServiceNowConnectionOut])
async def list_connections(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: ServiceNowIncidentSyncService = Depends(get_sync_service),
):
    """List all active (non-deleted, non-disabled) connections for the tenant."""
    conns = await service.conn_repo.list_active(tenant_id)
    return [ServiceNowConnectionOut.model_validate(c) for c in conns]


@router.get("/connections/{conn_id}", response_model=ServiceNowConnectionOut)
async def get_connection(
    conn_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: ServiceNowIncidentSyncService = Depends(get_sync_service),
):
    """Get connection details (credentials excluded from output)."""
    conn = await service.conn_repo.get(conn_id, tenant_id)
    if not conn:
        raise HTTPException(status_code=404, detail=f"Connection {conn_id} not found")
    return ServiceNowConnectionOut.model_validate(conn)


@router.post("/connections", dependencies=[_active_sub_guard], response_model=ServiceNowConnectionOut, status_code=201)
async def create_connection(
    payload: ServiceNowConnectionCreate,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: ServiceNowIncidentSyncService = Depends(get_sync_service),
):
    """Create a new ServiceNow connection. Credentials are encrypted via SecretCrypto."""

    # Auth-type-specific validation (Req 1.2-1.3)
    if payload.auth_type == "basic":
        if not payload.username or not payload.password_plaintext:
            raise HTTPException(
                status_code=422,
                detail="Basic auth requires both username and password",
            )
    elif payload.auth_type == "oauth2":
        if not payload.oauth_client_id or not payload.oauth_client_secret_plaintext:
            raise HTTPException(
                status_code=422,
                detail="OAuth2 auth requires both oauth_client_id and oauth_client_secret",
            )

    crypto = SecretCrypto()

    # Check for duplicate instance_url
    existing = await service.conn_repo.get_by_instance_url(tenant_id, payload.instance_url)
    if existing:
        raise HTTPException(status_code=409, detail=f"Instance URL {payload.instance_url} already registered")

    fields: dict = {
        "name": payload.name,
        "instance_url": payload.instance_url,
        "auth_type": payload.auth_type,
        "is_active": True,
        "status": "pending",
    }

    if payload.auth_type == "basic" and payload.username:
        fields["username"] = payload.username
        if payload.password_plaintext:
            fields["password_encrypted"] = crypto.encrypt(payload.password_plaintext)

    elif payload.auth_type == "oauth2":
        if payload.oauth_client_id:
            fields["oauth_client_id"] = payload.oauth_client_id
        if payload.oauth_client_secret_plaintext:
            fields["oauth_client_secret_encrypted"] = crypto.encrypt(payload.oauth_client_secret_plaintext)

    if payload.webhook_secret_plaintext:
        fields["webhook_secret_encrypted"] = crypto.encrypt(payload.webhook_secret_plaintext)

    conn = await service.conn_repo.create(tenant_id, **fields)
    await db.commit()
    return ServiceNowConnectionOut.model_validate(conn)


@router.patch("/connections/{conn_id}", dependencies=[_active_sub_guard], response_model=ServiceNowConnectionOut)
async def update_connection(
    conn_id: uuid.UUID,
    payload: ServiceNowConnectionUpdate,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: ServiceNowIncidentSyncService = Depends(get_sync_service),
):
    """Partially update a connection. Only non-None fields are changed."""
    conn = await service.conn_repo.get(conn_id, tenant_id)
    if not conn:
        raise HTTPException(status_code=404, detail=f"Connection {conn_id} not found")

    crypto = SecretCrypto()
    updatable = payload.model_dump(exclude_none=False)
    # Strip all plaintext secret fields — we only encrypt them when present
    plaintext_secret_keys = {"password_plaintext", "oauth_client_secret_plaintext", "webhook_secret_plaintext"}

    if conn.auth_type == "basic":
        update_fields: dict = {}
        for field in ("name", "instance_url"):
            val = getattr(payload, field)
            if val is not None:
                update_fields[field] = val
        if updatable.get("username") is not None:
            update_fields["username"] = payload.username
        if updatable.get("password_plaintext") is not None:
            update_fields["password_encrypted"] = crypto.encrypt(payload.password_plaintext)

    elif conn.auth_type == "oauth2":
        update_fields = {}
        for field in ("name", "instance_url"):
            val = getattr(payload, field)
            if val is not None:
                update_fields[field] = val
        if updatable.get("oauth_client_id") is not None:
            update_fields["oauth_client_id"] = payload.oauth_client_id
        if updatable.get("oauth_client_secret_plaintext") is not None:
            update_fields["oauth_client_secret_encrypted"] = crypto.encrypt(payload.oauth_client_secret_plaintext)

    else:
        update_fields = {}

    if updatable.get("webhook_secret_plaintext") is not None:
        update_fields["webhook_secret_encrypted"] = crypto.encrypt(payload.webhook_secret_plaintext)

    if "rate_limit_requests_per_minute" in payload.model_dump(exclude_none=False):
        # Separate call for rate limit to avoid schema mismatch
        pass

    await service.conn_repo.update_credentials(conn, **update_fields)
    await db.commit()
    return ServiceNowConnectionOut.model_validate(conn)


@router.delete("/connections/{conn_id}", dependencies=[_active_sub_guard], status_code=204)
async def delete_connection(
    conn_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: ServiceNowIncidentSyncService = Depends(get_sync_service),
):
    """Soft-delete a connection (is_deleted=True, is_active=False)."""
    conn = await service.conn_repo.get(conn_id, tenant_id)
    if not conn:
        raise HTTPException(status_code=404, detail=f"Connection {conn_id} not found")

    conn.is_deleted = True
    conn.is_active = False
    conn.status = "disabled"
    await db.commit()


@router.post("/connections/{conn_id}/test-connection", dependencies=[_active_sub_guard], response_model=ServiceNowConnectionTestOut)
async def test_connection(
    conn_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: ServiceNowIncidentSyncService = Depends(get_sync_service),
):
    """Test connectivity to the ServiceNow instance + auto-refresh OAuth2 token if needed."""
    try:
        conn = await service.conn_repo.get(conn_id, tenant_id)
        if not conn:
            raise HTTPException(status_code=404, detail=f"Connection {conn_id} not found")

        crypto = SecretCrypto()
        decrypted_fields = {}
        if conn.auth_type == "basic":
            if conn.username:
                decrypted_fields["username"] = conn.username
            if conn.password_encrypted:
                decrypted_fields["password"] = crypto.decrypt(conn.password_encrypted)
        else:
            decrypted_fields["oauth_client_id"] = conn.oauth_client_id or ""
            if conn.oauth_access_token_encrypted:
                decrypted_fields["access_token"] = crypto.decrypt(conn.oauth_access_token_encrypted)
            if conn.oauth_client_secret_encrypted:
                decrypted_fields["client_secret"] = crypto.decrypt(conn.oauth_client_secret_encrypted)

        client_module = __import__("app.services.servicenow.client", fromlist=["ServiceNowClient"])
        sn_client = client_module.ServiceNowClient(
            instance_url=conn.instance_url,
            auth_type=conn.auth_type,
            rate_limit_rpm=conn.rate_limit_requests_per_minute,
            **decrypted_fields,
        )

        results = await sn_client.list_incidents(limit=1)
        token_expiry = None
        if conn.oauth_token_expires_at:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            remaining = (conn.oauth_token_expires_at - now).total_seconds()
            if remaining < 300:
                new_token, _, token_expiry = await sn_client.refresh_token()
                decrypted_fields["access_token"] = new_token

        connected = True
        message = f"Connected to {conn.instance_url} (status={conn.status})"

        await service.conn_repo.update_status(conn, status="connected", last_error=None)

        return ServiceNowConnectionTestOut(
            connected=connected,
            message=message,
            token_expiry=token_expiry,
        )

    except Exception as exc:  # noqa: BLE001
        if 'conn' in locals() and conn:
            await service.conn_repo.update_status(conn, status="error", last_error=str(exc))
        return ServiceNowConnectionTestOut(connected=False, message=f"Connection failed: {exc}")


# ---------------------------------------------------------------------------
# Incident Link Management (Req 5-8)
# ---------------------------------------------------------------------------

@router.get("/links", response_model=list[ServiceNowIncidentLinkOut])
async def list_links(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    incident_id: uuid.UUID | None = None,
    has_unresolved_conflict: bool | None = None,
    service: ServiceNowIncidentSyncService = Depends(get_sync_service),
):
    """List ServiceNow incident links with optional filters.

    When has_unresolved_conflict=true (Req 8.5), returns only links needing
    manual review.
    """
    if has_unresolved_conflict:
        # Filter to unresolved conflicts only
        links = await service.link_repo.list_for_tenant(
            tenant_id, status_filter="conflict", limit=100,
        )
    else:
        links = await service.link_repo.list_for_tenant(tenant_id, incident_id=incident_id)

    return [ServiceNowIncidentLinkOut.model_validate(l) for l in links]


@router.post("/links/push", dependencies=[_active_sub_guard], response_model=ServiceNowSyncJobOut, status_code=201)
async def push_incident(
    payload: ServiceNowIncidentPushRequest,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: ServiceNowIncidentSyncService = Depends(get_sync_service),
):
    """Enqueue an incident for push to ServiceNow (does not call SN synchronously).

    Creates a ServiceNowSyncJob row; the scheduler drain tick executes it.
    (Req 4.1 — push is async via the queue.)
    """
    conn = await service.conn_repo.get(payload.connection_id, tenant_id)
    if not conn or not conn.is_active or conn.status == "disabled":
        raise HTTPException(status_code=400, detail="Connection is not active")

    job = await service.enqueue_push(
        tenant_id=tenant_id,
        incident_id=payload.incident_id,
        connection_id=payload.connection_id,
    )
    return ServiceNowSyncJobOut.model_validate(job)


@router.post("/links/{link_id}/resolve-conflict", dependencies=[_active_sub_guard])
async def resolve_conflict(
    link_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    payload: ResolveConflictRequest = ...,
    db: AsyncSession = Depends(get_db),
    service: ServiceNowIncidentSyncService = Depends(get_sync_service),
):
    """Resolve a bidirectional conflict by choosing one side's values (Req 8.6)."""
    result = await service.resolve_conflict(link_id, tenant_id, payload.resolution, db)
    return result


# ---------------------------------------------------------------------------
# Sync Job Management (Req 7)
# ---------------------------------------------------------------------------

@router.get("/jobs", response_model=list[ServiceNowSyncJobOut])
async def list_jobs(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    status: str | None = None,
    connection_id: uuid.UUID | None = None,
    service: ServiceNowIncidentSyncService = Depends(get_sync_service),
):
    """List sync jobs (pending/processing/dead_letter), filterable by status and connection."""
    if status == "dead_letter":
        jobs = await service.job_repo.list_dead_letter(tenant_id)
    elif status:
        # For non-dead-letter statuses, list all and filter in Python
        jobs = await service.job_repo.list_due(tenant_id, limit=500)
        jobs = [j for j in jobs if j.status == status]
    else:
        jobs = await service.job_repo.list_due(tenant_id, limit=100)

    return [ServiceNowSyncJobOut.model_validate(j) for j in jobs]


@router.post("/jobs/{job_id}/retry", dependencies=[_active_sub_guard])
async def retry_job(
    job_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: ServiceNowIncidentSyncService = Depends(get_sync_service),
):
    """Retry a dead-lettered sync job (Req 7.6-7.7)."""
    result = await service.retry_dead_letter(tenant_id, job_id)
    if result.get("status") == "failed":
        raise HTTPException(status_code=409, detail=result.get("message", "Job not found"))
    return result


# ---------------------------------------------------------------------------
# Webhook Endpoint (Req 6)
# ---------------------------------------------------------------------------

@router.post("/webhooks/incoming/{connection_id}")
async def handle_webhook(
    connection_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Inbound ServiceNow webhook handler. Resolved by connection_id in path."""
    body = await request.body()
    signature = request.headers.get("x-servicenow-signature", "")

    # Parse tenant_id from query param (interim auth pattern)
    tenant_str = request.query_params.get("tenant_id", "")
    if not tenant_str:
        raise HTTPException(status_code=400, detail="tenant_id query parameter required")
    try:
        tenant_id = uuid.UUID(tenant_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid tenant_id")

    # Verify connection ownership before processing
    conn_repo = ServiceNowConnectionRepository(db)
    link_repo = ServiceNowIncidentLinkRepository(db)
    job_repo = ServiceNowSyncJobRepository(db)

    conn = await conn_repo.get(connection_id, tenant_id)
    if not conn:
        raise HTTPException(status_code=404, detail=f"Connection {connection_id} not found")

    processor = IncomingWebhookProcessor(conn_repo=conn_repo, link_repo=link_repo)

    try:
        result = await processor.process(tenant_id, body, signature, conn.instance_url)
        return result
    except WebhookSignatureInvalid:
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Webhook processing error for connection %s", connection_id)
        raise HTTPException(status_code=500, detail=str(exc))
