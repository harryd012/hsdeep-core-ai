"""Discovery evidence contract: what a collector observed, before recognition.

This is the ONLY new contract the integration sprint needs. It carries raw
evidence from an existing collector to the recognition core and deliberately
performs no recognition of its own (no vendor logic, no classification) — that
lives in ``app.services.recognition``.

Rule: absent evidence stays absent. Every field is optional; a collector that
only achieved an ICMP echo populates ``address`` + ``transport`` and nothing
else, and the recognition core will honestly return ``unknown`` rather than a
guessed vendor/type.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

TRANSPORT_ICMP = "icmp"
TRANSPORT_TCP = "tcp"
TRANSPORT_SNMP = "snmp"
TRANSPORT_SSH = "ssh"
TRANSPORT_HTTP = "http"
TRANSPORT_API = "api"
TRANSPORT_AGENT = "agent"


@dataclass
class NeighborEvidence:
    """One observed adjacency, with the evidence that supports it.

    Only recorded when the collector actually observed it (LLDP/CDP neighbour,
    ARP entry, cloud parent reference). An unresolved neighbour produces NO
    topology edge — see DiscoveryService.
    """

    address: str
    evidence_type: str
    local_interface: str | None = None
    remote_interface: str | None = None
    relationship_type: str = "connected_to"
    confidence: float = 100.0


@dataclass
class DiscoveryEvidence:
    """Normalized evidence for ONE target, as observed by a collector."""

    address: str
    transport: str = TRANSPORT_ICMP
    provider: str | None = None
    source: str | None = None
    hostname: str | None = None
    sys_descr: str | None = None
    sys_object_id: str | None = None
    model: str | None = None
    serial: str | None = None
    mac: str | None = None
    vendor: str | None = None
    device_type: str | None = None
    provider_resource_id: str | None = None
    cloud_resource_id: str | None = None
    region: str | None = None
    availability_zone: str | None = None
    #: capability tokens the collector actually observed or the provider declared
    capabilities: list[str] = field(default_factory=list)
    neighbors: list[NeighborEvidence] = field(default_factory=list)
    reachable: bool | None = None
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    #: untouched provider payload for audit/debugging (never contains secrets)
    raw_evidence: dict[str, Any] = field(default_factory=dict)

    def recognition_input(self) -> dict[str, Any]:
        """Evidence dict for ``recognition.engine.recognize``.

        Only populated fields are emitted, so the recogniser can tell "no
        evidence" apart from "evidence says empty".
        """
        out: dict[str, Any] = {"ip": self.address}
        mapping = {
            "provider": self.provider,
            "model": self.model,
            "vendor": self.vendor,
            "hostname": self.hostname,
            "sys_descr": self.sys_descr,
            "sys_object_id": self.sys_object_id,
            "serial_number": self.serial,
            "mac": self.mac,
            "resource_type": self.device_type,
            "external_id": self.provider_resource_id,
            "region": self.region,
            "availability_zone": self.availability_zone,
        }
        for key, value in mapping.items():
            if value not in (None, ""):
                out[key] = value
        if self.capabilities:
            out["capabilities"] = list(self.capabilities)
        return out

    def merge(self, other: "DiscoveryEvidence") -> "DiscoveryEvidence":
        """Combine two observations of the same target.

        Used across polls: a later poll that lost a signal (e.g. SNMP went
        unreachable) must not erase evidence an earlier poll obtained. The
        newer non-empty value wins; otherwise the older value is preserved.
        """
        def pick(new: Any, old: Any) -> Any:
            return new if new not in (None, "", []) else old

        return DiscoveryEvidence(
            address=pick(other.address, self.address),
            transport=other.transport or self.transport,
            provider=pick(other.provider, self.provider),
            source=pick(other.source, self.source),
            hostname=pick(other.hostname, self.hostname),
            sys_descr=pick(other.sys_descr, self.sys_descr),
            sys_object_id=pick(other.sys_object_id, self.sys_object_id),
            model=pick(other.model, self.model),
            serial=pick(other.serial, self.serial),
            mac=pick(other.mac, self.mac),
            vendor=pick(other.vendor, self.vendor),
            device_type=pick(other.device_type, self.device_type),
            provider_resource_id=pick(
                other.provider_resource_id, self.provider_resource_id
            ),
            cloud_resource_id=pick(other.cloud_resource_id, self.cloud_resource_id),
            region=pick(other.region, self.region),
            availability_zone=pick(
                other.availability_zone, self.availability_zone
            ),
            capabilities=sorted(set(self.capabilities) | set(other.capabilities)),
            neighbors=other.neighbors or self.neighbors,
            reachable=(
                other.reachable if other.reachable is not None else self.reachable
            ),
            observed_at=other.observed_at,
            raw_evidence={**(self.raw_evidence or {}), **(other.raw_evidence or {})},
        )
