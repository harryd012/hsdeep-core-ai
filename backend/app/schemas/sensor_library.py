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
