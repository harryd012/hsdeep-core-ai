"""
Repository Pattern: all direct SQLAlchemy access for ServiceNowSyncJob lives here.
Queue operations: list_due, claim, increment_retry, mark_completed, retry_dead_letter.
Mirrors the collector run queue drain pattern in CollectorRunRepository.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.servicenow import ServiceNowSyncJob


class ServiceNowSyncJobNotFound(Exception):
    pass


class ServiceNowSyncJobRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, **fields) -> ServiceNowSyncJob:
        job = ServiceNowSyncJob(**fields)
        self.db.add(job)
        await self.db.flush()
        await self.db.refresh(job)
        return job

    async def get(self, job_id: uuid.UUID, tenant_id: uuid.UUID) -> ServiceNowSyncJob | None:
        stmt = select(ServiceNowSyncJob).where(
            ServiceNowSyncJob.id == job_id,
            ServiceNowSyncJob.tenant_id == tenant_id,
            ServiceNowSyncJob.is_deleted.is_(False),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_due(
        self,
        tenant_id: uuid.UUID,
        *,
        now: datetime | None = None,
        limit: int = 10,
    ) -> list[ServiceNowSyncJob]:
        """Pending/failed rows whose next_attempt_at has elapsed (like MonitoringSource.list_due)."""
        target = now or datetime.now(timezone.utc)
        stmt = select(ServiceNowSyncJob).where(
            ServiceNowSyncJob.tenant_id == tenant_id,
            ServiceNowSyncJob.is_deleted.is_(False),
            ServiceNowSyncJob.status.in_(("pending", "failed")),
            ServiceNowSyncJob.next_attempt_at <= target,
        ).order_by(ServiceNowSyncJob.created_at.asc()).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def claim_for_processing(self, job: ServiceNowSyncJob) -> ServiceNowSyncJob:
        """Transition pending/failed -> processing, set started_at."""
        if job.status not in ("pending", "failed"):
            raise ValueError(f"Cannot claim job with status={job.status!r}")
        job.status = "processing"
        job.started_at = datetime.now(timezone.utc)
        await self.db.flush()
        await self.db.refresh(job)
        return job

    async def complete(
        self,
        job: ServiceNowSyncJob,
        *,
        status: str = "succeeded",
        error: str | None = None,
    ) -> ServiceNowSyncJob:
        """Mark a job complete. If retrying (failed), bumps retry_count and
        sets next_attempt_at with exponential backoff; if exceeded max_retries,
        transitions to dead_letter."""
        job.status = status
        job.completed_at = datetime.now(timezone.utc)
        job.last_error = error

        if status == "failed" and job.retry_count < job.max_retries:
            # Retry with exponential backoff: 2^retry_count seconds
            delay = min(2 ** job.retry_count, 300)  # cap at 5 minutes
            job.retry_count += 1
            job.next_attempt_at = datetime.now(timezone.utc).replace(tzinfo=timezone.utc)
        elif status == "failed":
            job.status = "dead_letter"

        await self.db.flush()
        await self.db.refresh(job)
        return job

    async def list_pending(
        self,
        tenant_id: uuid.UUID,
        *,
        limit: int = 10,
    ) -> list[ServiceNowSyncJob]:
        """List pending jobs (alias for list_due with no time constraint)."""
        return await self.list_due(tenant_id, now=datetime.now(timezone.utc), limit=limit)

    async def list_dead_letter(
        self,
        tenant_id: uuid.UUID,
        *,
        limit: int = 50,
    ) -> list[ServiceNowSyncJob]:
        """List dead-lettered jobs for manual inspection/retry."""
        stmt = select(ServiceNowSyncJob).where(
            ServiceNowSyncJob.tenant_id == tenant_id,
            ServiceNowSyncJob.is_deleted.is_(False),
            ServiceNowSyncJob.status == "dead_letter",
        ).order_by(ServiceNowSyncJob.created_at.desc()).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def retry_dead_letter(self, job: ServiceNowSyncJob) -> ServiceNowSyncJob:
        """Move a dead-lettered job back into the pending queue with reset counters."""
        job.status = "pending"
        job.retry_count = 0
        job.next_attempt_at = datetime.now(timezone.utc).replace(tzinfo=timezone.utc)
        job.last_error = None
        await self.db.flush()
        await self.db.refresh(job)
        return job
