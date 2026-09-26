"""
Dependency-aware health aggregation (spec sections 22, 23, 26, 27, 40).

    resource health -> site health -> tenant health

Rules that make this honest
---------------------------
* A resource with NO persisted health and NO usable status is ``unknown``
  (score 50) - never "healthy". A resource nobody has evaluated must not inflate
  a rollup.
* Health is DEPENDENCY-AWARE: a resource whose dependency is critical drops,
  because spec section 26 requires a WAN failure to be visible on the firewall
  that routes through it, not only on the WAN link itself.
* Health is MAINTENANCE-AWARE: during a planned window the bucket becomes
  ``maintenance`` and the score is not penalised (expected changes are not
  failures), but the undecorated ``raw_score`` is still returned.
* A rollup with no members returns ``unknown``, not 100: "no data" and
  "perfect health" are different answers.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.services.infrastructure_intelligence import telemetry as T
from app.services.infrastructure_intelligence import vocabulary as V


@dataclass
class ResourceHealth:
    resource_id: uuid.UUID
    health: str
    score: int
    raw_score: int
    status: str
    reasons: list[dict] = field(default_factory=list)
    dependencies: list[dict] = field(default_factory=list)
    in_maintenance: bool = False
    telemetry_quality: str = T.QUALITY_UNKNOWN
    telemetry_freshness_seconds: float | None = None
    last_telemetry_at: datetime | None = None

    def to_dict(self) -> dict:
        return {
            "resource_id": str(self.resource_id),
            "health": self.health,
            "score": self.score,
            "raw_score": self.raw_score,
            "status": self.status,
            "reasons": list(self.reasons),
            "dependencies": list(self.dependencies),
            "in_maintenance": self.in_maintenance,
            "telemetry_quality": self.telemetry_quality,
            "telemetry_freshness_seconds": self.telemetry_freshness_seconds,
            "last_telemetry_at": (
                self.last_telemetry_at.isoformat() if self.last_telemetry_at else None
            ),
        }


@dataclass
class HealthRollup:
    """Aggregate health for a site or a tenant."""

    scope: str
    scope_id: uuid.UUID | None
    health: str
    score: int
    members: int
    counts: dict = field(default_factory=dict)
    maintenance_members: int = 0
    child_rollups: list[dict] = field(default_factory=list)
    reasons: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "scope": self.scope,
            "scope_id": str(self.scope_id) if self.scope_id else None,
            "health": self.health,
            "score": self.score,
            "members": self.members,
            "counts": dict(self.counts),
            "maintenance_members": self.maintenance_members,
            "child_rollups": list(self.child_rollups),
            "reasons": list(self.reasons),
        }


def bucket_for_score(score: int, *, members: int) -> str:
    """Score -> health bucket. An empty rollup is UNKNOWN, never healthy."""
    if members == 0:
        return V.HEALTH_UNKNOWN
    if score >= 90:
        return V.HEALTH_HEALTHY
    if score >= 60:
        return V.HEALTH_WARNING
    return V.HEALTH_CRITICAL


def base_health_for(
    device, now: datetime | None = None
) -> tuple[str, str, float | None]:
    """(health bucket, canonical status, telemetry freshness) for a resource.

    The persisted ``health`` column wins when a health pass has written one;
    otherwise the bucket is derived from the stored status spelling through the
    SAME vocabulary the rest of the platform uses, so the two cannot disagree.
    """
    reference = now or datetime.now(timezone.utc)
    last_telemetry = getattr(device, "last_telemetry_at", None)
    freshness = T.freshness_seconds(last_telemetry, now=reference)
    stored = getattr(device, "health", None)
    status = V.operational_state(device.status)
    if stored:
        return stored, status, freshness
    return V.health_from_oper_state(status), status, freshness


def dependency_penalty(dependency_health: str) -> float:
    """How far a dependency's health drags its dependents down."""
    if dependency_health == V.HEALTH_CRITICAL:
        return V.DEPENDENCY_DOWN_PENALTY
    if dependency_health == V.HEALTH_WARNING:
        return V.DEPENDENCY_WARNING_PENALTY
    if dependency_health == V.HEALTH_UNKNOWN:
        return V.DEPENDENCY_UNKNOWN_PENALTY
    return 0.0


