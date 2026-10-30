"""Controlled materialization of persisted probe results into canonical devices.

This is the SECOND half of the two-stage Probe -> Review -> Materialize flow. The
probe endpoint persists observation-only results (``DiscoveryProbeJob`` /
``DiscoveryProbeHost``); this module converts ELIGIBLE reachable-host evidence
into ``DiscoveryEvidence`` and feeds it through the EXISTING
``DiscoveryService.discover()`` / ``DeviceRepository.upsert()`` path - it never
re-implements device identity, deduplication or quota.

Safety invariants enforced here:
  * a host must be a row on the persisted, tenant-scoped job (arbitrary client
    IPs are rejected, not treated as evidence);
  * only a ``completed`` job can be materialized;
  * only ``reachable`` hosts are eligible (an unreachable host is rejected, not
    fabricated into a device);
  * a repeated request never duplicates a device - already-materialized hosts
    are terminal and ``DeviceRepository.upsert()`` is idempotent;
  * quota is enforced by ``DiscoveryService`` (same plan-limit semantics as the
    collector path), so quota exhaustion rejects the host instead of creating it.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.discovery_probe import (
    JOB_STATUS_COMPLETED,
    MATERIALIZATION_MATERIALIZED,
    MATERIALIZATION_PARTIAL,
    MATERIALIZATION_PENDING,
    OUTCOME_CREATED,
    OUTCOME_EXISTING,
    OUTCOME_FAILED,
    OUTCOME_REJECTED,
    TERMINAL_OUTCOMES,
    DiscoveryProbeHost,
)
from app.repositories.discovery_probe_repository import DiscoveryProbeRepository
from app.services.discovery.evidence import DiscoveryEvidence, TRANSPORT_ICMP
from app.services.discovery.service import DiscoveryService

QUOTA_ERROR_TOKEN = "device_quota_exceeded"


class ProbeJobNotFound(Exception):
    """The job does not exist (or does not belong to this tenant)."""


class ProbeJobNotMaterializable(Exception):
    """The job exists but is not in a state that may be materialized."""


class UnknownHostsError(Exception):
    """Some requested addresses are not part of the persisted job."""

    def __init__(self, addresses: list[str]):
        self.addresses = addresses
        super().__init__(
            "addresses not present in the persisted job: " + ", ".join(addresses)
        )


def host_to_evidence(host: DiscoveryProbeHost) -> DiscoveryEvidence:
    """Reachable-host evidence -> DiscoveryEvidence, without inventing identity.

    Transport is ``icmp`` (the host was at least reachable at the network
    layer). No vendor/model/serial/device_type is inferred from an open port or
    an address - the recognition core will honestly return ``unknown``.
    """
    return DiscoveryEvidence(
        address=host.address,
        transport=TRANSPORT_ICMP,
        reachable=host.reachable,
    )


@dataclass
class HostMaterializeOutcome:
    address: str
    outcome: str
    device_id: str | None = None
    error: str | None = None


@dataclass
class MaterializeReport:
    created: int = 0
    existing: int = 0
    rejected: int = 0
    failed: int = 0
    hosts: list[HostMaterializeOutcome] = field(default_factory=list)

    @property
    def state(self) -> str:
        if self.created or self.existing:
            if self.rejected or self.failed:
                return MATERIALIZATION_PARTIAL
            return MATERIALIZATION_MATERIALIZED
        return MATERIALIZATION_PENDING


def _match_error(errors: list[str], address: str) -> str | None:
    prefix = f"{address}:"
    for err in errors:
        if err.startswith(prefix):
            return err
    return None


async def materialize_probe_job(
    *,
    db: AsyncSession,
    tenant_id: uuid.UUID,
    job_id: uuid.UUID,
    addresses: list[str],
    monitoring_source_id: uuid.UUID,
    billing=None,
) -> MaterializeReport:
    """Materialize selected persisted hosts into canonical devices.

    Serializes concurrent requests for the SAME job via ``SELECT ... FOR
    UPDATE`` on the job row, so two simultaneous materializations cannot create
    duplicate devices or double-consume quota. The caller owns the commit.
    """
    repo = DiscoveryProbeRepository(db)

    job = await repo.get_job_for_update(job_id, tenant_id)
    if job is None:
        raise ProbeJobNotFound(f"probe job {job_id} not found")
    if job.status != JOB_STATUS_COMPLETED:
        raise ProbeJobNotMaterializable(
            f"probe job {job_id} is {job.status!r}; only completed jobs can be materialized"
        )

    host_rows = {h.address: h for h in await repo.get_hosts(job.id, tenant_id)}

    unknown = [a for a in addresses if a not in host_rows]
    if unknown:
        raise UnknownHostsError(unknown)

    report = MaterializeReport()
    now = datetime.now(timezone.utc)

    candidates: list[DiscoveryProbeHost] = []
    for address in addresses:
        host = host_rows[address]
        if host.materialization_outcome in TERMINAL_OUTCOMES:
            report.existing += 1
            report.hosts.append(
                HostMaterializeOutcome(
                    address=address,
                    outcome=OUTCOME_EXISTING,
                    device_id=str(host.device_id) if host.device_id else None,
                )
            )
        elif not host.reachable:
            await repo.update_host(
                host,
                materialization_outcome=OUTCOME_REJECTED,
                materialized_at=now,
            )
            report.rejected += 1
            report.hosts.append(
                HostMaterializeOutcome(
                    address=address, outcome=OUTCOME_REJECTED, error="not_reachable"
                )
            )
        else:
            candidates.append(host)

    if candidates:
        service = DiscoveryService(db, billing=billing)
        result = await service.discover(
            tenant_id=tenant_id,
            monitoring_source_id=monitoring_source_id,
            evidence=[host_to_evidence(h) for h in candidates],
            discovered_by="scope-materialize",
        )
        by_address = {r.address: r for r in result.resources}
        for host in candidates:
            r = by_address.get(host.address)
            if r is None:
                await repo.update_host(
                    host, materialization_outcome=OUTCOME_FAILED, materialized_at=now
                )
                report.failed += 1
                report.hosts.append(
                    HostMaterializeOutcome(
                        address=host.address,
                        outcome=OUTCOME_FAILED,
                        error=_match_error(result.errors, host.address),
                    )
                )
            elif r.persisted and r.created:
                await repo.update_host(
                    host,
                    materialization_outcome=OUTCOME_CREATED,
                    device_id=uuid.UUID(r.device_id) if r.device_id else None,
                    materialized_at=now,
                )
                report.created += 1
                report.hosts.append(
                    HostMaterializeOutcome(
                        address=host.address,
                        outcome=OUTCOME_CREATED,
                        device_id=r.device_id,
                    )
                )
            elif r.persisted:
                await repo.update_host(
                    host,
                    materialization_outcome=OUTCOME_EXISTING,
                    device_id=uuid.UUID(r.device_id) if r.device_id else None,
                    materialized_at=now,
                )
                report.existing += 1
                report.hosts.append(
                    HostMaterializeOutcome(
                        address=host.address,
                        outcome=OUTCOME_EXISTING,
                        device_id=r.device_id,
                    )
                )
            elif any(QUOTA_ERROR_TOKEN in e for e in r.errors):
                await repo.update_host(
                    host,
                    materialization_outcome=OUTCOME_REJECTED,
                    materialized_at=now,
                )
                report.rejected += 1
                report.hosts.append(
                    HostMaterializeOutcome(
                        address=host.address,
                        outcome=OUTCOME_REJECTED,
                        error=QUOTA_ERROR_TOKEN,
                    )
                )
            else:
                await repo.update_host(
                    host, materialization_outcome=OUTCOME_FAILED, materialized_at=now
                )
                report.failed += 1
                report.hosts.append(
                    HostMaterializeOutcome(
                        address=host.address,
                        outcome=OUTCOME_FAILED,
                        error="; ".join(r.errors) or None,
                    )
                )

    await repo.update_job(job, materialization_state=report.state)
    return report

