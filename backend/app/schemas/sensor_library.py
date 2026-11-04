"""Sensor library API shapes (observability plan P3 slice 1)."""
from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.schemas.monitoring import SensorThresholdsIn


class SensorTemplateOut(BaseModel):
    key: str
    name: str
    category: str
    description: str
    source_types: list[str]
    metric_types: list[str]
    unit: str | None
    direction: str | None
    thresholds_applicable: bool
    default_warning: float | None
    default_critical: float | None
    #: Where the default comes from; None when the platform defines none.
    default_origin: str | None
    #: The tenant's sensors this template covers.
    sensors: int
    by_status: dict[str, int]
    user_thresholds: int
    #: Collectors that reported these sensors for the tenant.
    reporting_source_types: list[str]


class UncataloguedMetricOut(BaseModel):
    metric_type: str
    total: int
    source_types: list[str]


class SensorLibraryOut(BaseModel):
    templates: list[SensorTemplateOut]
    uncatalogued: list[UncataloguedMetricOut]


class ApplyTemplateIn(BaseModel):
    """Set the template's sensors to these thresholds, or `reset` them to the provider's."""

    thresholds: SensorThresholdsIn | None = None
    reset: bool = False
    #: Only sensors of these hosts; omitted = every matching sensor in the tenant.
    device_ids: list[uuid.UUID] | None = Field(None, min_length=1, max_length=200)
    #: Count what would change without changing it.
    dry_run: bool = False


class ApplyTemplateResult(BaseModel):
    template: str
    matched: int
    changed: int
    status_changed: int
    not_found: list[uuid.UUID]
    dry_run: bool


class RecommendationRuleOut(BaseModel):
    key: str
    title: str
    description: str
    action: str
    hosts: int


class RecommendedHostOut(BaseModel):
    device_id: uuid.UUID
    name: str
    device_type: str | None
    address: str | None
    vendor: str | None
    sensors: int
    interfaces: int
    rules: list[str]


class RecommendationsOut(BaseModel):
    rules: list[RecommendationRuleOut]
    hosts: list[RecommendedHostOut]
    total: int
    #: True when `hosts` was cut to the page size.
    truncated: bool


class ReachabilityIn(BaseModel):
    """Add one tied reachability check (a connectivity source) per host."""

    device_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=200)
    check: str = Field("icmp", pattern="^(icmp|tcp)$")
    #: Required for a TCP check, refused for ICMP.
    port: int | None = None
    poll_interval_seconds: int = 300
    #: Sort the hosts and report the plan headroom without creating anything.
    dry_run: bool = False


class ReachabilityCreated(BaseModel):
    device_id: uuid.UUID
    source_id: uuid.UUID
    name: str


class ReachabilityResult(BaseModel):
    requested: int
    #: Hosts a check is (or would be, on a dry run) created for.
    to_create: list[uuid.UUID]
    created: list[ReachabilityCreated]
    already_checked: list[uuid.UUID]
    no_address: list[uuid.UUID]
    not_found: list[uuid.UUID]
    #: Sources the plan still allows; None when the plan sets no limit.
    sources_available: int | None
    dry_run: bool
