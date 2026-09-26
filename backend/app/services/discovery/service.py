"""Discovery integration service — the vertical slice that joins the
recognition core to the EXISTING HSDEEP persistence stack.

Flow (no parallel systems created):

    DiscoveryEvidence (collector)
        -> recognition.engine.recognize()      (existing recognition core)
        -> identity.enrich_identity()          (existing identity resolver)
        -> DeviceRepository.upsert()           (existing Device model + repo)
        -> DeviceCapabilityRepository.upsert() (existing capability store)
        -> ResourceRelationshipRepository.upsert() (existing topology edges)

Design rules enforced here:
  * Evidence regression is impossible: a later poll that lost a signal merges
    with the stored device instead of blanking known fields.
  * Idempotent: ``external_id`` is the strongest identity key, so re-discovery
    updates the same Device rather than inserting a duplicate.
  * Honest: capability flags that imply write privileges (config/reboot/
    firmware/backup/rollback) are NEVER derived from discovery evidence.
  * Topology only from real evidence: an unresolvable neighbour writes no edge.
  * No secrets: nothing from credentials is logged or persisted.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Device
from app.repositories.device_capability_repository import (
    CAPABILITY_FLAGS,
    DeviceCapabilityRepository,
)
from app.repositories.device_repository import DeviceRepository
from app.repositories.resource_relationship_repository import (
    ResourceRelationshipRepository,
)
from app.services.discovery.evidence import DiscoveryEvidence, NeighborEvidence
# Phase 7 vocabulary: identity stages, recognition status, evidence states. One
# shared source of truth with the resource/telemetry read models.
from app.services.infrastructure_intelligence import vocabulary as D
from app.services.infrastructure_intelligence import operational_state as O
from app.services.recognition import enrich_identity, recognize

logger = logging.getLogger(__name__)

# Provider-contract capability token -> existing DeviceCapability flag.
# Only these four have an existing flag; anything else is provider metadata.
_CAPABILITY_TO_FLAG: dict[str, str] = {
    "icmp": "supports_ping",
    "snmp": "supports_snmp",
    "ssh": "supports_ssh",
    "api": "supports_rest",
}

# Flags that must NEVER be set by discovery (they imply write privileges).
_PRIVILEGED_FLAGS: frozenset[str] = frozenset({
    "supports_config", "supports_reboot", "supports_firmware",
    "supports_backup", "supports_rollback", "supports_monitoring_actions",
})

UNKNOWN = "unknown"


@dataclass
class ResourceOutcome:
    """Per-resource result, suitable for an API response or an audit trail."""

    address: str
    resource_type: str
    resource_category: str
    device_id: str | None = None
    device_type: str = UNKNOWN
    vendor: str | None = None
    provider: str | None = None
    name: str | None = None
    external_id: str | None = None
    created: bool = False
    updated: bool = False
    identity_keys: list[str] = field(default_factory=list)
    identity_source: str | None = None
    identity_confidence: float = 0.0
    capabilities: dict[str, Any] = field(default_factory=dict)
    provider_capabilities: list[str] = field(default_factory=list)
    recognition: dict[str, Any] = field(default_factory=dict)
    relationships: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # -- Phase 7: evidence vs. canonical resource -------------------------
    #: What the probe actually observed. Valid evidence whether or not we
    #: recognised the target, and retained even when nothing was persisted.
    probe_evidence: dict[str, Any] = field(default_factory=dict)
    #: identified | unknown - recognition outcome, NOT a persistence flag.
    recognition_status: str = D.RECOGNITION_UNKNOWN
    #: Whether a canonical resource row exists for this evidence.
    persisted: bool = False
    #: Phase 7 identity stage reached by this evidence.
    identity_stage: str = D.STAGE_DISCOVERED
    #: The canonical resource this evidence was attached to (None when the
    #: evidence was not persisted - an unknown probe stays evidence-only).
    canonical_resource: dict[str, Any] | None = None
    #: active | stale | no_relationship_evidence for anything written here.
    relationship_evidence_state: str = D.NO_RELATIONSHIP_EVIDENCE

    def as_dict(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "resource_type": self.resource_type,
            "resource_category": self.resource_category,
            "device_id": self.device_id,
            "device_type": self.device_type,
            "vendor": self.vendor,
            "provider": self.provider,
            "name": self.name,
            "external_id": self.external_id,
            "created": self.created,
            "updated": self.updated,
            "identity_keys": list(self.identity_keys),
            "identity_source": self.identity_source,
            "identity_confidence": self.identity_confidence,
            "capabilities": dict(self.capabilities),
            "provider_capabilities": list(self.provider_capabilities),
            "recognition": dict(self.recognition),
            "relationships": list(self.relationships),
            "errors": list(self.errors),
            # -- evidence / resource separation --
            "probe_evidence": dict(self.probe_evidence),
            "recognition_status": self.recognition_status,
            "persisted": self.persisted,
            "identity_stage": self.identity_stage,
            "canonical_resource": (
                dict(self.canonical_resource) if self.canonical_resource else None
            ),
            "relationship_evidence_state": self.relationship_evidence_state,
        }


@dataclass
class DiscoveryResult:
    resources: list[ResourceOutcome] = field(default_factory=list)
    created: int = 0
    updated: int = 0
    unknown: int = 0
    relationships_written: int = 0
    unresolved_neighbors: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.errors and not self.resources:
            return "FAILED"
        if self.errors:
            return "PARTIAL"
        return "SUCCESS"

    #: Phase 7: evidence that produced no canonical resource.
    @property
    def evidence_only(self) -> int:
        return sum(1 for outcome in self.resources if not outcome.persisted)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "discovered": len(self.resources),
            "created": self.created,
            "updated": self.updated,
            "unknown": self.unknown,
            "relationships_written": self.relationships_written,
            "unresolved_neighbors": self.unresolved_neighbors,
            "resources": [r.as_dict() for r in self.resources],
            "errors": list(self.errors),
            # -- evidence vs. canonical resource (Phase 7) --
            "persisted": sum(1 for outcome in self.resources if outcome.persisted),
            "evidence_only": self.evidence_only,
            "recognized": sum(
                1
                for outcome in self.resources
                if outcome.recognition_status == D.RECOGNITION_IDENTIFIED
            ),
            "with_topology_evidence": sum(
                1
                for outcome in self.resources
                if outcome.relationship_evidence_state == D.RELATIONSHIP_ACTIVE
            ),
            "no_topology_evidence": sum(
                1
                for outcome in self.resources
                if outcome.relationship_evidence_state == D.NO_RELATIONSHIP_EVIDENCE
            ),
        }


class DiscoveryService:
    """Feeds collector evidence through recognition into the existing stack."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.devices = DeviceRepository(db)
        self.capabilities = DeviceCapabilityRepository(db)
        self.relationships = ResourceRelationshipRepository(db)

    async def discover(
        self,
        *,
        tenant_id: uuid.UUID,
        monitoring_source_id: uuid.UUID,
        evidence: list[DiscoveryEvidence],
        discovered_by: str = "collector",
        site_id: uuid.UUID | None = None,
    ) -> DiscoveryResult:
        """Recognise and persist one batch of collector evidence.

        Idempotent: re-running the same evidence updates the same Devices.
        The caller owns the commit (same convention as every other service).
        """
        result = DiscoveryResult()
        # Address -> Device.id for THIS batch, so neighbour evidence resolves
        # even for a resource discovered in the same run.
        batch_index: dict[str, uuid.UUID] = {}

        for ev in evidence:
            try:
                outcome = await self._persist_one(
                    tenant_id=tenant_id,
                    monitoring_source_id=monitoring_source_id,
                    ev=ev,
                    site_id=site_id,
                )
            except Exception as exc:  # noqa: BLE001 - one bad target never aborts a batch
                logger.warning(
                    "discovery failed for %s (%s): %s", ev.address, ev.transport, exc,
                    exc_info=True,
                )
                result.errors.append(f"{ev.address}: {type(exc).__name__}: {exc}")
                continue

            result.resources.append(outcome)
            if outcome.created:
                result.created += 1
            elif outcome.updated:
                result.updated += 1
            if outcome.resource_type == UNKNOWN:
                result.unknown += 1
            if outcome.device_id:
                batch_index[ev.address] = uuid.UUID(outcome.device_id)

        # Relationship evidence is a second pass so both endpoints exist.
        for ev in evidence:
            source_id = batch_index.get(ev.address)
            if source_id is None or not ev.neighbors:
                continue
            written, unresolved = await self._write_neighbors(
                tenant_id=tenant_id,
                monitoring_source_id=monitoring_source_id,
                source_id=source_id,
                neighbors=ev.neighbors,
                batch_index=batch_index,
            )
            result.relationships_written += written
            result.unresolved_neighbors += unresolved
            # Record the evidence state on the owning outcome, so the response
            # distinguishes "adjacency evidence recorded" from "none observed".
            for outcome in result.resources:
                if outcome.device_id == str(source_id):
                    outcome.relationship_evidence_state = (
                        D.RELATIONSHIP_ACTIVE
                        if written
                        else D.NO_RELATIONSHIP_EVIDENCE
                    )
                    outcome.relationships = [
                        {
                            "evidence_type": nb.evidence_type,
                            "neighbour_address": nb.address,
                            "local_interface": nb.local_interface,
                            "remote_interface": nb.remote_interface,
                            "relationship_type": nb.relationship_type,
                            "confidence": nb.confidence,
                        }
                        for nb in ev.neighbors
                    ]
                    break

        return result

    async def _persist_one(
        self,
        *,
        tenant_id: uuid.UUID,
        monitoring_source_id: uuid.UUID,
        ev: DiscoveryEvidence,
        site_id: uuid.UUID | None,
    ) -> ResourceOutcome:
        recognized = enrich_identity(recognize(ev.recognition_input()).as_dict())

        resource_type = str(recognized.get("resource_type") or UNKNOWN)
        resource_category = str(recognized.get("resource_category") or UNKNOWN)
        identity_keys = list(recognized.get("identity_keys") or [])
        external_id = identity_keys[0] if identity_keys else f"address:{ev.address}"

        outcome = ResourceOutcome(
            address=ev.address,
            resource_type=resource_type,
            resource_category=resource_category,
            name=recognized.get("name"),
            vendor=recognized.get("vendor"),
            provider=recognized.get("provider"),
            external_id=external_id,
            identity_keys=identity_keys,
            identity_source=recognized.get("identity_source"),
            identity_confidence=float(recognized.get("identity_confidence") or 0.0),
            recognition=dict(recognized.get("recognition") or {}),
        )

        # -- resolve the existing resource by EVIDENCE, not just one key -----
        # A poll that loses its strongest signal (e.g. SNMP gone, so no serial)
        # derives a different top identity key. Matching only on that key would
        # create a duplicate, so match on the whole identity set plus address.
        existing = await self._find_existing_device(
            tenant_id=tenant_id,
            monitoring_source_id=monitoring_source_id,
            address=ev.address,
            identity_keys=identity_keys,
        )
        # Keep the already-established identity so re-discovery cannot move it.
        if existing is not None and existing.external_id:
            external_id = existing.external_id
            outcome.external_id = external_id
        merged = _merge_with_existing(recognized, existing)

        status = "up" if ev.reachable is not False else "down"
        if ev.reachable is None:
            status = existing.status if existing is not None else "unknown"

        device, created = await self.devices.upsert(
            tenant_id=tenant_id,
            monitoring_source_id=monitoring_source_id,
            external_id=external_id,
            name=merged["name"],
            device_type=merged["device_type"],
            vendor=merged["vendor"],
            model=merged["model"],
            os_version=merged["os_version"],
            hostname=merged["hostname"],
            ip_address=merged["ip_address"],
            mac_address=merged["mac_address"],
            status=status,
            site_id=site_id if site_id is not None else (
                existing.site_id if existing is not None else None
            ),
            serial_number=merged["serial_number"],
        )
        outcome.device_id = str(device.id)
        outcome.created = created
        outcome.updated = not created
        outcome.device_type = merged["device_type"]

        # -- capabilities (existing DeviceCapability store) -----------------
        flags, provider_caps = _capability_flags(recognized, ev)
        outcome.capabilities = flags
        outcome.provider_capabilities = provider_caps
        if any(v is not None for v in flags.values()):
            await self.capabilities.upsert(
                tenant_id=tenant_id,
                device_id=device.id,
                source="discovery",
                capabilities=flags,
                management_address=merged["ip_address"],
            )

        # -- provider metadata (capabilities with no existing flag included)
        _store_provider_metadata(device, recognized, ev, provider_caps)

        # -- Phase 7: separate EVIDENCE from the CANONICAL RESOURCE ----------
        # A probe that answered but was not recognised is valid evidence and is
        # reported as such; it is not presented as an identified resource.
        outcome.probe_evidence = _probe_evidence(ev)
        outcome.recognition_status = (
            D.RECOGNITION_IDENTIFIED if outcome.resource_type != UNKNOWN
            else D.RECOGNITION_UNKNOWN
        )
        outcome.persisted = True
        outcome.identity_stage = (
            D.STAGE_IDENTIFIED if outcome.recognition_status == D.RECOGNITION_UNKNOWN
            else D.STAGE_CLASSIFIED
        )
        outcome.canonical_resource = {
            "device_id": str(device.id),
            "created": created,
            "updated": not created,
            "external_id": external_id,
            "identity_source": outcome.identity_source,
            "identity_confidence": outcome.identity_confidence,
            "identity_keys": list(outcome.identity_keys),
            "lifecycle_state": getattr(device, "lifecycle_state", None),
            "provider_resource_id": getattr(device, "provider_resource_id", None),
        }
        if outcome.identity_stage == D.STAGE_IDENTIFIED:
            outcome.probe_evidence["note"] = (
                "identity established but the resource type is unknown: this "
                "device is valid discovery evidence, not a classified resource"
            )

        logger.info(
            "discovery: %s -> device=%s type=%s category=%s provider=%s "
            "method=%s identity=%s %s",
            ev.address, device.id, resource_type, resource_category,
            recognized.get("provider"), outcome.recognition.get("method"),
            outcome.identity_source, "created" if created else "updated",
        )
        return outcome

    async def _write_neighbors(
        self,
        *,
        tenant_id: uuid.UUID,
        monitoring_source_id: uuid.UUID,
        source_id: uuid.UUID,
        neighbors: list[NeighborEvidence],
        batch_index: dict[str, uuid.UUID],
    ) -> tuple[int, int]:
        """Write edges ONLY for neighbours that resolve to a real resource.

        Phase 7 hardening: every edge records WHICH source produced it, WHEN, and
        with what local/remote interface, in a per-source evidence ledger. A later
        LLDP-only poll therefore cannot erase the ARP evidence an earlier poll
        obtained, and "stale evidence" stays distinguishable from "current
        evidence" without re-probing anything.
        """
        written = 0
        unresolved = 0
        observed_at = datetime.now(timezone.utc)
        for nb in neighbors:
            target_id = batch_index.get(nb.address) or await self._device_id_for_address(
                tenant_id, monitoring_source_id, nb.address
            )
            if target_id is None:
                # No evidence-backed endpoint -> no edge. Reported, not faked.
                unresolved += 1
                logger.info(
                    "discovery: neighbour %s unresolved (evidence=%s) - no edge written",
                    nb.address, nb.evidence_type,
                )
                continue
            if target_id == source_id:
                continue
            existing, _created = await self.relationships.upsert(
                tenant_id,
                source_resource_id=source_id,
                target_resource_id=target_id,
                relationship_type=nb.relationship_type,
                relationship_subtype=nb.evidence_type,
                label=nb.local_interface or nb.remote_interface,
                confidence=nb.confidence,
                discovered_by=nb.evidence_type,
                status=D.RELATIONSHIP_ACTIVE,
                attributes={
                    "local_interface": nb.local_interface,
                    "remote_interface": nb.remote_interface,
                    "evidence_type": nb.evidence_type,
                },
            )
            # Merge (never replace) the per-source ledger so multi-source
            # evidence accrues on one edge instead of overwriting itself.
            await self.relationships.update(
                existing,
                attributes=O.merge_relationship_evidence(
                    existing.attributes,
                    source=nb.evidence_type,
                    observed_at=observed_at,
                    payload={
                        "local_interface": nb.local_interface,
                        "remote_interface": nb.remote_interface,
                        "neighbour_address": nb.address,
                        "confidence": nb.confidence,
                    },
                ),
                status=D.RELATIONSHIP_ACTIVE,
            )
            written += 1
        return written, unresolved

    async def write_observed_neighbors(
        self,
        *,
        tenant_id: uuid.UUID,
        monitoring_source_id: uuid.UUID,
        source_device_id: uuid.UUID,
        neighbors: list[NeighborEvidence],
    ) -> tuple[int, int]:
        """Persist edges for neighbours of an ALREADY-RESOLVED local device.

        Public entry point for the collector path (BundleSaver), which has just
        upserted the local Device and so does not need `discover()` to recognise
        it again. Delegates to the same `_write_neighbors` the probe path uses,
        so exactly one method decides whether an adjacency is evidence-backed.

        Never creates a Device: a neighbour that does not resolve to an existing
        device in this tenant is counted as unresolved and produces NO edge.

        Returns (written, unresolved).
        """
        return await self._write_neighbors(
            tenant_id=tenant_id,
            monitoring_source_id=monitoring_source_id,
            source_id=source_device_id,
            neighbors=neighbors,
            batch_index={},
        )

    async def _device_id_for_address(
        self, tenant_id: uuid.UUID, monitoring_source_id: uuid.UUID, address: str
    ) -> uuid.UUID | None:
        stmt = select(Device.id).where(
            Device.tenant_id == tenant_id,
            Device.monitoring_source_id == monitoring_source_id,
            Device.ip_address == address,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def _find_existing_device(
        self,
        *,
        tenant_id: uuid.UUID,
        monitoring_source_id: uuid.UUID,
        address: str,
        identity_keys: list[str],
    ) -> Device | None:
        """Resolve an already-known resource from the CURRENT evidence.

        Deliberately broader than a single-key lookup: it matches on any of the
        resource's identity keys AND on the management address. This is what
        makes re-discovery idempotent even when the strongest signal is lost
        between polls (serial yesterday, hostname today) — the case that would
        otherwise fork the resource into a duplicate.
        """
        if not identity_keys and not address:
            return None

        candidates = [k for k in identity_keys if k]
        stmt = select(Device).where(
            Device.tenant_id == tenant_id,
            Device.monitoring_source_id == monitoring_source_id,
            or_(
                Device.external_id.in_(candidates) if candidates else false(),
                Device.ip_address == address if address else false(),
            ),
        )
        rows = list((await self.db.execute(stmt)).scalars().all())
        if not rows:
            return None
        if len(rows) == 1:
            return rows[0]

        # Deterministic tie-break: an exact identity-key match wins over an
        # address match, then the oldest row (the established record).
        key_set = set(candidates)
        rows.sort(key=lambda d: (d.external_id not in key_set, d.created_at))
        return rows[0]


# ---------------------------------------------------------------------------
# Module-level helpers (pure - unit-testable without a database)
# ---------------------------------------------------------------------------


def _merge_with_existing(
    recognized: dict[str, Any], existing: Device | None
) -> dict[str, Any]:
    """New evidence wins; otherwise the previously stored value is preserved.

    Without this, a poll that could not reach SNMP would blank the vendor,
    model and serial learned on an earlier poll — and, because idempotency is
    keyed on identity evidence, could even change the resource's identity.
    """
    def pick(new: Any, stored: Any) -> Any:
        return new if new not in (None, "") else stored

    def stored(attr: str) -> Any:
        return getattr(existing, attr, None) if existing is not None else None

    name = (
        recognized.get("name")
        or recognized.get("hostname")
        or stored("name")
        or recognized.get("management_ip")
    )
    device_type = (
        pick(recognized.get("resource_type"), stored("device_type")) or UNKNOWN
    )
    return {
        "name": name or UNKNOWN,
        "device_type": device_type,
        "vendor": pick(recognized.get("vendor"), stored("vendor")),
        "model": pick(recognized.get("model"), stored("model")),
        "os_version": pick(recognized.get("version"), stored("os_version")),
        "hostname": pick(recognized.get("hostname"), stored("hostname")),
        "ip_address": pick(
            recognized.get("management_ip"), stored("ip_address")
        ),
        "mac_address": pick(recognized.get("mac_address"), stored("mac_address")),
        "serial_number": pick(
            recognized.get("serial_number"), stored("serial_number")
        ),
    }


def _probe_evidence(ev: DiscoveryEvidence) -> dict[str, Any]:
    """The observable facts behind one discovery result.

    Kept EXACTLY as observed - no inference, no defaults, and no secrets. This is
    what makes an "unknown" probe defensible: the evidence is retained even when
    recognition (and therefore a canonical resource) is impossible.
    """
    authentication = ev.raw_evidence.get("authentication") if isinstance(
        ev.raw_evidence, dict
    ) else None
    return {
        "address": ev.address,
        "transport": ev.transport,
        "source": ev.source,
        "provider": ev.provider,
        "reachable": ev.reachable,
        "hostname": ev.hostname,
        "sys_descr": ev.sys_descr,
        "sys_object_id": ev.sys_object_id,
        "model": ev.model,
        "vendor": ev.vendor,
        "serial": ev.serial,
        "mac": ev.mac,
        "device_type": ev.device_type,
        "provider_resource_id": ev.provider_resource_id,
        "cloud_resource_id": ev.cloud_resource_id,
        "region": ev.region,
        "availability_zone": ev.availability_zone,
        "capabilities": list(ev.capabilities),
        "neighbor_count": len(ev.neighbors),
        "observed_at": ev.observed_at.isoformat() if ev.observed_at else None,
        # Authentication outcome is reported as observed, never inferred.
        "authentication": authentication or "not_attempted",
        "raw_evidence_keys": sorted(ev.raw_evidence) if isinstance(
            ev.raw_evidence, dict
        ) else [],
    }


def _capability_flags(
    recognized: dict[str, Any], ev: DiscoveryEvidence
) -> tuple[dict[str, Any], list[str]]:
    """Map observed/provider-declared tokens onto the EXISTING capability flags.

    Tokens with no existing flag are returned separately as provider capability
    metadata rather than being dropped or forced into an unrelated flag.
    Write-implying flags are never derived from discovery evidence.
    """
    declared = set(recognized.get("capabilities") or []) | set(ev.capabilities)
    if ev.reachable is True:
        declared.add("icmp")
    elif ev.transport:
        declared.add(ev.transport)

    flags: dict[str, Any] = {flag: None for flag in _CAPABILITY_TO_FLAG.values()}
    provider_caps: list[str] = []
    for token in sorted(declared):
        flag = _CAPABILITY_TO_FLAG.get(token)
        if flag in _PRIVILEGED_FLAGS:  # defensive: never derive write rights
            continue
        if flag:
            flags[flag] = True
        else:
            provider_caps.append(token)
    return flags, provider_caps


def _store_provider_metadata(
    device: Device,
    recognized: dict[str, Any],
    ev: DiscoveryEvidence,
    provider_caps: list[str],
) -> None:
    """Provider-specific facts ride the existing custom_metadata column.

    No parallel metadata table is created. Nothing secret is written: only the
    evidence fields the collector already normalised.
    """
    metadata = dict(device.custom_metadata or {})
    previous = (metadata.get("discovery") or {}).get("identity_keys") or []
    metadata["discovery"] = {
        "source": ev.source or ev.provider or ev.transport or "collector",
        "transport": ev.transport,
        "provider": recognized.get("provider"),
        "resource_category": recognized.get("resource_category"),
        "provider_capabilities": provider_caps,
        "identity_source": recognized.get("identity_source"),
        "identity_confidence": recognized.get("identity_confidence"),
        # Union of every identity key ever observed, so a later poll that lost
        # its strongest signal can still match this resource.
        "identity_keys": sorted(set(previous) | set(recognized.get("identity_keys") or [])),
        "recognition": recognized.get("recognition"),
        "observed_at": ev.observed_at.isoformat(),
    }
    device.custom_metadata = metadata


