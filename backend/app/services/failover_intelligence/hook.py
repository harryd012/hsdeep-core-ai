"""
Post-collection hook — the ONLY thing the monitoring pipeline calls.

Engineering rules this module exists to satisfy, in order of importance:

1. **Never break collection.** Every failure mode returns a report dict; nothing
   raised here can propagate into ``CollectionOrchestrator.run()``.
2. **Bounded.** A cheap pre-check skips tenants with no topology at all, the
   detector caps how many sites it visits per run, and each site's reads are
   bounded by ``loader``'s limits.
3. **Timeout safe.** The whole pass runs under ``asyncio.wait_for``; a slow
   database cannot hold a collection run open indefinitely.
4. **Observable.** The returned report (logged by the caller) says exactly what
   was evaluated, skipped, opened, or failed — including the error text.

Detection runs *after* the collection rows are written, so it always reads the
state the run just produced rather than a half-applied bundle.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

#: Hard ceiling for one tenant's post-collection pass.
DEFAULT_TIMEOUT_SECONDS = 20.0
#: Sites visited per run (matches FailoverDetectorService.MAX_SCOPES).
MAX_SCOPES = 25


async def has_wan_topology(db, tenant_id) -> tuple[bool, int]:
    """Cheap pre-check: does this tenant have any topology links at all?"""
    from app.repositories.topology_link_repository import TopologyLinkRepository

    _rows, total = await TopologyLinkRepository(db).list(tenant_id, limit=1)
    return total > 0, int(total)


async def run_after_collection(
    db,
    tenant_id,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_scopes: int = MAX_SCOPES,
) -> dict:
    """Run one bounded failover-detection pass. Never raises."""
    report: dict = {"status": "skipped", "tenant_id": str(tenant_id)}
    try:
        try:
            has_topology, total_links = await asyncio.wait_for(
                has_wan_topology(db, tenant_id), timeout=max(1.0, timeout / 4)
            )
        except asyncio.TimeoutError:
            report["reason"] = "topology pre-check timed out"
            return report
        if not has_topology:
            report["reason"] = "tenant has no topology links"
            return report
        report["topology_links"] = total_links

        from app.services.failover_intelligence.detector import (
            FailoverDetectorService,
        )

        detector = FailoverDetectorService(db, max_scopes=max_scopes)
        result = await asyncio.wait_for(
            detector.run_for_tenant(tenant_id), timeout=timeout
        )
        report.update(result)
        report["status"] = "ok"
    except asyncio.TimeoutError:
        report["status"] = "timeout"
        report["reason"] = f"failover detection exceeded {timeout}s"
        logger.warning(
            "Failover detection timed out for tenant %s after %ss", tenant_id, timeout
        )
    except Exception as exc:  # noqa: BLE001 - collection must never be affected
        report["status"] = "error"
        report["reason"] = f"{type(exc).__name__}: {exc}"
        logger.warning(
            "Failover detection failed for tenant %s: %s",
            tenant_id, exc, exc_info=True,
        )
    return report
