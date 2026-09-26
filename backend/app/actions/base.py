"""
Device Action Plane - core abstractions (Phase 1 foundation).

This is the EXECUTION counterpart to the Collector Framework
(app/collectors/): collectors pull telemetry IN, action providers push
controlled change (or on-demand diagnostics) OUT. The two frameworks
deliberately mirror each other:

  Collector Framework                Action Plane
  -------------------------------    --------------------------------
  BaseCollector                      DeviceActionProvider
  @CollectorRegistry.register(...)   @ActionProviderRegistry.register(...)
  source_type -> collector class     provider_kind -> provider class
  authenticate/collect/normalize     supported_actions/execute

Design rules (from the Device Control Plane spec):

  * The web dashboard NEVER connects to a device. It calls the backend
    Action API; the Action Orchestrator (DeviceActionService) resolves a
    provider here and executes server-side.
  * Every action carries a classification (READ_ONLY .. DESTRUCTIVE) from
    a single catalog - ACTION_CATALOG - which is also served through the
    API so the frontend NEVER hard-codes action metadata.
  * Providers receive already-decrypted connection info (the same
    MonitoringSourceConnectionInfo seam the collectors use) and never see
    tenant/user context they could leak.
  * A provider that cannot honestly perform an action must say so
    (ActionNotSupported / ActionResult(success=False)) - never fabricate
    execution evidence.

Phase 1 ships read-only ICMP + SNMP diagnostic providers. SSH / REST /
vendor adapters (FortiGate, Meraki, Cisco, Aruba ...) plug into the SAME
registry later without touching the orchestrator, API, or policy engine.
"""
from __future__ import annotations

import abc
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from app.core.security import MonitoringSourceConnectionInfo


class DeviceActionType(str, Enum):
    """Canonical device action vocabulary.

    Read-only diagnostics (Phase 1 + future):
      PING, HEALTH_CHECK, GET_STATUS, GET_INTERFACES, GET_FIRMWARE,
      GET_CONFIG_STATUS, COLLECT_DIAGNOSTICS

    State-changing (require policy evaluation + approval; adapters land
    with the vendor phases - the catalog reserves the vocabulary so the
    policy engine, API and audit shapes are stable from day one):
      RESTART_SERVICE, REBOOT_DEVICE, ENABLE_INTERFACE, DISABLE_INTERFACE,
      APPLY_CONFIGURATION, SYNC_CONFIGURATION, UPGRADE_FIRMWARE,
      ROLLBACK_FIRMWARE
    """

    PING = "PING"
    HEALTH_CHECK = "HEALTH_CHECK"
    GET_STATUS = "GET_STATUS"
    GET_INTERFACES = "GET_INTERFACES"
    GET_FIRMWARE = "GET_FIRMWARE"
    GET_CONFIG_STATUS = "GET_CONFIG_STATUS"
    COLLECT_DIAGNOSTICS = "COLLECT_DIAGNOSTICS"

    RESTART_SERVICE = "RESTART_SERVICE"
    REBOOT_DEVICE = "REBOOT_DEVICE"
    ENABLE_INTERFACE = "ENABLE_INTERFACE"
    DISABLE_INTERFACE = "DISABLE_INTERFACE"
    APPLY_CONFIGURATION = "APPLY_CONFIGURATION"
    SYNC_CONFIGURATION = "SYNC_CONFIGURATION"
    UPGRADE_FIRMWARE = "UPGRADE_FIRMWARE"
    ROLLBACK_FIRMWARE = "ROLLBACK_FIRMWARE"

    # Phase 2 - PRTG Network Monitor (read diagnostics + pause/resume).
    PRTG_QUERY_SENSOR = "PRTG_QUERY_SENSOR"
    PRTG_QUERY_DEVICE = "PRTG_QUERY_DEVICE"
    PRTG_PAUSE_SENSOR = "PRTG_PAUSE_SENSOR"
    PRTG_RESUME_SENSOR = "PRTG_RESUME_SENSOR"

    # Phase 3 - vendor MIB extensions (READ_ONLY health counters that the
    # standard MIB-II does not expose). These are the FIRST actions that
    # genuinely require vendor-specific adapters: the OIDs live in the
    # vendor's own MIB, not in MIB-II.
    #   CISCO_QUERY_HEALTH  -> CISCO-PROCESS-MIB + CISCO-MEMORY-POOL-MIB
    #   ARUBA_QUERY_HEALTH  -> WLSX-SYSTEMEXT-MIB
    CISCO_QUERY_HEALTH = "CISCO_QUERY_HEALTH"
    CISCO_QUERY_PROCESSES = (
        "CISCO_QUERY_PROCESSES"
    )  # per-process CPU + memory (cpmProcessExtRevTable)
    ARUBA_QUERY_HEALTH = "ARUBA_QUERY_HEALTH"