class HealthService:
    """Computes resource/site/tenant health from persisted rows only."""

    def __init__(
        self,
        *,
        device_repo,
        site_repo,
        relationship_repo,
        maintenance_repo,
    ):
        self.device_repo = device_repo
        self.site_repo = site_repo
        self.relationship_repo = relationship_repo
        self.maintenance_repo = maintenance_repo

    async def resource_health(
        self,
        tenant_id: uuid.UUID,
        device,
        *,
        now: datetime | None = None,
    ) -> ResourceHealth:
        """Health for ONE resource, including dependency and maintenance state."""
        reference = now or datetime.now(timezone.utc)
        health, status, freshness = base_health_for(device, reference)
        reasons: list[dict] = []

        if getattr(device, "health", None) is None:
            reasons.append({
                "reason": V.REASON_NOT_YET_COLLECTED,
                "detail": "no health pass has been written for this resource",
            })
        if freshness is None:
            reasons.append({
                "reason": V.REASON_NO_TELEMETRY_EVER,
                "detail": "no canonical telemetry has ever been written",
            })
        elif freshness > V.TELEMETRY_STALE_AFTER_SECONDS:
            reasons.append({
                "reason": V.REASON_TELEMETRY_STALE,
                "detail": f"newest telemetry is {int(freshness)}s old",
            })

        penalties, dependency_rows = await self._dependency_penalties(
            tenant_id, device, reference
        )

        raw_score = V.HEALTH_SCORE.get(health, 50)
        score = raw_score
        if penalties:
            worst = max(penalties)
            if worst > 0:
                score = int(raw_score * (1.0 - worst))
                reasons.append({
                    "reason": "dependency_degraded",
                    "detail": (
                        f"{len(penalties)} dependency edge(s); worst penalty "
                        f"{worst:.2f}"
                    ),
                })

        windows = await self.maintenance_repo.list_active_for_scope(
            tenant_id,
            site_id=getattr(device, "site_id", None),
            device_id=device.id,
            at=reference,
        )
        in_maintenance = bool(windows)
        return ResourceHealth(
            resource_id=device.id,
            # Planned changes are expected: the bucket becomes `maintenance` and
            # the score is not penalised. `raw_score` still carries the real,
            # un-suppressed value so nothing is hidden from an operator.
            health=V.HEALTH_MAINTENANCE if in_maintenance else health,
            score=(
                V.HEALTH_SCORE[V.HEALTH_MAINTENANCE]
                if in_maintenance else max(0, min(100, score))
            ),
            raw_score=max(0, min(100, raw_score)),
            status=status,
            reasons=reasons,
            dependencies=dependency_rows,
            in_maintenance=in_maintenance,
            telemetry_quality=T.quality_for(
                getattr(device, "last_telemetry_at", None), now=reference
            ),
            telemetry_freshness_seconds=freshness,
            last_telemetry_at=getattr(device, "last_telemetry_at", None),
        )

    async def _dependency_penalties(
        self, tenant_id: uuid.UUID, device, reference: datetime
    ) -> tuple[list[float], list[dict]]:
        """One batch lookup of this resource's dependencies (no N+1 per edge)."""
        edges = await self.relationship_repo.list_edges_from(device.id, tenant_id)
        if not edges:
            return [], []
        target_ids = [edge.target_resource_id for edge, _ in edges]
        targets = {
            d.id: d for d in await self.device_repo.list_by_ids(tenant_id, target_ids)
        }
        penalties: list[float] = []
        rows: list[dict] = []
        seen: set[tuple[uuid.UUID, str]] = set()
        for edge, target_name in edges:
            key = (edge.target_resource_id, edge.relationship_type)
            if key in seen:
                continue
            seen.add(key)
            target = targets.get(edge.target_resource_id)
            if target is None:
                rows.append({
                    "resource_id": str(edge.target_resource_id),
                    "name": target_name,
                    "relationship_type": edge.relationship_type,
                    "health": V.HEALTH_UNKNOWN,
                    "reason": V.REASON_NOT_YET_COLLECTED,
                })
                penalties.append(dependency_penalty(V.HEALTH_UNKNOWN))
                continue
            dependency_health, dependency_status, _ = base_health_for(
                target, reference
            )
            rows.append({
                "resource_id": str(target.id),
                "name": target.name,
                "relationship_type": edge.relationship_type,
                "health": dependency_health,
                "status": dependency_status,
            })
            penalties.append(dependency_penalty(dependency_health))
        return penalties, rows

    async def site_health(
        self,
        tenant_id: uuid.UUID,
        site_id: uuid.UUID,
        *,
        now: datetime | None = None,
    ) -> HealthRollup:
        """Roll a site's resources (and child sites) into one health answer."""
        reference = now or datetime.now(timezone.utc)
        devices = await self.device_repo.list(
            tenant_id, site_id=site_id, limit=100_000
        )
        counts: dict[str, int] = {state: 0 for state in V.HEALTH_STATES}
        scores: list[int] = []
        maintenance_members = 0
        for device in devices:
            result = await self.resource_health(tenant_id, device, now=reference)
            counts[result.health] = counts.get(result.health, 0) + 1
            scores.append(result.score)
            if result.in_maintenance:
                maintenance_members += 1

        child_rollups: list[dict] = []
        child_scores: list[int] = []
        for site in await self.site_repo.list(tenant_id):
            if getattr(site, "parent_site_id", None) != site_id:
                continue
            child = await self.site_health(tenant_id, site.id, now=reference)
            child_rollups.append({
                "site_id": str(site.id),
                "name": site.name,
                "health": child.health,
                "score": child.score,
                "members": child.members,
            })
            if child.members:
                child_scores.append(child.score)

        own = sum(scores) / len(scores) if scores else 0.0
        if child_scores:
            child_avg = sum(child_scores) / len(child_scores)
            score = int(own * V.PARENT_SELF_WEIGHT + child_avg * V.PARENT_CHILD_WEIGHT)
        else:
            score = int(own)
        members = len(scores) + len(child_scores)
        reasons: list[dict] = []
        if not scores:
            reasons.append({
                "reason": V.REASON_PRIMARY_DATA_MISSING,
                "detail": "no resources are assigned to this site",
            })
        return HealthRollup(
            scope="site",
            scope_id=site_id,
            health=bucket_for_score(score, members=members),
            score=max(0, min(100, score)),
            members=members,
            counts=counts,
            maintenance_members=maintenance_members,
            child_rollups=child_rollups,
            reasons=reasons,
        )

    async def tenant_health(
        self, tenant_id: uuid.UUID, *, now: datetime | None = None
    ) -> HealthRollup:
        """Roll every site plus every site-less resource into tenant health."""
        reference = now or datetime.now(timezone.utc)
        scores: list[int] = []
        counts: dict[str, int] = {state: 0 for state in V.HEALTH_STATES}
        maintenance_members = 0
        child_rollups: list[dict] = []

        for site in await self.site_repo.list(tenant_id):
            rollup = await self.site_health(tenant_id, site.id, now=reference)
            for bucket, count in rollup.counts.items():
                counts[bucket] = counts.get(bucket, 0) + count
            maintenance_members += rollup.maintenance_members
            if rollup.members:
                scores.append(rollup.score)
                child_rollups.append({
                    "site_id": str(site.id),
                    "name": site.name,
                    "health": rollup.health,
                    "score": rollup.score,
                    "members": rollup.members,
                })

        # Resources attached to no site are still this tenant's resources;
        # leaving them out would understate the tenant's health.
        unassigned_scores: list[int] = []
        for device in await self.device_repo.list(tenant_id, limit=100_000):
            if device.site_id is not None:
                continue
            result = await self.resource_health(tenant_id, device, now=reference)
            counts[result.health] = counts.get(result.health, 0) + 1
            unassigned_scores.append(result.score)

        all_scores = list(scores)
        if unassigned_scores:
            all_scores.append(int(sum(unassigned_scores) / len(unassigned_scores)))
        score = int(sum(all_scores) / len(all_scores)) if all_scores else 0
        reasons: list[dict] = []
        if not all_scores:
            reasons.append({
                "reason": V.REASON_PRIMARY_DATA_MISSING,
                "detail": "this tenant has no sites and no resources yet",
            })
        return HealthRollup(
            scope="tenant",
            scope_id=tenant_id,
            health=bucket_for_score(score, members=len(all_scores)),
            score=max(0, min(100, score)),
            members=len(all_scores),
            counts=counts,
            maintenance_members=maintenance_members,
            child_rollups=child_rollups,
            reasons=reasons,
        )
