"""
Collector interface + registry for monitoring sources.

Design goal: new monitoring platforms can be added without modifying any
existing code. This is achieved with the classic Adapter + Registry
pattern (the Collector Framework's evolution of the previous
`MonitoringAdapter`/`AdapterRegistry` pattern):

  1. Every vendor integration is a class implementing `BaseCollector`.
  2. It registers itself against a `source_type` string via
     `@CollectorRegistry.register("prtg")`.
  3. Callers only ever ask the registry for
     `CollectorRegistry.get(source.source_type)` - they never import a
     vendor class directly, so adding vendor #11 means adding one new
     file, not editing the orchestrator, the API, or the registry itself.

Unlike the old adapter pattern, a `BaseCollector` splits work into three
distinct steps (`authenticate`, `collect`, `normalize`) plus a shared
`save` step, so that raw vendor I/O and pure normalization logic can be
tested independently.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from app.core.security import MonitoringSourceConnectionInfo
    from app.services.monitoring.bundle_saver import BundleSaver, SaveResult


@dataclass
class NormalizedDevice:
    external_id: str
    name: str
    device_type: str          # router | switch | firewall | wireless_ap | server | vm | cloud_resource | storage | other
    vendor: str | None = None
    model: str | None = None
    os_version: str | None = None
    hostname: str | None = None
    ip_address: str | None = None
    mac_address: str | None = None
    status: str = "unknown"   # up | down | warning | unknown
    site_hint: str | None = None   # optional site name/code for identity resolution
    #: Authoritative hardware serial, when the provider actually exposes one
    #: (SNMP ENTITY-MIB entPhysicalSerialNum, WMI Win32_BIOS SerialNumber, a
    #: vendor API's serial field). This is the ONE cross-source identity key:
    #: two different collectors reporting the same serial are describing the
    #: same physical device, so they enrich one canonical Device instead of
    #: creating two. Deliberately NOT derived from hostname or IP - see
    #: app/services/discovery/identity.py.
    serial_number: str | None = None
    # Free-form, source-specific metadata persisted to Device.custom_metadata
    # (keys should follow app/models/device_types.py field specs).
    custom_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class NormalizedInterface:
    device_external_id: str
    external_id: str
    name: str
    admin_status: str = "unknown"
    oper_status: str = "unknown"
    speed_mbps: float | None = None
    mac_address: str | None = None
    in_octets: float | None = None
    out_octets: float | None = None
    in_errors: float | None = None
    out_errors: float | None = None


@dataclass
class NormalizedSensorReading:
    external_id: str
    name: str
    metric_type: str
    value: float | None
    unit: str | None = None
    status: str = "unknown"
    warning_threshold: float | None = None
    critical_threshold: float | None = None
    device_external_id: str | None = None
    interface_external_id: str | None = None


@dataclass
class NormalizedMetric:
    metric_type: str
    value: float
    recorded_at: datetime
    unit: str | None = None
    device_external_id: str | None = None
    sensor_external_id: str | None = None


@dataclass
class NormalizedEvent:
    event_type: str
    message: str
    occurred_at: datetime
    severity: str = "info"          # critical | warning | info
    external_id: str | None = None
    device_external_id: str | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class NormalizedAlertSignal:
    """A vendor-native alarm/alert, distinct from threshold-derived alerts."""
    external_id: str
    title: str
    severity: str                   # critical | warning | info
    triggered_at: datetime
    description: str | None = None
    device_external_id: str | None = None


@dataclass
class NormalizedNeighbor:
    """One OBSERVED adjacency between the collecting device and a remote one.

    Produced by protocols that actually report adjacencies (LLDP, CDP) — never
    inferred from IP subnet, hostname similarity or ping. Phase 3A.
    """

    #: LOCAL device this adjacency was observed FROM (NormalizedDevice.external_id).
    device_external_id: str
    #: Local port the neighbour was seen on (interface NAME, so it correlates
    #: with the persisted Interface row rather than inventing a new one).
    local_interface: str | None = None
    #: Neighbour's MANAGEMENT ADDRESS (LLDP lldpRemManAddr / CDP cdpCacheAddress).
    #: This is what the existing identity resolution matches on. None when the
    #: protocol did not expose one - in that case the adjacency is real but
    #: cannot be resolved to a Device, so NO edge is written for it.
    remote_address: str | None = None
    #: Remote system name / device id, kept for provenance only. Never used to
    #: fabricate a Device.
    remote_identity: str | None = None
    #: Remote port id or description.
    remote_interface: str | None = None
    #: Remote chassis ID, when the protocol supplied one.
    remote_chassis_id: str | None = None
    #: Remote platform/model string, when supplied.
    remote_platform: str | None = None
    #: "lldp" | "cdp" - the PROTOCOL is evidence; it is not the relationship type.
    evidence_type: str = "lldp"


@dataclass
class NormalizedBundle:
    devices: list[NormalizedDevice] = field(default_factory=list)
    interfaces: list[NormalizedInterface] = field(default_factory=list)
    sensors: list[NormalizedSensorReading] = field(default_factory=list)
    metrics: list[NormalizedMetric] = field(default_factory=list)
    events: list[NormalizedEvent] = field(default_factory=list)
    alerts: list[NormalizedAlertSignal] = field(default_factory=list)
    #: Observed adjacencies (LLDP/CDP). Persisted as topology edges by
    #: BundleSaver through DiscoveryService - never inserted by the collector.
    neighbors: list[NormalizedNeighbor] = field(default_factory=list)
    #: Per-protocol collection outcome so "SNMP failed" is never reported as
    #: "no neighbours found". Values: collected | empty | unavailable | failed.
    neighbor_status: dict[str, str] = field(default_factory=dict)


@dataclass
class ConnectionCheck:
    success: bool
    message: str


# -- Readiness vocabulary (Phase 3B §10) -------------------------------------
# A readiness probe answers ONE question: "can this configured source be polled
# right now?" These distinct states must never be collapsed, because they have
# completely different remedies:
#     configured != reachable != authenticated != protocol_ready != collected
# e.g. "SNMP TCP is reachable" says nothing about whether the community is
# accepted, and an accepted community says nothing about whether LLDP was
# collected. Reporting one as another is the failure mode this vocabulary exists
# to prevent.
READINESS_READY = "ready"
READINESS_CONFIGURATION_ERROR = "configuration_error"
READINESS_CREDENTIAL_ERROR = "credential_error"
READINESS_UNREACHABLE = "unreachable"
READINESS_AUTHENTICATION_FAILED = "authentication_failed"
READINESS_AUTHORIZATION_FAILED = "authorization_failed"
READINESS_PROTOCOL_ERROR = "protocol_error"
READINESS_TIMEOUT = "timeout"
READINESS_UNSUPPORTED = "unsupported"

STAGE_CONFIGURATION = "configuration"
STAGE_CREDENTIALS = "credentials"
STAGE_NETWORK = "network"
STAGE_TRANSPORT = "transport"
STAGE_AUTHENTICATION = "authentication"
STAGE_PROTOCOL = "protocol"
STAGE_READY = "ready"


@dataclass
class ReadinessResult:
    """Provider-neutral outcome of a READ-ONLY readiness probe.

    Diagnostic only: produces no Device/Interface/Capability/Relationship/
    topology rows. `target` and `safe_details` must never carry credentials -
    see `app/services/monitoring/readiness.py`, which builds every instance.
    """

    status: str
    stage: str
    message: str
    #: Safe, human-readable representation of where the probe pointed
    #: (host:port, or "not configured"). Never a credential.
    target: str
    #: Display label of the protocol actually exercised, e.g. "SNMP",
    #: "WMI/WSMan". Deliberately distinct from `source_type`.
    protocol: str
    #: Non-secret diagnostic extras only (port, snmp version, auth_type...).
    safe_details: dict[str, Any] = field(default_factory=dict)

    @property
    def is_ready(self) -> bool:
        return self.status == READINESS_READY


class CollectorConfigError(ValueError):
    """Config dict/columns missing required keys for this collector."""


class CollectorAuthenticationError(RuntimeError):
    """authenticate() failed after config was otherwise valid."""


class BaseCollector(abc.ABC):
    source_type: ClassVar[str]
    display_name: ClassVar[str]
    implemented: ClassVar[bool] = True

    def __init__(self, source: "MonitoringSourceConnectionInfo"):
        """`source` exposes the typed columns (hostname, ip_address, port,
        protocol, api_key, snmp_version, username, password) plus the
        `config` JSONB overflow dict. Secrets are already decrypted by the
        caller (CollectionOrchestrator) before this constructor runs."""
        self.source = source

    @abc.abstractmethod
    async def authenticate(self) -> ConnectionCheck:
        """Verify credentials/reachability. Must not raise for expected
        failures (bad creds, unreachable host) - return ConnectionCheck(False, ...)
        instead. May raise CollectorConfigError for missing config."""
        raise NotImplementedError

    @abc.abstractmethod
    async def collect(self) -> Any:
        """Pull the raw vendor-shaped payload. Returns whatever shape is
        natural for that vendor (dict, list of dicts, SDK objects) - no
        normalization happens here."""
        raise NotImplementedError

    @abc.abstractmethod
    def normalize(self, raw: Any) -> NormalizedBundle:
        """Pure function: raw vendor payload -> NormalizedBundle. No I/O."""
        raise NotImplementedError

    async def save(self, bundle: NormalizedBundle, saver: "BundleSaver") -> "SaveResult":
        """Default implementation delegates to the shared BundleSaver
        (upserts via repositories). Collectors only override this if they
        need vendor-specific save behavior."""
        return await saver.save(self.source, bundle)

    async def aclose(self) -> None:
        """Release anything this collector owns. Default: nothing.

        Called by the CollectionOrchestrator after EVERY run, so a collector
        that holds a client, a session pool or an SNMP engine's dispatcher can
        hand the resources back instead of leaking them once per run.
        Implementations must never raise: cleanup failures cannot be allowed to
        change a run's outcome.
        """
        return None


class CollectorRegistry:
    _collectors: dict[str, type[BaseCollector]] = {}

    @classmethod
    def register(cls, source_type: str):
        def _decorator(collector_cls: type[BaseCollector]):
            collector_cls.source_type = source_type
            cls._collectors[source_type] = collector_cls
            return collector_cls
        return _decorator

    @classmethod
    def get(cls, source_type: str) -> type[BaseCollector]:
        try:
            return cls._collectors[source_type]
        except KeyError as exc:
            raise ValueError(
                f"No collector registered for source_type={source_type!r}. "
                f"Supported: {sorted(cls._collectors)}"
            ) from exc

    @classmethod
    def list_types(cls) -> list[type[BaseCollector]]:
        return list(cls._collectors.values())
