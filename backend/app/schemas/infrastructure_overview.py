"""
Pydantic v2 schemas for the canonical Infrastructure Overview.

Kept separate from the ORM model (per Clean Architecture, same convention as
schemas/site.py) so the API contract evolves independently of persistence.

`InfrastructureHealthOut` is deliberately fully typed: it is the contract the
spec requires Dashboard, Infrastructure, Topology and Reports to agree on, so
a missing or renamed field must be a hard failure rather than a silent
`undefined` in the UI. The per-domain blocks stay `dict` because their shape
varies with what the tenant actually has (e.g. `topology.status` and
`sensors.status` only appear when there is nothing to report) - typing them
strictly would force placeholders, which the data-trust rule forbids.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class InfrastructureHealthOut(BaseModel):
    """The single health contract every page must agree on."""

    # devices
    total_devices: int
    healthy_devices: int
    warning_devices: int
    critical_devices: int
    unknown_devices: int

    # sites
    total_sites: int
    healthy_sites: int
    warning_sites: int
    critical_sites: int
    unknown_sites: int

    # sensors
    total_sensors: int
    healthy_sensors: int
    warning_sensors: int
    down_sensors: int
    unknown_sensors: int

    # alerts (see alerts.active_definition for how "active" is defined)
    total_alerts: int
    active_alerts: int
    critical_alerts: int
    warning_alerts: int
    info_alerts: int

    # incidents
    total_incidents: int
    open_incidents: int

    # collector runs (all-time; windows live in collector_runs.windows)
    total_collector_runs: int
    successful_collector_runs: int
    failed_collector_runs: int

    # topology / interfaces
    total_relationships: int
    total_interfaces: int


class InfrastructureOverviewOut(BaseModel):
    tenant_id: str
    health: InfrastructureHealthOut
    sites: list[dict[str, Any]]
    devices: dict[str, Any]
    sensors: dict[str, Any]
    alerts: dict[str, Any]
    incidents: dict[str, Any]
    collector_runs: dict[str, Any]
    monitoring_sources: dict[str, Any]
    topology: dict[str, Any]
    geography: dict[str, Any]
    last_updated: str
