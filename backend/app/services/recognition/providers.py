"""Provider adapter contract + registry (universal discovery).

The core discovery engine never knows vendor names. It talks to this contract
only. Adding a vendor/provider therefore means adding an adapter + registering
it — no core edit.

Two adapter kinds, matching the two real deployment shapes:

  * ``NetworkAdapter``   — runs inside the customer network (ICMP/SNMP/SSH/
    WMI/WinRM/vendor REST).
  * ``CloudAdapter``     — talks to a cloud/controller API (Azure, AWS, GCP,
    VMware, Kubernetes) and may run centrally or customer-side.

Both produce the SAME normalized resource shape so downstream telemetry,
topology, alerting, AI and the Action Plane stay provider-agnostic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Iterable

# Capability tokens a resource can expose. The UI and the Action Plane consult
# these — an unsupported action is never offered.
CAP_ICMP = "icmp"
CAP_SNMP = "snmp"
CAP_SSH = "ssh"
CAP_API = "api"
CAP_WMI = "wmi"
CAP_WINRM = "winrm"
CAP_NETCONF = "netconf"
CAP_GNMI = "gnmi"
CAP_TELEMETRY = "telemetry"
CAP_TOPOLOGY = "topology"
CAP_CONFIGURATION = "configuration"
CAP_INTERFACES = "interface_control"
CAP_RESTART = "restart"

ALL_CAPABILITIES: tuple[str, ...] = (
    CAP_ICMP, CAP_SNMP, CAP_SSH, CAP_API, CAP_WMI, CAP_WINRM,
    CAP_NETCONF, CAP_GNMI, CAP_TELEMETRY, CAP_TOPOLOGY,
    CAP_CONFIGURATION, CAP_INTERFACES, CAP_RESTART,
)


@dataclass(frozen=True)
class RecognitionFingerprint:
    """Evidence that this adapter claims a target.

    ``provided`` names the evidence keys the adapter needs in hand. ``contains``
    optionally constrains a key's value (prefix/substring), which is what keeps a
    fingerprint precise — e.g. a Meraki claim requires a Meraki model code, not
    merely the presence of any ``model``.

    The registry only asks adapters whose required evidence is present, so a
    missing signal makes the adapter abstain instead of producing a false claim.
    """

    provided: tuple[str, ...] = ()
    #: (key, needle) substring constraints, ALL of which must hold
    contains: tuple[tuple[str, str], ...] = ()
    #: (key, prefixes) where AT LEAST ONE prefix must match (OR semantics)
    any_prefix: tuple[tuple[str, tuple[str, ...]], ...] = ()
    weight: float = 1.0
    note: str = ""

    def matches(self, evidence: dict[str, Any]) -> bool:
        for key in self.provided:
            if not evidence.get(key):
                return False
        for key, needle in self.contains:
            if needle.lower() not in str(evidence.get(key) or "").lower():
                return False
        for key, prefixes in self.any_prefix:
            value = str(evidence.get(key) or "").lower()
            if not any(value.startswith(p.lower()) for p in prefixes):
                return False
        return True



class ProviderAdapter:
    """Base contract. Subclasses declare identity, capabilities and fingerprints.

    Deliberately thin: nothing here performs I/O. Discovery orchestration,
    concurrency, retries and persistence stay in the existing collector/
    scheduler layer.
    """

    #: canonical provider token, e.g. "cisco", "fortinet", "azure", "aws"
    provider: ClassVar[str] = ""
    #: human label for the UI
    display_name: ClassVar[str] = ""
    #: "network" | "cloud"
    adapter_kind: ClassVar[str] = "network"
    #: capability tokens this provider can expose (subject to per-resource
    #: detection — never assumed for every resource)
    capabilities: ClassVar[frozenset[str]] = frozenset()
    #: recognition fingerprints this adapter can match on
    fingerprints: ClassVar[tuple[RecognitionFingerprint, ...]] = ()

    def candidate_types(self) -> tuple[str, ...]:
        """Canonical resource types this provider is known to expose."""
        return ()

    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Map a provider payload onto the normalized resource shape.

        MUST NOT raise for missing optional fields; unparsable values are
        omitted so the recogniser can report an honest partial result.
        """
        raise NotImplementedError

    # -- optional lifecycle hooks (no-ops by default) ----------------------

    async def validate_credentials(self, credential: dict[str, Any]) -> bool:
        return bool(credential)

    async def discover_accounts(self, credential: dict[str, Any]) -> list[dict[str, Any]]:
        return []

    async def discover_regions(self, credential: dict[str, Any]) -> list[str]:
        return []


class ProviderRegistry:
    """Name -> adapter class. The only place the core looks providers up."""

    _adapters: ClassVar[dict[str, type[ProviderAdapter]]] = {}

    @classmethod
    def register(cls, adapter_cls: type[ProviderAdapter]):
        if not adapter_cls.provider:
            raise ValueError("adapter must declare a provider token")
        cls._adapters[adapter_cls.provider] = adapter_cls
        return adapter_cls

    @classmethod
    def get(cls, provider: str) -> type[ProviderAdapter]:
        try:
            return cls._adapters[provider]
        except KeyError as exc:  # pragma: no cover - defensive
            raise KeyError(f"no adapter registered for provider={provider!r}") from exc

    @classmethod
    def try_get(cls, provider: str) -> type[ProviderAdapter] | None:
        return cls._adapters.get(provider)

    @classmethod
    def providers(cls) -> list[str]:
        return sorted(cls._adapters)

    @classmethod
    def by_kind(cls, kind: str) -> list[type[ProviderAdapter]]:
        return [a for _, a in sorted(cls._adapters.items()) if a.adapter_kind == kind]

    @classmethod
    def capable_of(cls, capability: str) -> list[type[ProviderAdapter]]:
        """Adapters that can expose ``capability`` — drives capability-driven UI."""
        return [
            a for _, a in sorted(cls._adapters.items())
            if capability in a.capabilities
        ]

    @classmethod
    def unregister_all(cls) -> None:
        """Test helper: reset the registry without touching module imports."""
        cls._adapters.clear()


def register(adapter_cls: type[ProviderAdapter]):
    """Decorator form: ``@register`` on an adapter class."""
    return ProviderRegistry.register(adapter_cls)


def all_capabilities() -> Iterable[str]:
    return ALL_CAPABILITIES
