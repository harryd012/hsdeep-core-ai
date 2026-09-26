"""
Business Impact Engine.

Computes an incident's business impact purely from real, already-stored
rows: the Devices/Sites/BusinessServices touched by its member alerts. No
network topology graph is assumed here (that's a future RCA-sprint
concern) - "affected" means "directly referenced by a member alert's
device_id/site_id", which is exactly the same denormalized-column
approach CorrelationEngine already uses for grouping.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from app.models.alert import Alert
from app.models.business_service import BusinessService
from app.models.device import Device
from app.repositories.business_service_repository import BusinessServiceRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.site_repository import SiteRepository
from app.services.priority.criticality_weights import criticality_weight

# Weights for combining the four sub-factors into one 0-100 business
# impact score. Sum to 1.0. Kept as named constants (not magic numbers
# inline) so PriorityEngine's overall docstring/reasoning can reference
# them by name for explainability.
_WEIGHT_DEVICE_COUNT = 0.20
_WEIGHT_USER_COUNT = 0.25
_WEIGHT_SERVICE_CRITICALITY = 0.30
_WEIGHT_SITE_CRITICALITY = 0.25

# Device/user counts are diminishing-returns capped rather than linear -
# an incident affecting 50 devices isn't "5x worse" than one affecting 10,
# it's already saturated at "many". Caps chosen so a single-digit count
# still moves the score meaningfully (per Requirement: business impact
# must reflect small tenants realistically, not just enterprise scale).
_DEVICE_COUNT_SATURATION = 10
_USER_COUNT_SATURATION = 1000


@dataclass
class BusinessImpactResult:
    score: float  # 0-100
    affected_device_count: int
    affected_site_ids: list[uuid.UUID]
    affected_region_count: int
    affected_business_services: list[BusinessService]
    estimated_affected_users: int
    reasoning: dict = field(default_factory=dict)


class BusinessImpactEngine:
    def __init__(
        self,
        device_repo: DeviceRepository,
        site_repo: SiteRepository,
        business_service_repo: BusinessServiceRepository,
    ):
        self.device_repo = device_repo
        self.site_repo = site_repo
        self.business_service_repo = business_service_repo

    async def calculate(self, tenant_id: uuid.UUID, member_alerts: list[Alert]) -> BusinessImpactResult:
        device_ids = {a.device_id for a in member_alerts if a.device_id is not None}
        site_ids = {a.site_id for a in member_alerts if a.site_id is not None}
        regions = {a.region for a in member_alerts if a.region is not None}

        devices: list[Device] = []
        for device_id in device_ids:
            device = await self.device_repo.get(device_id, tenant_id)
            if device is not None:
                devices.append(device)
                if device.site_id is not None:
                    site_ids.add(device.site_id)

        business_service_ids = {d.business_service_id for d in devices if d.business_service_id is not None}
        business_services: list[BusinessService] = []
        for bs_id in business_service_ids:
            bs = await self.business_service_repo.get(bs_id, tenant_id)
            if bs is not None:
                business_services.append(bs)

        estimated_users = sum(bs.estimated_users or 0 for bs in business_services)

        device_count_component = _saturating_ratio(len(device_ids), _DEVICE_COUNT_SATURATION) * 100.0
        user_count_component = _saturating_ratio(estimated_users, _USER_COUNT_SATURATION) * 100.0

        service_criticality_component = (
            max((criticality_weight(bs.criticality) for bs in business_services), default=criticality_weight(None))
        )

        site_criticalities = []
        for site_id in site_ids:
            site = await self.site_repo.get(site_id, tenant_id)
            if site is not None:
                site_criticalities.append(criticality_weight(site.criticality))
        # Device criticality also feeds the "site" bucket when a device has
        # no resolvable site - otherwise a critical device with no site
        # link would be invisible to this component entirely.
        device_only_criticalities = [
            criticality_weight(d.criticality) for d in devices if d.site_id is None
        ]
        all_location_criticalities = site_criticalities + device_only_criticalities
        site_criticality_component = max(all_location_criticalities, default=criticality_weight(None))

        score = (
            device_count_component * _WEIGHT_DEVICE_COUNT
            + user_count_component * _WEIGHT_USER_COUNT
            + service_criticality_component * _WEIGHT_SERVICE_CRITICALITY
            + site_criticality_component * _WEIGHT_SITE_CRITICALITY
        )

        reasoning = {
            "affected_device_count": len(device_ids),
            "device_count_component": round(device_count_component, 1),
            "estimated_affected_users": estimated_users,
            "user_count_component": round(user_count_component, 1),
            "max_business_service_criticality": _criticality_label(service_criticality_component),
            "service_criticality_component": round(service_criticality_component, 1),
            "max_site_or_device_criticality": _criticality_label(site_criticality_component),
            "site_criticality_component": round(site_criticality_component, 1),
            "weights": {
                "device_count": _WEIGHT_DEVICE_COUNT,
                "user_count": _WEIGHT_USER_COUNT,
                "service_criticality": _WEIGHT_SERVICE_CRITICALITY,
                "site_criticality": _WEIGHT_SITE_CRITICALITY,
            },
        }

        return BusinessImpactResult(
            score=round(score, 1),
            affected_device_count=len(device_ids),
            affected_site_ids=list(site_ids),
            affected_region_count=len(regions),
            affected_business_services=business_services,
            estimated_affected_users=estimated_users,
            reasoning=reasoning,
        )


def _saturating_ratio(value: int, saturation_point: int) -> float:
    if saturation_point <= 0:
        return 0.0
    return min(value / saturation_point, 1.0)


def _criticality_label(weight: float) -> str:
    for label, w in (("critical", 100.0), ("high", 75.0), ("medium", 50.0), ("low", 20.0)):
        if weight >= w:
            return label
    return "low"
