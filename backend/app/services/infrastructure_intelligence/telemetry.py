"""
Canonical telemetry contract (spec sections 16, 17, 33, 38).

One shape for every reading, from every provider, for every resource:

    identity  tenant / site / resource / device / interface / provider /
              capability / metric_name / timestamp
    value     numeric_value | text_value | boolean_value + unit
    quality   status / collection_status / quality / freshness /
              source_timestamp / received_timestamp
    context   dimensions / provider_metadata

The canonical metric-name catalog (``CAPABILITY_METRICS``) is deliberately
data-only, so a new provider maps its native metric names onto an existing
canonical name instead of inventing a vendor column.

The unsupported answer
----------------------
``not_available()`` produces the exact shape spec section 38 requires::

    {"capability": "vpn", "supported": False, "status": "not_available",
     "reason": "provider_capability_not_supported"}

Nothing in this module ever generates a value.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.services.infrastructure_intelligence import vocabulary as V

#: Data-quality buckets. ``stale`` is a first-class answer, not a failure.
QUALITY_GOOD = "good"
QUALITY_DEGRADED = "degraded"
QUALITY_STALE = "stale"
QUALITY_UNKNOWN = "unknown"

QUALITY_STATES: tuple[str, ...] = (
    QUALITY_GOOD, QUALITY_DEGRADED, QUALITY_STALE, QUALITY_UNKNOWN,
)

# ---------------------------------------------------------------------------
# Evidence provenance (Phase 7).
#
# Deliberately re-exported from the shared vocabulary rather than redefined, so
# canonical telemetry and the failover-intelligence evidence list can never drift
# apart. Provenance answers "where did this value come from?" - which is a
# DIFFERENT axis from failover's `finding` ("how does it bear on a hypothesis?").
# A record may be OBSERVED and still contradict an explanation.
#
# Nothing but OBSERVED may be treated as an operational fact on its own.
# ---------------------------------------------------------------------------
PROVENANCE_OBSERVED = V.EVIDENCE_OBSERVED
PROVENANCE_DERIVED = V.EVIDENCE_DERIVED
PROVENANCE_CONTEXT = V.EVIDENCE_CONTEXT
PROVENANCE_SIMULATED = V.EVIDENCE_SIMULATED

PROVENANCE_STATES: tuple[str, ...] = V.EVIDENCE_PROVENANCE_STATES


@dataclass(frozen=True)
class CanonicalTelemetry:
    """One canonical telemetry record. Extensible via ``dimensions``."""

    capability: str
    metric_name: str
    timestamp: datetime
    # -- identity --
    tenant_id: uuid.UUID | None = None
    site_id: uuid.UUID | None = None
    resource_id: uuid.UUID | None = None
    device_id: uuid.UUID | None = None
    interface_id: uuid.UUID | None = None
    provider: str | None = None
    # -- value (exactly one is populated; the rest stay None) --
    numeric_value: float | None = None
    text_value: str | None = None
    boolean_value: bool | None = None
    unit: str | None = None
    # -- quality --
    status: str = V.COLLECTION_COLLECTED
    collection_status: str = V.COLLECTION_COLLECTED
    quality: str = QUALITY_GOOD
    #: Where this value came from. Collector-written telemetry is observed by
    #: default; anything computed or joined in must say so explicitly.
    provenance: str = PROVENANCE_OBSERVED
    source_timestamp: datetime | None = None
    received_timestamp: datetime | None = None
    # -- context --
    dimensions: dict = field(default_factory=dict)
    provider_metadata: dict = field(default_factory=dict)

    @property
    def has_value(self) -> bool:
        return (
            self.numeric_value is not None
            or self.text_value is not None
            or self.boolean_value is not None
        )

    def to_dict(self) -> dict:
        return {
            "capability": self.capability,
            "metric_name": self.metric_name,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "site_id": str(self.site_id) if self.site_id else None,
            "resource_id": str(self.resource_id) if self.resource_id else None,
            "device_id": str(self.device_id) if self.device_id else None,
            "interface_id": str(self.interface_id) if self.interface_id else None,
            "provider": self.provider,
            "numeric_value": self.numeric_value,
            "text_value": self.text_value,
            "boolean_value": self.boolean_value,
            "unit": self.unit,
            "status": self.status,
            "collection_status": self.collection_status,
            "quality": self.quality,
            "provenance": self.provenance,
            "source_timestamp": (
                self.source_timestamp.isoformat() if self.source_timestamp else None
            ),
            "received_timestamp": (
                self.received_timestamp.isoformat()
                if self.received_timestamp else None
            ),
            "dimensions": dict(self.dimensions or {}),
            "provider_metadata": dict(self.provider_metadata or {}),
            "has_value": self.has_value,
        }

    def to_evidence(self, *, now: datetime | None = None) -> dict:
        """This record as an operational-evidence item (Phase 7 contract).

        Carries exactly the fields a consumer needs to judge the value WITHOUT
        re-deriving anything:

            resource_id, timestamp, source, metric, value, unit,
            quality, freshness, provenance

        ``freshness`` is the shared freshness vocabulary, not the quality bucket -
        "how old is this" and "how trustworthy is this" are different questions.
        ``timestamp`` is the SOURCE timestamp when the collector reported one,
        because that is when the device actually looked that way; the receive
        time stays available separately as ``received_at``.
        """
        observed_at = self.source_timestamp or self.timestamp
        age = freshness_seconds(observed_at, now=now)
        from app.services.infrastructure_intelligence import operational_state as O

        freshness = (
            V.FRESHNESS_NEVER_COLLECTED
            if age is None
            else O.freshness_state_for_age(age)
        )
        value: Any = self.numeric_value
        if value is None:
            value = self.text_value if self.text_value is not None else self.boolean_value
        return {
            "resource_id": str(self.resource_id) if self.resource_id else None,
            "timestamp": observed_at.isoformat() if observed_at else None,
            "received_at": (
                self.received_timestamp.isoformat()
                if self.received_timestamp
                else (self.timestamp.isoformat() if self.timestamp else None)
            ),
            "source": self.provider,
            "metric": self.metric_name,
            "capability": self.capability,
            "value": value,
            "unit": self.unit,
            "quality": self.quality,
            "freshness": freshness,
            "freshness_seconds": None if age is None else round(age, 3),
            "provenance": self.provenance,
            "collection_status": self.collection_status,
            "interface_id": str(self.interface_id) if self.interface_id else None,
        }


def not_available(capability: str, reason: str, *, detail: str | None = None) -> dict:
    """The canonical "we do not have this and here is why" answer.

    Returns the exact contract shape from spec section 38. ``detail`` is an
    optional human string and never replaces ``reason``.
    """
    payload = {
        "capability": capability,
        "supported": False,
        "status": V.COLLECTION_NOT_AVAILABLE,
        "reason": reason,
    }
    if detail:
        payload["detail"] = detail
    return payload


def freshness_seconds(
    timestamp: datetime | None, *, now: datetime | None = None
) -> float | None:
    """Age of a sample in seconds. ``None`` when there is no timestamp."""
    if timestamp is None:
        return None
    reference = now or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return max(0.0, (reference - timestamp).total_seconds())


def quality_for(
    timestamp: datetime | None,
    *,
    now: datetime | None = None,
    stale_after_seconds: int = V.TELEMETRY_STALE_AFTER_SECONDS,
) -> str:
    """Quality bucket for a sample, based on age only."""
    age = freshness_seconds(timestamp, now=now)
    if age is None:
        return QUALITY_UNKNOWN
    if age > stale_after_seconds:
        return QUALITY_STALE
    if age > stale_after_seconds * 0.6:
        return QUALITY_DEGRADED
    return QUALITY_GOOD


# ---------------------------------------------------------------------------
# Canonical metric-name catalog. Provider-native metric names are mapped onto
# these; a provider that reports something not listed here still works - it is
# recorded under its own metric_name with the capability that produced it.
# ---------------------------------------------------------------------------
M_AVAILABILITY = "availability_pct"
M_REACHABILITY = "reachability"
M_LATENCY = "latency_ms"
M_JITTER = "jitter_ms"
M_PACKET_LOSS = "packet_loss_pct"

CAPABILITY_METRICS: dict[str, tuple[str, ...]] = {
    V.CAP_INVENTORY: ("inventory_items_total",),
    V.CAP_AVAILABILITY: (M_REACHABILITY, M_AVAILABILITY, M_PACKET_LOSS, M_LATENCY),
    V.CAP_HEALTH: ("health_score", "health_state"),
    V.CAP_CPU: (
        "cpu_utilization_pct", "cpu_load_1m", "cpu_load_5m", "cpu_load_15m",
    ),
    V.CAP_MEMORY: (
        "memory_used_pct", "memory_used_bytes", "memory_total_bytes",
        "memory_free_bytes",
    ),
    V.CAP_TEMPERATURE: ("temperature_celsius",),
    V.CAP_UPTIME: ("uptime_seconds", "uptime_pct"),
    V.CAP_INTERFACES: (
        "interfaces_total", "interfaces_up", "interfaces_down",
        "interface_utilization_pct",
    ),
    V.CAP_BANDWIDTH: (
        "rx_bps", "tx_bps", "bandwidth_mbps", "bandwidth_utilization_pct",
    ),
    V.CAP_PACKET_STATISTICS: (
        "packets_in", "packets_out", "unicast_packets", "multicast_packets",
        "broadcast_packets",
    ),
    V.CAP_ERRORS: ("errors_in", "errors_out", "error_rate_pct"),
    V.CAP_DISCARDS: ("discards_in", "discards_out"),
    V.CAP_WAN: (
        "wan_member_state", "wan_members_total", "wan_members_up", M_LATENCY,
        M_JITTER, M_PACKET_LOSS, "sla_score",
    ),
    V.CAP_SDWAN: (
        "sdwan_path_state", "sdwan_path_score", "sdwan_sla_breaches",
        "sdwan_path_switches",
    ),
    V.CAP_VPN: (
        "tunnel_state", "tunnels_total", "tunnels_up", "tunnel_uptime_seconds",
        "tunnel_rx_bytes", "tunnel_tx_bytes",
    ),
    V.CAP_ROUTING: ("routes_total", "routes_active", "routes_changed"),
    V.CAP_BGP: (
        "bgp_neighbors_total", "bgp_neighbors_established", "prefixes_received",
        "prefixes_advertised",
    ),
    V.CAP_OSPF: (
        "ospf_neighbors_total", "ospf_neighbors_full", "ospf_lsa_count",
    ),
    V.CAP_HIGH_AVAILABILITY: (
        "ha_state", "ha_members_total", "ha_sync_status",
    ),
    V.CAP_SESSIONS: (
        "sessions_total", "sessions_active", "sessions_utilization_pct",
    ),
    V.CAP_POLICIES: ("policies_total", "policy_hits", "policy_blocks"),
    V.CAP_NAT: ("nat_translations_total",),
    V.CAP_VLAN: ("vlans_total", "vlan_count"),
    V.CAP_POE: ("poe_ports_total", "poe_power_watts"),
    V.CAP_CLIENTS: ("clients_total", "clients_connected", "clients_failed"),
    V.CAP_WIRELESS: (
        "ssids_total", "channel", "tx_power_dbm", "retry_rate_pct",
    ),
    V.CAP_RF: ("signal_dbm", "noise_dbm", "snr_db", "channel_utilization_pct"),
    V.CAP_DISK: (
        "disk_used_pct", "disk_used_bytes", "disk_total_bytes",
        "disk_free_bytes",
    ),
    V.CAP_PROCESSES: ("processes_total", "processes_running"),
    V.CAP_SERVICES: ("services_total", "services_down"),
    V.CAP_APPLICATIONS: ("applications_total", "applications_down"),
    V.CAP_STORAGE: (
        "storage_used_bytes", "storage_total_bytes", "iops",
        "throughput_mbps", M_LATENCY,
    ),
    V.CAP_STORAGE_CAPACITY: (
        "capacity_bytes", "used_bytes", "available_bytes", "used_pct",
    ),
    V.CAP_REPLICATION: ("replication_state", "replication_lag_seconds"),
    V.CAP_BACKUP: ("backup_state", "backup_age_seconds", "last_backup_at"),
    V.CAP_CLOUD_METRICS: (
        "cloud_requests", "cloud_errors", "cloud_throttled_requests",
    ),
    V.CAP_CLOUD_EVENTS: ("cloud_events_total", "cloud_critical_events"),
    V.CAP_CLOUD_RESOURCES: ("cloud_resources_total", "cloud_resources_unhealthy"),
    V.CAP_CLOUD_SERVICES: ("cloud_services_total", "cloud_services_unhealthy"),
    V.CAP_CLOUD_ACTIVITY: ("activity_events_total", "activity_failures"),
    V.CAP_TOPOLOGY: ("topology_nodes_total", "topology_edges_total"),
    V.CAP_DEPENDENCIES: ("dependencies_total", "dependents_total"),
    V.CAP_CONFIGURATION: ("config_changes_total", "config_last_changed_at"),
    V.CAP_ALERTS: ("alerts_active", "alerts_critical"),
    V.CAP_LOGS: ("log_events_total", "log_error_events"),
    V.CAP_ACTIONS: ("actions_available_total",),
    V.CAP_REMEDIATION: ("remediations_available_total",),
}

ALL_METRIC_NAMES: tuple[str, ...] = tuple(
    sorted({name for names in CAPABILITY_METRICS.values() for name in names})
)

#: metric_name -> capability. Built once so callers never re-derive it.
METRIC_CAPABILITY: dict[str, str] = {
    name: capability
    for capability, names in CAPABILITY_METRICS.items()
    for name in names
}

#: Provider-native metric spellings -> canonical metric name. Kept data-only so
#: wiring a new provider means adding rows here, never editing the pipeline.
_METRIC_ALIASES: dict[str, str] = {
    "cpu": "cpu_utilization_pct",
    "cpu_load": "cpu_load_1m",
    "cpuutilization": "cpu_utilization_pct",
    "cpu_usage": "cpu_utilization_pct",
    "percentagecpu": "cpu_utilization_pct",
    "memory": "memory_used_pct",
    "memoryusage": "memory_used_pct",
    "memory_used": "memory_used_bytes",
    "memory_available": "memory_free_bytes",
    "temp": "temperature_celsius",
    "temperature": "temperature_celsius",
    "uptime": "uptime_seconds",
    "sysuptime": "uptime_seconds",
    "ping": "reachability",
    "status": "reachability",
    "availability": "availability_pct",
    "response_time": "latency_ms",
    "responsetime": "latency_ms",
    "latency": "latency_ms",
    "rtt": "latency_ms",
    "jitter": "jitter_ms",
    "packet_loss": "packet_loss_pct",
    "loss": "packet_loss_pct",
    "traffic_in": "rx_bps",
    "traffic_out": "tx_bps",
    "rx": "rx_bps",
    "tx": "tx_bps",
    "bandwidth": "bandwidth_mbps",
    "in_octets": "rx_bps",
    "out_octets": "tx_bps",
    "in_errors": "errors_in",
    "out_errors": "errors_out",
    "discards_in": "discards_in",
    "discards_out": "discards_out",
    "sessions": "sessions_total",
    "concurrent_sessions": "sessions_active",
    "vpn_tunnels": "tunnels_total",
    "vpn_tunnels_up": "tunnels_up",
    "ipsec_tunnels": "tunnels_total",
    "clients": "clients_total",
    "client_count": "clients_connected",
    "users": "clients_total",
    "disk": "disk_used_pct",
    "disk_usage": "disk_used_pct",
    "disk_free": "disk_free_bytes",
    "storage_used": "storage_used_bytes",
    "capacity": "capacity_bytes",
    "used": "used_bytes",
    "used_pct": "used_pct",
    "iops": "iops",
    "throughput": "throughput_mbps",
    "routes": "routes_total",
    "bgp_neighbors": "bgp_neighbors_total",
    "bgp_peers": "bgp_neighbors_total",
    "peers_established": "bgp_neighbors_established",
    "prefixes": "prefixes_received",
    "ospf_neighbors": "ospf_neighbors_total",
    "ha_state": "ha_state",
    "sensor_health": "health_score",
}


def canonical_metric_name(raw: str | None) -> str:
    """Map a provider-native metric name to a canonical metric name.

    An unrecognised name is NOT dropped and NOT guessed: it is normalised to
    snake_case and returned as-is, so its series survives under its own name.
    """
    if not raw:
        return "unknown"
    token = raw.strip().lower().replace("-", "_").replace(" ", "_")
    if token in METRIC_CAPABILITY:
        return token
    if token in _METRIC_ALIASES:
        return _METRIC_ALIASES[token]
    return token


def capability_for_metric(metric_name: str | None) -> str | None:
    """Capability a metric belongs to, or None when nobody mapped it.

    The name is canonicalised FIRST, so a provider's own spelling ("CPU",
    "Response Time") resolves exactly like the canonical name does - otherwise a
    collector would write a capability-less reading for a metric the catalog
    already knows.
    """
    if not metric_name:
        return None
    token = canonical_metric_name(metric_name)
    return METRIC_CAPABILITY.get(token)


def metrics_for_capability(capability: str) -> tuple[str, ...]:
    """Canonical metric names for a capability (empty tuple when unknown)."""
    return CAPABILITY_METRICS.get(capability, ())


@dataclass
class SeriesSummary:
    """Deterministic summary of one metric series (spec section 33)."""

    metric_name: str
    capability: str | None
    unit: str | None
    samples: int
    minimum: float | None
    maximum: float | None
    average: float | None
    latest_value: float | None
    latest_timestamp: datetime | None
    quality: str

    def to_dict(self) -> dict:
        return {
            "metric_name": self.metric_name,
            "capability": self.capability,
            "unit": self.unit,
            "samples": self.samples,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "average": self.average,
            "latest_value": self.latest_value,
            "latest_timestamp": (
                self.latest_timestamp.isoformat() if self.latest_timestamp else None
            ),
            "quality": self.quality,
        }


def summarize_series(
    records: list[CanonicalTelemetry],
    *,
    now: datetime | None = None,
) -> SeriesSummary:
    """Aggregate a metric series. A series with no numeric samples stays honest.

    ``latest_value`` is the value at the newest timestamp - never the last item
    in the list, so callers may pass records in any order.
    """
    numeric = [
        r for r in records if r.numeric_value is not None
    ]
    ordered = sorted(records, key=lambda r: r.timestamp or datetime.min.replace(
        tzinfo=timezone.utc
    ))
    latest = ordered[-1] if ordered else None
    values = [float(r.numeric_value) for r in numeric]  # type: ignore[arg-type]
    latest_numeric = next(
        (r for r in reversed(ordered) if r.numeric_value is not None), None
    )
    return SeriesSummary(
        metric_name=latest.metric_name if latest else "unknown",
        capability=latest.capability if latest else None,
        unit=latest.unit if latest else None,
        samples=len(records),
        minimum=round(min(values), 6) if values else None,
        maximum=round(max(values), 6) if values else None,
        average=round(sum(values) / len(values), 6) if values else None,
        latest_value=(
            float(latest_numeric.numeric_value)  # type: ignore[arg-type]
            if latest_numeric is not None else None
        ),
        latest_timestamp=latest.timestamp if latest else None,
        quality=quality_for(latest.timestamp if latest else None, now=now),
    )
