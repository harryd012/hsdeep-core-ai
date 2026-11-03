"""
Run a scope probe and keep its evidence (Phase 6.3 §8; scheduling: observability plan P2).

One path for both callers - the "probe now" route and the discovery scheduler
job - so a scheduled probe is recorded exactly as a manual one: a durable
``DiscoveryProbeJob`` with its hosts, the scope's run state, and (for a scope
with a schedule) its next run. OBSERVATION ONLY: nothing here creates a
device; hosts become inventory only through the separate, operator-approved
materialize step.

The caller owns the transaction (this flushes, never commits).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from app.models.discovery_probe import JOB_STATUS_COMPLETED, JOB_STATUS_FAILED, DiscoveryProbeJob
from app.models.discovery_scope import DiscoveryScope
from app.repositories.discovery_probe_repository import DiscoveryProbeRepository
from app.repositories.discovery_scope_repository import DiscoveryScopeRepository
from app.services.discovery.network_probe import NetworkProbeReport
from app.services.discovery.schedule import next_run_after
from app.services.discovery.scope_runner import ScopeNotProbeable, probe_scope

logger = logging.getLogger(__name__)


class ScopeProbeFailed(Exception):
    """The probe raised; the failed run is already recorded on the job and scope."""

    def __init__(self, job: DiscoveryProbeJob, message: str):
        super().__init__(message)
        self.job = job


@dataclass
class ScopeProbeOutcome:
    job: DiscoveryProbeJob
    report: NetworkProbeReport


async def run_scope_probe(
    scope_repo: DiscoveryScopeRepository,
    probe_repo: DiscoveryProbeRepository,
    scope: DiscoveryScope,
    *,
    trigger: str,
    now: datetime | None = None,
) -> ScopeProbeOutcome:
    """Probe ``scope`` and persist the result.

    Raises ``ScopeNotProbeable`` (nothing recorded) for a scope type with no
    probe, and ``ScopeProbeFailed`` after recording a failed run.
    """
    now = now or datetime.now(timezone.utc)
    # The next run counts from this one, whether it succeeds or not, so a
    # failing scope is retried on its schedule rather than every tick.
    next_run = next_run_after(scope.schedule, now)
    try:
        report = await probe_scope(scope)
    except ScopeNotProbeable:
        raise
    except Exception as exc:  # noqa: BLE001 - record the failed run durably
        logger.exception("scope probe failed for scope %s", scope.id)
        job = await probe_repo.create_job(
            tenant_id=scope.tenant_id,
            scope_id=scope.id,
            target=scope.target,
            scope_type=scope.scope_type,
            status=JOB_STATUS_FAILED,
            trigger=trigger,
            error=str(exc)[:2000],
            started_at=now,
            finished_at=now,
        )
        await scope_repo.update(
            scope, status="error", last_run_at=now, last_error=str(exc)[:2000], next_run_at=next_run
        )
        raise ScopeProbeFailed(job, str(exc)) from exc

    job = await probe_repo.create_job(
        tenant_id=scope.tenant_id,
        scope_id=scope.id,
        target=scope.target,
        scope_type=scope.scope_type,
        status=JOB_STATUS_COMPLETED,
        trigger=trigger,
        hosts_probed=report.hosts_probed,
        hosts_total=report.hosts_total,
        reachable=len(report.reachable),
        truncated=report.truncated,
        started_at=now,
        finished_at=datetime.now(timezone.utc),
    )
    await probe_repo.add_hosts(
        job.id,
        scope.tenant_id,
        [
            {
                "address": h.address,
                "reachable": h.reachable,
                "latency_ms": h.latency_ms,
                "packet_loss_pct": h.packet_loss_pct,
                "protocols": dict(h.protocols),
                "evidence": list(h.evidence),
            }
            for h in report.hosts
        ],
    )
    await scope_repo.update(scope, status="ok", last_run_at=now, last_error=None, next_run_at=next_run)
    return ScopeProbeOutcome(job=job, report=report)


__all__ = ["ScopeProbeFailed", "ScopeProbeOutcome", "run_scope_probe"]
