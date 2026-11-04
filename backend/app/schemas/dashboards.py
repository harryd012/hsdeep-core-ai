"""Saved dashboards and the widget registry (observability plan P6 slice 2).

Every widget type reads one existing API, which enforces its own permission;
the registry here only says which widgets exist and which settings each takes,
so a dashboard can never hold an unknown widget or free-form configuration.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.saved_dashboard import MAX_WIDGETS

WINDOW_HOURS = (1, 24, 168, 720)
INTERFACE_CHANNELS = ("rx_bps", "tx_bps", "interface_utilization_pct", "errors_in", "errors_out")
WIDGET_SIZES = ("small", "medium", "large")


class _NoConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _BusiestLinks(_NoConfig):
    limit: int = Field(5, ge=1, le=10)


class _SensorChart(_NoConfig):
    sensor_id: uuid.UUID
    channel: str = Field("primary", min_length=1, max_length=255)
    hours: int = 24

    @field_validator("hours")
    @classmethod
    def _window(cls, v: int) -> int:
        if v not in WINDOW_HOURS:
            raise ValueError(f"hours must be one of {WINDOW_HOURS}")
        return v


class _InterfaceChart(_NoConfig):
    interface_id: uuid.UUID
    channel: str = "rx_bps"
    hours: int = 24

    @field_validator("channel")
    @classmethod
    def _channel(cls, v: str) -> str:
        if v not in INTERFACE_CHANNELS:
            raise ValueError(f"channel must be one of {INTERFACE_CHANNELS}")
        return v

    @field_validator("hours")
    @classmethod
    def _window(cls, v: int) -> int:
        if v not in WINDOW_HOURS:
            raise ValueError(f"hours must be one of {WINDOW_HOURS}")
        return v


#: type -> (label, data source, settings model). The data source is documentation
#: for the UI; the widget calls it with the viewer's own permissions.
WIDGET_TYPES: dict[str, tuple[str, str, type[BaseModel]]] = {
    "network_summary": ("Network summary", "GET /api/network/overview", _NoConfig),
    "busiest_links": ("Busiest links", "GET /api/network/overview", _BusiestLinks),
    "interfaces_down": ("Interfaces down while enabled", "GET /api/network/overview", _NoConfig),
    "vpn_tunnels": ("VPN tunnels", "GET /api/network/overview", _NoConfig),
    "sensor_chart": ("Sensor history", "GET /api/monitoring/sensors/{id}/history", _SensorChart),
    "interface_chart": ("Interface history", "GET /api/network/interfaces/{id}/history", _InterfaceChart),
    "coverage_gaps": ("Monitoring coverage gaps", "GET /api/monitoring/sensor-library/recommendations", _NoConfig),
    "server_fleet": ("Server fleet", "GET /api/explorer/hosts/resources", _NoConfig),
    "sensor_status": ("Sensors by template", "GET /api/monitoring/sensor-library", _NoConfig),
}

_WIDGET_ID = re.compile(r"^[a-z0-9-]{1,40}$")


class Widget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: str
    title: str | None = Field(None, max_length=80)
    size: str = "medium"
    config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> "Widget":
        if not _WIDGET_ID.match(self.id):
            raise ValueError("widget id must be 1-40 lower-case letters, digits or hyphens")
        if self.type not in WIDGET_TYPES:
            raise ValueError(f"unknown widget type {self.type!r}")
        if self.size not in WIDGET_SIZES:
            raise ValueError(f"size must be one of {WIDGET_SIZES}")
        settings = WIDGET_TYPES[self.type][2].model_validate(self.config)
        self.config = settings.model_dump(mode="json")
        return self


def _unique_ids(widgets: list[Widget]) -> list[Widget]:
    ids = [w.id for w in widgets]
    if len(ids) != len(set(ids)):
        raise ValueError("widget ids must be unique within a dashboard")
    return widgets


class DashboardCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    widgets: list[Widget] = Field(default_factory=list, max_length=MAX_WIDGETS)
    shared: bool = False

    @field_validator("widgets")
    @classmethod
    def _ids(cls, v: list[Widget]) -> list[Widget]:
        return _unique_ids(v)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v


class DashboardUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    widgets: list[Widget] | None = Field(None, max_length=MAX_WIDGETS)
    shared: bool | None = None

    @field_validator("widgets")
    @classmethod
    def _ids(cls, v: list[Widget] | None) -> list[Widget] | None:
        return None if v is None else _unique_ids(v)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v


class DashboardOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    widgets: list[Widget]
    shared: bool
    #: True when the caller owns it (only the owner may change it).
    owned: bool
    created_at: datetime
    updated_at: datetime


class DashboardSummary(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    widget_count: int
    shared: bool
    owned: bool
    updated_at: datetime


class WidgetTypeOut(BaseModel):
    type: str
    label: str
    source: str
    #: JSON schema of the widget's settings.
    settings: dict
