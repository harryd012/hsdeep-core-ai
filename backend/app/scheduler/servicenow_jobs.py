"""
ServiceNow sync job scheduler tick.

Drains pending ServiceNowSyncJob rows with the same advisory-lock + bounded
concurrency pattern as monitoring_collection_tick. Independent lock key so it
doesn't compete with the collection tick in multi-worker deployments.

Also runs a periodic pull-tick for connections configured to pull, and
startup reconciliation of stale "processing" jobs left by crashed processes.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import AsyncSessionLocal, engine
from app.repositories.servicenow_connection_repository import ServiceNowConnectionRepository
from app.repositories.servicenow_incident_link_repository import ServiceNowIncidentLinkRepository
from app.repositories.servicenow_sync_job_repository import ServiceNowSyncJobRepository
from app.services.servicenow.incident_sync import (
    ServiceNowIncidentSyncService,
)

logger = logging.getLogger(__name__)

# Arbitrary constant advisory-lock key (distinct from TICK_LOCK_KEY in jobs.py)
_SYNC_TICK_LOCK_KEY = 91827365


async def servicenow_sync_tick() -> None:
    """Process due ServiceNowSyncJob rows AND run pull ticks for configured
    connections. Guarded by an advisory lock so only one worker runs per tick."""

    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": _SYNC_TICK_LOCK_KEY})
        if not result.scalar_one():
            logger.debug("ServiceNow sync tick: advisory lock held by another worker, skipping.")
            return

    settings = get_settings()

    # Identify which tenant(s) to operate on (Phase 1: single-tenant default)
    async with AsyncSessionLocal() as db:
        conn_repo = ServiceNowConnectionRepository(db)
        link_repo = ServiceNowIncidentLinkRepository(db)
        job_repo = ServiceNowSyncJobRepository(db)

        # Find the active tenant (single-tenant default for Phase 1)
        tenant_stmt = text("SELECT id::text FROM tenants WHERE is_deleted = false LIMIT 1")
        tenant_result = await db.execute(tenant_stmt)
        tenant_row = tenant_result.fetchone()
        if not tenant_row:
            return  # no tenants yet

        tenant_id = uuid.UUID(tenant_row[0])

        conns = await conn_repo.list_active(tenant_id)
        sync_service = ServiceNowIncidentSyncService(
            conn_repo=conn_repo,
            link_repo=link_repo,
            job_repo=job_repo,
        )

        # --- Phase 1: drain pending jobs (push, webhook_inbound) ---
        due = await job_repo.list_due(tenant_id, limit=settings.servicenow_sync_batch_size)

        for job in due:
            # Validate connection is still active before processing
            conn = await conn_repo.get(job.connection_id, tenant_id)
            if not conn or not conn.is_active or conn.status == "disabled":
                continue  # skip inactive connections; jobs stay pending for later

            claimed = await job_repo.claim_for_processing(job)
            try:
                if job.job_type == "incident_push":
                    status, message = await sync_service.process_incident_push(claimed, db)
                    if status == "succeeded":
                        await job_repo.complete(claimed, status="succeeded")
                    else:
                        await job_repo.complete(claimed, status="failed", error=message)
                elif job.job_type == "webhook_inbound":
                    status, message = await sync_service.process_webhook_inbound(claimed, db)
                    if status == "succeeded":
                        await job_repo.complete(claimed, status="succeeded")
                    else:
                        await job_repo.complete(claimed, status="failed", error=message)
                else:
                    msg = f"Unknown job type {job.job_type!r}"
                    await job_repo.complete(claimed, status="failed", error=msg)
                    logger.warning("ServiceNow sync tick: %s", msg)

            except Exception:  # noqa: BLE001
                logger.exception("ServiceNow sync job %s failed", claimed.id)
                try:
                    await job_repo.complete(claimed, status="failed")
                except Exception:
                    pass  # job may have been consumed; ignore cleanup failure
            finally:
                try:
                    await db.commit()
                except Exception:
                    await db.rollback()

        # --- Phase 2: pull-tick for active connections ---
        for connection in conns:
            try:
                updated = await sync_service.process_pull_tick(connection, db)
                if updated > 0:
                    logger.info("Pull tick completed for %s: %d record(s)", connection.instance_url, updated)
            except Exception:  # noqa: BLE001
                logger.exception("Pull tick failed for %s", connection.instance_url)
        await db.commit()


async def reconcile_stale_servicenow_jobs(stale_after_minutes: int = 30) -> int:
    """Reset stale 'processing' jobs back to 'pending' (Req 7.8).

    Called at startup to recover from crashed processes that left jobs in
    the processing state without completing them.
    """
    async with AsyncSessionLocal() as db:
        stale_threshold = datetime.now(timezone.utc) - timedelta(minutes=stale_after_minutes)

        # Query stale processing jobs directly via raw SQL for efficiency
        stmt = text(
            "SELECT id FROM servicenow_sync_jobs "
            "WHERE status = 'processing' "
            f"  AND started_at < '{stale_threshold.isoformat()}' "
            "  AND is_deleted = false"
        )
        result = await db.execute(stmt)
        rows = result.fetchall()

        reset_count = 0
        for row in rows:
            try:
                from app.models.servicenow import ServiceNowSyncJob as JobModel
                job_obj = await db.get(JobModel, row[0])
                if job_obj and job_obj.status == "processing":
                    job_obj.status = "pending"
                    job_obj.started_at = None
                    job_obj.last_error = f"Reconciled stale job (was processing for >{stale_after_minutes} min)"
                    reset_count += 1
                    logger.info("Reconciled stale job %s", row[0])
            except Exception:  # noqa: BLE001
                pass

        if reset_count:
            await db.commit()

        return reset_count