class ActionClassification(str, Enum):
    """Risk classification driving the policy engine.

    READ_ONLY                  -> may execute automatically (when tenant
                                  policy + capability permit)
    LOW_RISK                   -> reversible/minimal blast radius; auto
                                  only when tenant policy explicitly allows
    CHANGE_REQUIRING_APPROVAL  -> human approval required
    HIGH_RISK                  -> approval + elevated role + maintenance
                                  window (e.g. REBOOT_DEVICE)
    DESTRUCTIVE                -> approval + elevated role; never auto
                                  (e.g. ROLLBACK_FIRMWARE)
    """

    READ_ONLY = "READ_ONLY"
    LOW_RISK = "LOW_RISK"
    CHANGE_REQUIRING_APPROVAL = "CHANGE_REQUIRING_APPROVAL"
    HIGH_RISK = "HIGH_RISK"
    DESTRUCTIVE = "DESTRUCTIVE"


# Provider transport kinds. "icmp" and "snmp" exist today; "ssh"/"rest"/
# "vendor:*" arrive with the later phases without any change to this
# module's consumers (the registry resolves by capability, not kind).
PROVIDER_KIND_ICMP = "icmp"
PROVIDER_KIND_SNMP = "snmp"
PROVIDER_KIND_SSH = "ssh"
PROVIDER_KIND_REST = "rest"
PROVIDER_KIND_VENDOR = "vendor"


@dataclass(frozen=True)
class ActionSpec:
    """One catalog entry: everything the policy engine needs to route and
    gate an action. The single source of truth - served to clients via
    GET /api/device-actions/catalog so the UI never hard-codes it."""
    action: DeviceActionType
    classification: ActionClassification
    provider_kind: str
    required_capability: str          # DeviceCapability flag that must be True
    description: str


