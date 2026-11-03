"""Network Monitoring Center API shapes (observability plan P4 slice 1)."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class NetworkInterfaceOut(BaseModel):
    id: uuid.UUID
    device_id: uuid.UUID
    device_name: str
    device_type: str | None
    name: str
    description: str | None
    interface_type: str | None
    admin_status: str
    oper_status: str
    speed_mbps: float | None
    #: Derived from two counter samples; None until a second poll.
    rx_bps: float | None
    tx_bps: float | None
    throughput_bps: float | None
    utilization_pct: float | None
    #: Cumulative counters as the device reports them.
    in_errors: float | None
    out_errors: float | None
    last_telemetry_at: datetime | None
    last_status_change_at: datetime | None
    #: False when the last counter sample is older than 30 minutes.
    fresh: bool


class ErrorInterfaceOut(NetworkInterfaceOut):
    #: Sum of recorded in + out error rates over the last hour (errors / s).
    errors_per_s_1h: float


class InterfaceTotals(BaseModel):
    total: int
    up: int
    down: int
    #: Enabled (admin up) but operationally down.
    unexpected_down: int
    admin_down: int
    #: Counter sample in the last 30 minutes.
    fresh: int
    #: Fresh, with a traffic rate.
    measured: int
    devices: int
    last_telemetry_at: datetime | None


class VpnTunnelOut(BaseModel):
    id: uuid.UUID
    device_id: uuid.UUID
    device_name: str
    name: str
    tunnel_type: str
    remote_gateway: str | None
    status: str
    phase2_up: int | None
    phase2_total: int | None
    bytes_in: float | None
    bytes_out: float | None
    last_change_at: datetime | None
    last_seen_at: datetime


class TunnelSummary(BaseModel):
    total: int
    up: int
    down: int
    items: list[VpnTunnelOut]


class NetworkOverviewOut(BaseModel):
    as_of: datetime
    interfaces: InterfaceTotals
    top_utilization: list[NetworkInterfaceOut]
    top_throughput: list[NetworkInterfaceOut]
    unexpected_down: list[NetworkInterfaceOut]
    top_errors: list[ErrorInterfaceOut]
    tunnels: TunnelSummary


class NetworkInterfacePage(BaseModel):
    items: list[NetworkInterfaceOut]
    total: int


class HistoryChannel(BaseModel):
    samples: int
    last_recorded_at: datetime | None


class NetworkInterfaceDetail(NetworkInterfaceOut):
    mac_address: str | None
    vlan_id: int | None
    #: Recorded history per channel (rx_bps, tx_bps, interface_utilization_pct, errors_in, errors_out).
    history: dict[str, HistoryChannel]


class HistoryPoint(BaseModel):
    t: datetime
    avg: float
    max: float


class InterfaceHistoryOut(BaseModel):
    hours: int
    bucket_seconds: int
    series: dict[str, list[HistoryPoint]]
