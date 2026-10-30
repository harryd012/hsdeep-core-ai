"""DiscoveryProbeJob / DiscoveryProbeHost repository. Every query is tenant-scoped.

The probe result is an OBSERVATION ledger, never a canonical inventory record.
Materialization writes go through ``DiscoveryService.discover()`` /
``DeviceRepository.upsert()``; this repository only records what the probe saw
and what materialization did to each observed address.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.discovery_probe import DiscoveryProbeHost, DiscoveryProbeJob


class DiscoveryProbeRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    # -- jobs ----------------------------------------------------------------

    async def create_job(self, **fields) -> DiscoveryProbeJob:
        job = DiscoveryProbeJob(**fields)
        self.db.add(job)
        await self.db.flush()
        await self.db.refresh(job)
        return job

    async def get_job(
        self, job_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> DiscoveryProbeJob | None:
        stmt = select(DiscoveryProbeJob).where(
            DiscoveryProbeJob.id == job_id,
            DiscoveryProbeJob.tenant_id == tenant_id,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_job_for_update(
        self, job_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> DiscoveryProbeJob | None:
        """Load the job with a row lock so concurrent materializations serialize."""
        stmt = (
            select(DiscoveryProbeJob)
            .where(
                DiscoveryProbeJob.id == job_id,
                DiscoveryProbeJob.tenant_id == tenant_id,
            )
            .with_for_update()
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def list_jobs(
        self,
        tenant_id: uuid.UUID,
        scope_id: uuid.UUID | None = None,
        limit: int = 50,
    ) -> list[DiscoveryProbeJob]:
        stmt = select(DiscoveryProbeJob).where(
            DiscoveryProbeJob.tenant_id == tenant_id
        )
        if scope_id is not None:
            stmt = stmt.where(DiscoveryProbeJob.scope_id == scope_id)
        stmt = stmt.order_by(DiscoveryProbeJob.started_at.desc()).limit(limit)
        return list((await self.db.execute(stmt)).scalars().all())

    async def update_job(self, job: DiscoveryProbeJob, **fields) -> DiscoveryProbeJob:
        for key, value in fields.items():
            setattr(job, key, value)
        await self.db.flush()
        await self.db.refresh(job)
        return job

    # -- hosts ---------------------------------------------------------------

    async def add_hosts(
        self, job_id: uuid.UUID, tenant_id: uuid.UUID, host_rows: list[dict]
    ) -> list[DiscoveryProbeHost]:
        hosts = [
            DiscoveryProbeHost(job_id=job_id, tenant_id=tenant_id, **row)
            for row in host_rows
        ]
        self.db.add_all(hosts)
        await self.db.flush()
        return hosts

    async def get_hosts(
        self, job_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> list[DiscoveryProbeHost]:
        stmt = (
            select(DiscoveryProbeHost)
            .where(
                DiscoveryProbeHost.job_id == job_id,
                DiscoveryProbeHost.tenant_id == tenant_id,
            )
            .order_by(DiscoveryProbeHost.address)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def get_hosts_by_addresses(
        self, job_id: uuid.UUID, tenant_id: uuid.UUID, addresses: list[str]
    ) -> list[DiscoveryProbeHost]:
        if not addresses:
            return []
        stmt = select(DiscoveryProbeHost).where(
            DiscoveryProbeHost.job_id == job_id,
            DiscoveryProbeHost.tenant_id == tenant_id,
            DiscoveryProbeHost.address.in_(list(addresses)),
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def update_host(
        self, host: DiscoveryProbeHost, **fields
    ) -> DiscoveryProbeHost:
        for key, value in fields.items():
            setattr(host, key, value)
        await self.db.flush()
        return host