ACTION_CATALOG: dict[DeviceActionType, ActionSpec] = {
    # ---- Read-only diagnostics -------------------------------------------
    DeviceActionType.PING: ActionSpec(
        action=DeviceActionType.PING,
        classification=ActionClassification.READ_ONLY,
        provider_kind=PROVIDER_KIND_ICMP,
        required_capability="supports_ping",
        description="ICMP echo reachability and latency probe",
    ),
    DeviceActionType.HEALTH_CHECK: ActionSpec(
        action=DeviceActionType.HEALTH_CHECK,
        classification=ActionClassification.READ_ONLY,
        provider_kind=PROVIDER_KIND_ICMP,
        required_capability="supports_ping",
        description="Reachability + basic response health",
    ),
    DeviceActionType.GET_STATUS: ActionSpec(
        action=DeviceActionType.GET_STATUS,
        classification=ActionClassification.READ_ONLY,
        provider_kind=PROVIDER_KIND_SNMP,
        required_capability="supports_snmp",
        description="System status via SNMP (sysDescr/sysName/uptime)",
    ),
    DeviceActionType.GET_INTERFACES: ActionSpec(
        action=DeviceActionType.GET_INTERFACES,
        classification=ActionClassification.READ_ONLY,
        provider_kind=PROVIDER_KIND_SNMP,
        required_capability="supports_snmp",
        description="Interface table via SNMP (status/speed/errors/octets)",
    ),
    DeviceActionType.GET_FIRMWARE: ActionSpec(
        action=DeviceActionType.GET_FIRMWARE,
        classification=ActionClassification.READ_ONLY,
        provider_kind=PROVIDER_KIND_SNMP,
        required_capability="supports_snmp",
        description="Firmware/OS version via SNMP sysDescr",
    ),
    DeviceActionType.GET_CONFIG_STATUS: ActionSpec(
        action=DeviceActionType.GET_CONFIG_STATUS,
        classification=ActionClassification.READ_ONLY,
        provider_kind=PROVIDER_KIND_SNMP,
        required_capability="supports_snmp",
        description="Configuration status (running config indicators)",
    ),
    DeviceActionType.COLLECT_DIAGNOSTICS: ActionSpec(
        action=DeviceActionType.COLLECT_DIAGNOSTICS,
        classification=ActionClassification.READ_ONLY,
        provider_kind=PROVIDER_KIND_SSH,
        required_capability="supports_ssh",
        description="Collect device diagnostic bundle (SSH; Phase 3+)",
    ),
    # ---- State-changing (vocabulary reserved; adapters arrive with the
    #      vendor phases - the policy engine gates them from day one) ------
    DeviceActionType.RESTART_SERVICE: ActionSpec(
        action=DeviceActionType.RESTART_SERVICE,
        classification=ActionClassification.CHANGE_REQUIRING_APPROVAL,
        provider_kind=PROVIDER_KIND_SSH,
        required_capability="supports_ssh",
        description="Restart a named service on the device",
    ),
    DeviceActionType.REBOOT_DEVICE: ActionSpec(
        action=DeviceActionType.REBOOT_DEVICE,
        classification=ActionClassification.HIGH_RISK,
        provider_kind=PROVIDER_KIND_SSH,
        required_capability="supports_reboot",
        description="Reboot the device (maintenance window required)",
    ),
    DeviceActionType.ENABLE_INTERFACE: ActionSpec(
        action=DeviceActionType.ENABLE_INTERFACE,
        classification=ActionClassification.CHANGE_REQUIRING_APPROVAL,
        provider_kind=PROVIDER_KIND_SNMP,
        required_capability="supports_config",
        description="Enable a network interface",
    ),
    DeviceActionType.DISABLE_INTERFACE: ActionSpec(
        action=DeviceActionType.DISABLE_INTERFACE,
        classification=ActionClassification.HIGH_RISK,
        provider_kind=PROVIDER_KIND_SNMP,
        required_capability="supports_config",
        description="Disable a network interface (may cut connectivity)",
    ),
    DeviceActionType.APPLY_CONFIGURATION: ActionSpec(
        action=DeviceActionType.APPLY_CONFIGURATION,
        classification=ActionClassification.CHANGE_REQUIRING_APPROVAL,
        provider_kind=PROVIDER_KIND_SSH,
        required_capability="supports_config",
        description="Apply a configuration change",
    ),
    DeviceActionType.SYNC_CONFIGURATION: ActionSpec(
        action=DeviceActionType.SYNC_CONFIGURATION,
        classification=ActionClassification.LOW_RISK,
        provider_kind=PROVIDER_KIND_SSH,
        required_capability="supports_config",
        description="Synchronize configuration state",
    ),
    DeviceActionType.UPGRADE_FIRMWARE: ActionSpec(
        action=DeviceActionType.UPGRADE_FIRMWARE,
        classification=ActionClassification.HIGH_RISK,
        provider_kind=PROVIDER_KIND_VENDOR,
        required_capability="supports_firmware",
        description="Upgrade device firmware (lifecycle engine phase)",
    ),
    DeviceActionType.ROLLBACK_FIRMWARE: ActionSpec(
        action=DeviceActionType.ROLLBACK_FIRMWARE,
        classification=ActionClassification.DESTRUCTIVE,
        provider_kind=PROVIDER_KIND_VENDOR,
        required_capability="supports_rollback",
        description="Roll back firmware to the previous image",
    ),
    # ---- Phase 2 - PRTG Network Monitor ----------------------------------
    DeviceActionType.PRTG_QUERY_SENSOR: ActionSpec(
        action=DeviceActionType.PRTG_QUERY_SENSOR,
        classification=ActionClassification.READ_ONLY,
        provider_kind="vendor:prtg",
        required_capability="supports_monitoring_actions",
        description="Live PRTG sensor row (status, values, device/group context)",
    ),
    DeviceActionType.PRTG_QUERY_DEVICE: ActionSpec(
        action=DeviceActionType.PRTG_QUERY_DEVICE,
        classification=ActionClassification.READ_ONLY,
        provider_kind="vendor:prtg",
        required_capability="supports_monitoring_actions",
        description="Live PRTG device row with sensor counters",
    ),
    DeviceActionType.PRTG_PAUSE_SENSOR: ActionSpec(
        action=DeviceActionType.PRTG_PAUSE_SENSOR,
        classification=ActionClassification.CHANGE_REQUIRING_APPROVAL,
        provider_kind="vendor:prtg",
        required_capability="supports_monitoring_actions",
        description="Pause a PRTG sensor indefinitely (human approval required)",
    ),
    DeviceActionType.PRTG_RESUME_SENSOR: ActionSpec(
        action=DeviceActionType.PRTG_RESUME_SENSOR,
        classification=ActionClassification.CHANGE_REQUIRING_APPROVAL,
        provider_kind="vendor:prtg",
        required_capability="supports_monitoring_actions",
        description="Resume a paused PRTG sensor (human approval required)",
    ),

    # ---- Phase 3: vendor MIB health extensions (read-only) ----------------
    DeviceActionType.CISCO_QUERY_HEALTH: ActionSpec(
        action=DeviceActionType.CISCO_QUERY_HEALTH,
        classification=ActionClassification.READ_ONLY,
        provider_kind="vendor:cisco",
        required_capability="supports_snmp",
        description=(
            "Read Cisco CPU utilisation and memory-pool usage over SNMP "
            "(CISCO-PROCESS-MIB, CISCO-MEMORY-POOL-MIB)"
        ),
    ),
    DeviceActionType.ARUBA_QUERY_HEALTH: ActionSpec(
        action=DeviceActionType.ARUBA_QUERY_HEALTH,
        classification=ActionClassification.READ_ONLY,
        provider_kind="vendor:aruba",
        required_capability="supports_snmp",
        description=(
            "Read Aruba controller CPU load and memory usage over SNMP "
            "(WLSX-SYSTEMEXT-MIB)"
        ),
    ),

        DeviceActionType.CISCO_QUERY_PROCESSES: ActionSpec(
            action=DeviceActionType.CISCO_QUERY_PROCESSES,
            classification=ActionClassification.READ_ONLY,
            provider_kind="vendor:cisco",
            required_capability="supports_snmp",
            description=(
                "Read Cisco per-process CPU utilisation and memory over SNMP "
                "(CISCO-PROCESS-MIB cpmProcessExtRevTable), sorted by CPU "
                "descending so the top-talkers appear first"
            ),
        ),
}

