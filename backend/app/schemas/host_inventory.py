"""
Pydantic v2 schemas for host saved views and bulk host actions
(observability plan P2 slice 2).

Vocabularies:
  * criticality: the priority engine's weight table keys
    (`services/priority/criticality_weights.CRITICALITY_WEIGHTS`), so a value
    set here always changes a host's business-impact weight.
  * environment: `HOST_ENVIRONMENTS` below. `devices.environment` had no
    vocabulary and every existing row is NULL, so this one is new.
  * tags: 1-50 characters of letters, digits, space and ``_ . : / -``,
    stored trimmed; at most `MAX_TAGS_PER_HOST` per host.
"""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.schemas.explorer_hosts import HostHealth
from app.services.priority.criticality_weights import CRITICALITY_WEIGHTS

HOST_CRITICALITIES: tuple[str, ...] = tuple(CRITICALITY_WEIGHTS)
HOST_ENVIRONMENTS: tuple[str, ...] = ("production", "staging", "development", "test", "dr", "lab")
MAX_TAGS_PER_HOST = 50
MAX_BULK_HOSTS = 200

Tag = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50, pattern=r"^[A-Za-z0-9 _.:/-]+$")]
#: Same values as models.host_saved_view.HOST_VIEW_LAYOUTS.
ViewLayout = Literal["health", "inventory"]


# ---------------------------------------------------------------------------
# Saved views
# ---------------------------------------------------------------------------


class HostViewFilters(BaseModel):
    """The Hosts explorer's own filters (GET /api/explorer/hosts query), nothing else."""

    model_config = ConfigDict(extra="forbid")

    search: str | None = Field(None, max_length=100)
    health: HostHealth | None = None
    status: str | None = Field(None, max_length=50)
    device_type: str | None = Field(None, max_length=100)
    vendor: str | None = Field(None, max_length=100)
    criticality: str | None = Field(None, max_length=20)
    environment: str | None = Field(None, max_length=20)
    tag: str | None = Field(None, max_length=50)
    site_id: uuid.UUID | None = None


ViewName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class HostViewCreate(BaseModel):
    name: ViewName
    filters: HostViewFilters = HostViewFilters()
    layout: ViewLayout = "health"
    shared: bool = False


class HostViewUpdate(BaseModel):
    """Omitted fields stay as they are; `filters` replaces the whole filter set."""

    name: ViewName | None = None
    filters: HostViewFilters | None = None
    layout: ViewLayout | None = None
    shared: bool | None = None


class HostViewOut(BaseModel):
    id: uuid.UUID
    name: str
    filters: HostViewFilters
    layout: ViewLayout
    shared: bool
    #: True when the caller owns the view (only then can it be changed).
    owned: bool
    created_at: dt.datetime
    updated_at: dt.datetime


# ---------------------------------------------------------------------------
# Bulk actions
# ---------------------------------------------------------------------------

BulkAction = Literal[
    "set_criticality",
    "set_environment",
    "add_tags",
    "remove_tags",
    "pause_monitoring",
    "resume_monitoring",
]
CLASSIFY_ACTIONS: frozenset[str] = frozenset({"set_criticality", "set_environment", "add_tags", "remove_tags"})
MONITORING_ACTIONS: frozenset[str] = frozenset({"pause_monitoring", "resume_monitoring"})


class HostBulkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=MAX_BULK_HOSTS)
    action: BulkAction
    #: set_criticality / set_environment: the new value, or null to clear it.
    value: str | None = None
    #: add_tags / remove_tags.
    tags: list[Tag] = Field(default_factory=list, max_length=MAX_TAGS_PER_HOST)

    @field_validator("device_ids")
    @classmethod
    def _dedupe(cls, ids: list[uuid.UUID]) -> list[uuid.UUID]:
        return list(dict.fromkeys(ids))

    def check(self) -> None:
        """Cross-field validation; raises ValueError with a user-facing message."""
        if self.action == "set_criticality" and self.value is not None and self.value not in HOST_CRITICALITIES:
            raise ValueError(f"criticality must be one of {', '.join(HOST_CRITICALITIES)} or null")
        if self.action == "set_environment" and self.value is not None and self.value not in HOST_ENVIRONMENTS:
            raise ValueError(f"environment must be one of {', '.join(HOST_ENVIRONMENTS)} or null")
        if self.action in ("add_tags", "remove_tags") and not self.tags:
            raise ValueError("tags is required for add_tags / remove_tags")
        if self.action not in ("set_criticality", "set_environment") and self.value is not None:
            raise ValueError(f"value is not used by {self.action}")
        if self.action not in ("add_tags", "remove_tags") and self.tags:
            raise ValueError(f"tags is not used by {self.action}")


class HostBulkResult(BaseModel):
    action: BulkAction
    #: Distinct host ids in the request.
    requested: int
    #: Hosts of the caller's tenant that were found.
    matched: int
    #: Hosts that actually changed (the rest already had the value).
    changed: int
    #: Ids not found in the caller's tenant (another tenant's ids land here).
    not_found: list[uuid.UUID]
    #: pause / resume only: sensors whose lifecycle changed.
    sensors_changed: int = 0
    #: add_tags only: hosts left unchanged because they already hold MAX_TAGS_PER_HOST tags.
    tag_limit_hit: int = 0