# Actions whose adapters exist. Everything else resolves to an honest
# "provider not implemented" at execution time - never a mock.
# Phase 1: ICMP + SNMP diagnostics. Phase 2: + PRTG queries.
# Phase 3: + vendor MIB health extensions (Cisco / Aruba).
IMPLEMENTED_ACTIONS: frozenset[DeviceActionType] = frozenset({
    DeviceActionType.PING,
    DeviceActionType.HEALTH_CHECK,
    DeviceActionType.GET_STATUS,
    DeviceActionType.GET_INTERFACES,
    DeviceActionType.GET_FIRMWARE,
    DeviceActionType.PRTG_QUERY_SENSOR,
    DeviceActionType.PRTG_QUERY_DEVICE,
    DeviceActionType.PRTG_PAUSE_SENSOR,
    DeviceActionType.PRTG_RESUME_SENSOR,
    DeviceActionType.CISCO_QUERY_HEALTH,
    DeviceActionType.ARUBA_QUERY_HEALTH,
    DeviceActionType.CISCO_QUERY_PROCESSES,
})


# ---------------------------------------------------------------------------
# Execution shapes
# ---------------------------------------------------------------------------

@dataclass
class ActionTarget:
    """A fully-resolved execution target: the device row's identity plus
    its decrypted monitoring-source connection info (when linked). Built
    exclusively by the capability resolver - providers never query the DB
    themselves, so tenant isolation is enforced before any socket opens.
    """
    tenant_id: uuid.UUID
    device_id: uuid.UUID
    name: str
    hostname: str | None = None
    ip_address: str | None = None
    vendor: str | None = None
    model: str | None = None
    # Decrypted credentials for the device's linked monitoring source
    # (SNMP community/v3 keys etc.). None when the device has no source.
    connection: "MonitoringSourceConnectionInfo | None" = None
    # Free-form context the provider may need (e.g. interface name params
    # are validated per-action by the service layer, not here).
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def address(self) -> str | None:
        """Best network address for this target (ip wins over hostname)."""
        return self.ip_address or self.hostname


@dataclass
class ActionResult:
    """Uniform provider result - same contract shape as the ToolGateway
    adapters ({success, output, error}) so the SOP StepHandler bridge can
    wrap providers without translation."""
    success: bool
    output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    provider: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class ActionNotSupported(RuntimeError):
    """The provider cannot honestly execute this action (wrong transport,
    missing capability at the provider level, or a not-yet-implemented
    adapter). Raised instead of fabricating a result."""


# ---------------------------------------------------------------------------
# Provider abstraction + registry
# ---------------------------------------------------------------------------

class DeviceActionProvider(abc.ABC):
    """One transport/vendor execution adapter.

    Contract:
      * `provider_kind`   - registry key ("icmp", "snmp", "ssh", "rest",
                            "vendor:fortigate", ...).
      * `implemented`     - False for scaffolding providers that reserve
                            a kind but cannot execute yet (honest
                            capability reporting).
      * `supported_actions()` - the concrete actions this provider can
                            perform.
      * `execute()`       - perform the action. Must not raise for
                            expected device-level failures (unreachable,
                            timeout, auth rejected) - return
                            ActionResult(success=False, error=...).
                            May raise ActionNotSupported for actions
                            outside supported_actions().
    """

    provider_kind: ClassVar[str]
    display_name: ClassVar[str]
    implemented: ClassVar[bool] = True

    def supports(self, action: DeviceActionType) -> bool:
        return action in self.supported_actions()

    @abc.abstractmethod
    def supported_actions(self) -> frozenset[DeviceActionType]: ...

    @abc.abstractmethod
    async def execute(
        self,
        action: DeviceActionType,
        target: ActionTarget,
        params: dict[str, Any] | None = None,
    ) -> ActionResult: ...


class ActionProviderRegistry:
    """Same registry pattern as CollectorRegistry: providers self-register
    by kind; callers resolve by kind and never import a provider class
    directly. Adding vendor adapter #N means adding one new file."""

    _providers: dict[str, type[DeviceActionProvider]] = {}

    @classmethod
    def register(cls, provider_kind: str):
        def _decorator(provider_cls: type[DeviceActionProvider]):
            provider_cls.provider_kind = provider_kind
            cls._providers[provider_kind] = provider_cls
            return provider_cls
        return _decorator

    @classmethod
    def get(cls, provider_kind: str) -> type[DeviceActionProvider]:
        try:
            return cls._providers[provider_kind]
        except KeyError as exc:
            raise ValueError(
                f"No action provider registered for provider_kind={provider_kind!r}. "
                f"Supported: {sorted(cls._providers)}"
            ) from exc

    @classmethod
    def try_get(cls, provider_kind: str) -> type[DeviceActionProvider] | None:
        return cls._providers.get(provider_kind)

    @classmethod
    def list_kinds(cls) -> list[str]:
        return sorted(cls._providers)
