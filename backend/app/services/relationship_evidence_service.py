"""
Relationship evidence materialisation (Phase 9.6 — passive increment).

Turns ALREADY-RECORDED, ALREADY-PERSISTED structural evidence into
``resource_relationships`` rows. It performs **no probing, no scanning and no
inference**: every edge it writes corresponds to a column or row the platform
already holds, and an endpoint that does not resolve to a real device in the same
tenant produces NO edge.

Sources implemented
-------------------
1. ``device_containment`` — ``devices.parent_resource_id`` -> ``contains``
   (parent -> child). Recorded structure, not an observation.
2. ``bgp_peering`` — ``bgp_neighbors`` -> ``peers_with`` (subtype ``bgp``).
   The peer address is resolved against the addresses already recorded on this
   tenant's devices; an address that matches no device is counted as unresolved
   rather than guessed.

Sources deliberately NOT implemented, with the reason reported at runtime
-----------------------------------------------------------------------
* ``devices.monitoring_source_id`` (a *monitored_by* binding) and
  ``devices.site_id``/``zone_id`` membership: the other endpoint is a monitoring
  source, site or zone, and ``resource_relationships`` endpoints are foreign keys
  to ``devices``. Converting them is impossible without a schema change, so it is
  not attempted.
* Interface-level adjacency: an ``interfaces`` row records no peer, and no
  neighbour-evidence store (LLDP/CDP/ARP/MAC) is persisted anywhere in the
  platform, so there is nothing to convert. This is reported, never faked.

Reuse, not duplication
----------------------
Writes go through ``ResourceRelationshipRepository.upsert`` — the same method
``DiscoveryService._write_neighbors`` uses — and the per-source evidence ledger is
merged with ``operational_state.merge_relationship_evidence``, so multi-source
evidence accrues on one edge instead of overwriting itself. This module is a new
EVIDENCE SOURCE into the existing relationship store, not a second relationship
engine: it invents no relationship type (both types used are in the canonical
``RELATIONSHIP_TYPES``), no confidence and no endpoint.

Query budget: a constant number of round trips per run (device list, BGP list),
independent of how many devices the tenant has.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.bgp_neighbor_repository import BgpNeighborRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.resource_relationship_repository import (
    ResourceRelationshipRepository,
)
from app.services.infrastructure_intelligence import operational_state as O
from app.services.infrastructure_intelligence import vocabulary as V

logger = logging.getLogger(__name__)

#: Bounded fetch, matching the other topology services.
MAX_FETCH_LIMIT = 100_000

#: Relationship status vocabulary is `active | degraded | failed | unknown`
#: (documented on the model and validated by schemas.topology). A recorded BGP
#: session that is not established is NOT reported as failed - we know it is not
#: established, we do not know it failed - so it stays `unknown`.
_STATUS_UNKNOWN = "unknown"

#: The recorded BGP state that means "this session is up".
_BGP_ESTABLISHED = "established"

# -- Phase 9.10: canonical reconciliation vocabulary ------------------------
#: Freshness words for recorded evidence. The THRESHOLD is not re-implemented:
#: V.TELEMETRY_STALE_AFTER_SECONDS is the project's existing staleness constant,
#: and "stale" reuses V.COLLECTION_STALE so the vocabulary stays one word set.
FRESHNESS_FRESH = "fresh"
FRESHNESS_STALE = V.COLLECTION_STALE
FRESHNESS_UNKNOWN = "unknown"

#: Resolution of one piece of evidence to canonical resources.
RESOLUTION_RESOLVED = "resolved"
RESOLUTION_UNRESOLVED = "unresolved"
RESOLUTION_AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class SourceReport:
    """What one evidence source contributed (or could not contribute)."""

    source: str
    #: Recorded evidence items considered.
    candidates: int = 0
    #: Edges that would be written (candidates minus unresolved and self-edges).
    planned: int = 0
    #: Edges created in this run.
    written: int = 0
    #: Edges that already existed and were re-observed (ledger + last_seen).
    refreshed: int = 0
    #: Evidence whose other endpoint is not a device in this tenant.
    unresolved: int = 0
    #: Self-edges (an endpoint referencing itself) - skipped, never written.
    skipped: int = 0
    #: Why this source produced nothing, when it produced nothing.
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "candidates": self.candidates,
            "planned": self.planned,
            "written": self.written,
            "refreshed": self.refreshed,
            "unresolved": self.unresolved,
            "skipped": self.skipped,
            "note": self.note,
        }


class RelationshipEvidenceService:
    """Materialises recorded evidence into relationship rows. Never probes."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.devices = DeviceRepository(db)
        self.bgp = BgpNeighborRepository(db)
        self.relationships = ResourceRelationshipRepository(db)

    @staticmethod
    def _address_counts(devices: Sequence[Any]) -> dict[str, int]:
        """How many devices in this tenant record each address.

        A count above one is the definition of an AMBIGUOUS identity key: the
        resolver's deterministic order still picks one device, but the evidence
        must be reported as ambiguous instead of looking settled.
        """
        counts: dict[str, int] = {}
        for device in devices:
            address = (device.ip_address or "").strip()
            if address:
                counts[address] = counts.get(address, 0) + 1
        return counts

    @classmethod
    def _evidence_address(cls, row: Any) -> str:
        """The address the evidence carried, when it recorded one."""
        attributes = row.attributes or {}
        for key in ("remote_address", "address", "peer_address"):
            value = str(attributes.get(key) or "").strip()
            if value:
                return value
        return ""

    @staticmethod
    def _freshness(row: Any, now: datetime) -> str:
        """fresh / stale / unknown for one recorded edge, using the project
        threshold. A stale edge never becomes fresh by being reconciled."""
        last_seen = getattr(row, "last_seen", None)
        if last_seen is None:
            return FRESHNESS_UNKNOWN
        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)
        age = (now - last_seen).total_seconds()
        if age <= V.TELEMETRY_STALE_AFTER_SECONDS:
            return FRESHNESS_FRESH
        return FRESHNESS_STALE

    @staticmethod
    def _address_index(devices: Sequence[Any]) -> dict[str, uuid.UUID]:
        """Recorded device address -> device id.

        Only addresses the platform already recorded on a device row are used.
        The first device wins a duplicated address (sorted by the caller's
        deterministic list order), so resolution never depends on dict luck.
        """
        index: dict[str, uuid.UUID] = {}
        for device in devices:
            address = (device.ip_address or "").strip()
            if address and address not in index:
                index[address] = device.id
        return index

    async def _record_edge(
        self,
        tenant_id: uuid.UUID,
        *,
        source_id: uuid.UUID,
        target_id: uuid.UUID,
        relationship_type: str,
        relationship_subtype: str | None,
        discovered_by: str,
        status: str,
        attributes: dict[str, Any],
        label: str | None,
        observed_at: datetime,
    ) -> bool:
        """Upsert one evidence-backed edge and accrue its evidence ledger.

        Confidence is 100.0 for every materialised edge because the binding is
        RECORDED, not estimated. Returns True when this run created the edge.
        """
        existing, created = await self.relationships.upsert(
            tenant_id,
            source_resource_id=source_id,
            target_resource_id=target_id,
            relationship_type=relationship_type,
            relationship_subtype=relationship_subtype,
            label=label,
            confidence=100.0,
            discovered_by=discovered_by,
            status=status,
            attributes=attributes,
        )
        # Merge (never replace) the ledger: another source's evidence for the
        # same edge must survive, and re-observing must not duplicate it.
        await self.relationships.update(
            existing,
            attributes=O.merge_relationship_evidence(
                existing.attributes,
                source=discovered_by,
                observed_at=observed_at,
                payload=attributes,
            ),
            status=status,
        )
        return created

    async def _materialize_containment(
        self,
        tenant_id: uuid.UUID,
        devices: Sequence[Any],
        by_id: dict[uuid.UUID, Any],
        *,
        observed_at: datetime,
        dry_run: bool,
    ) -> SourceReport:
        """``devices.parent_resource_id`` -> ``contains`` (parent -> child).

        A parent outside this tenant, or one that has been soft-deleted, is
        counted as unresolved rather than resurrected into a node the tenant
        cannot see.
        """
        children = [device for device in devices if device.parent_resource_id]
        written = refreshed = unresolved = skipped = planned = 0

        for child in children:
            parent = by_id.get(child.parent_resource_id)
            if parent is None:
                unresolved += 1
                continue
            if parent.id == child.id:
                skipped += 1
                continue
            planned += 1
            if dry_run:
                continue
            created = await self._record_edge(
                tenant_id,
                source_id=parent.id,
                target_id=child.id,
                relationship_type=V.REL_CONTAINS,
                relationship_subtype=None,
                discovered_by=V.EVIDENCE_SOURCE_INVENTORY,
                status=V.RELATIONSHIP_ACTIVE,
                attributes={
                    "evidence_type": V.EVIDENCE_SOURCE_INVENTORY,
                    "recorded_field": "devices.parent_resource_id",
                },
                label=child.name,
                observed_at=observed_at,
            )
            written += 1 if created else 0
            refreshed += 0 if created else 1

        note = None
        if not children:
            note = (
                "no device in this tenant records a parent_resource_id, so there "
                "is no recorded containment to convert"
            )
        return SourceReport(
            source="device_containment",
            candidates=len(children),
            planned=planned,
            written=written,
            refreshed=refreshed,
            unresolved=unresolved,
            skipped=skipped,
            note=note,
        )

    async def _materialize_bgp(
        self,
        tenant_id: uuid.UUID,
        devices: Sequence[Any],
        by_id: dict[uuid.UUID, Any],
        *,
        observed_at: datetime,
        dry_run: bool,
    ) -> SourceReport:
        """Recorded BGP sessions -> ``peers_with`` (subtype ``bgp``).

        The local end is ``bgp_neighbors.resource_id`` (the device that owns the
        session). The remote end is resolved from ``neighbor_ip`` against the
        addresses already recorded on the tenant's devices — never from a
        hostname, a subnet or a name.
        """
        rows = await self.bgp.list_for_tenant(tenant_id, limit=MAX_FETCH_LIMIT)
        addresses = self._address_index(devices)
        written = refreshed = unresolved = skipped = planned = 0

        for row in rows:
            local = by_id.get(row.resource_id)
            if local is None:
                unresolved += 1
                continue
            peer_id = addresses.get((row.neighbor_ip or "").strip())
            if peer_id is None:
                unresolved += 1
                continue
            if peer_id == local.id:
                skipped += 1
                continue
            planned += 1
            if dry_run:
                continue

            status = (
                V.RELATIONSHIP_ACTIVE
                if (row.state or "").strip().lower() == _BGP_ESTABLISHED
                else _STATUS_UNKNOWN
            )
            attributes = {
                key: value
                for key, value in {
                    "evidence_type": V.EVIDENCE_SOURCE_BGP,
                    "recorded_field": "bgp_neighbors.neighbor_ip",
                    "vrf": row.vrf,
                    "local_ip": row.local_ip,
                    "remote_as": row.remote_as,
                    "local_as": row.local_as,
                    "state": row.state,
                    "admin_status": row.admin_status,
                    "provider": row.provider,
                    "prefixes_received": row.prefixes_received,
                    "prefixes_advertised": row.prefixes_advertised,
                }.items()
                if value not in (None, "")
            }
            created = await self._record_edge(
                tenant_id,
                source_id=local.id,
                target_id=peer_id,
                relationship_type=V.REL_PEERS_WITH,
                relationship_subtype="bgp",
                discovered_by=V.EVIDENCE_SOURCE_BGP,
                status=status,
                attributes=attributes,
                label=row.description,
                observed_at=observed_at,
            )
            written += 1 if created else 0
            refreshed += 0 if created else 1

        note = None
        if not rows:
            note = (
                "bgp_neighbors holds no row for this tenant, so no peering "
                "evidence exists to convert"
            )
        return SourceReport(
            source="bgp_peering",
            candidates=len(rows),
            planned=planned,
            written=written,
            refreshed=refreshed,
            unresolved=unresolved,
            skipped=skipped,
            note=note,
        )

    async def materialize(
        self,
        tenant_id: uuid.UUID,
        *,
        dry_run: bool = False,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Convert this tenant's recorded evidence into relationship rows.

        ``dry_run`` counts what WOULD be written and writes nothing. Both paths
        read the same evidence; only the writing differs.
        """
        observed_at = now or datetime.now(timezone.utc)
        devices = await self.devices.list(tenant_id, limit=MAX_FETCH_LIMIT)
        by_id = {device.id: device for device in devices}

        reports = [
            await self._materialize_containment(
                tenant_id, devices, by_id, observed_at=observed_at, dry_run=dry_run
            ),
            await self._materialize_bgp(
                tenant_id, devices, by_id, observed_at=observed_at, dry_run=dry_run
            ),
        ]

        unresolved_total = sum(report.unresolved for report in reports)
        limitations: list[str] = []
        if not any(report.candidates for report in reports):
            limitations.append(
                "No recorded adjacency evidence exists for this tenant: neither "
                "devices.parent_resource_id nor bgp_neighbors holds a row."
            )
        limitations.append(
            "Relationship endpoints are foreign keys to devices, so a monitoring "
            "binding (devices.monitoring_source_id) or a site/zone membership "
            "cannot be materialised as an edge."
        )
        if unresolved_total:
            limitations.append(
                f"{unresolved_total} recorded endpoint(s) do not resolve to a "
                "device in this tenant and were skipped rather than guessed."
            )

        return {
            "tenant_id": str(tenant_id),
            "generated_at": observed_at.isoformat(),
            "dry_run": dry_run,
            "devices_total": len(devices),
            "candidates_total": sum(report.candidates for report in reports),
            "planned_total": sum(report.planned for report in reports),
            "written_total": sum(report.written for report in reports),
            "refreshed_total": sum(report.refreshed for report in reports),
            "unresolved_total": unresolved_total,
            "skipped_total": sum(report.skipped for report in reports),
            "sources": [report.to_dict() for report in reports],
            "limitations": limitations,
            # Phase 9.10: the canonical view of what is ALREADY recorded. Purely
            # additive - every key above is unchanged for existing callers.
            "canonical": await self.reconcile_canonical(
                tenant_id, devices=devices, dry_run=dry_run, now=observed_at
            ),
        }

    async def reconcile_canonical(
        self,
        tenant_id: uuid.UUID,
        *,
        devices: Sequence[Any] | None = None,
        dry_run: bool = False,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Reconcile ALREADY-RECORDED relationships into one canonical graph.

        Deterministic and idempotent: it reads this tenant's relationship rows,
        groups the directed rows that describe one physical relationship, merges
        their provenance, classifies freshness, detects conflicts between sources,
        and annotates each row with that canonical view. Annotating never changes
        an endpoint, a type, a confidence or an inferred flag, so running it twice
        against unchanged evidence reports ``unchanged``.

        Nothing is guessed: a row whose endpoint is no longer a device in this
        tenant is UNRESOLVED evidence (reported, not rewritten or deleted), and
        AMBIGUOUS means the identity key the evidence used (an address) is
        recorded on more than one device in the tenant.
        """
        observed_at = now or datetime.now(timezone.utc)
        if devices is None:
            devices = await self.devices.list(tenant_id, limit=MAX_FETCH_LIMIT)
        device_ids = {device.id for device in devices}

        rows = await self.relationships.list(tenant_id, limit=MAX_FETCH_LIMIT)
        stats: dict[str, Any] = {
            "tenant_id": str(tenant_id),
            "generated_at": observed_at.isoformat(),
            "dry_run": dry_run,
            "evidence_records_seen": len(rows),
            "resolved": 0,
            "unresolved": 0,
            "ambiguous": 0,
            "canonical_relationships": 0,
            "relationships_created": 0,
            "relationships_updated": 0,
            "relationships_unchanged": 0,
            "conflicts": 0,
            "stale": 0,
            "skipped": 0,
            "errors": 0,
            "conflict_samples": [],
            "limitations": [],
        }
        if not rows:
            stats["limitations"] = [
                "No relationship rows exist for this tenant, so there is no recorded "
                "evidence to reconcile (nothing is inferred to fill the gap)."
            ]
            return stats

        # 1. Identity resolution. Endpoints are foreign keys, so resolution asks
        #    whether both endpoints are still devices in THIS tenant.
        resolved_rows: list[Any] = []
        for row in rows:
            if row.source_resource_id not in device_ids or row.target_resource_id not in device_ids:
                stats["unresolved"] += 1
                continue
            if row.source_resource_id == row.target_resource_id:
                # A self-edge relates a resource to itself: not an edge.
                stats["skipped"] += 1
                continue
            resolved_rows.append(row)

        # 2. Ambiguity is about the identity KEY the evidence used, not the join:
        #    an address recorded on two devices is not a unique identity, so any
        #    edge resolved through it (or ending on such a device) is AMBIGUOUS and
        #    must be visible rather than looking settled.
        address_counts = self._address_counts(devices)
        ambiguous_addresses = {address for address, count in address_counts.items() if count > 1}
        ambiguous_device_ids = {
            device.id
            for device in devices
            if (device.ip_address or "").strip() in ambiguous_addresses
        }

        # 3. Canonical grouping: directed rows describing ONE physical
        #    relationship share a pair key (endpoints sorted, so A->B and B->A
        #    collapse into it) plus the relationship type.
        groups: dict[tuple[uuid.UUID, uuid.UUID, str], list[Any]] = {}
        for row in resolved_rows:
            low, high = sorted((row.source_resource_id, row.target_resource_id), key=str)
            groups.setdefault((low, high, row.relationship_type), []).append(row)
        stats["canonical_relationships"] = len(groups)

        # 4. Conflict detection - two deterministic, evidence-level rules. A
        #    conflict is REPORTED and flagged, never silently resolved by
        #    preferring one source over another.
        conflicts: list[dict[str, Any]] = []
        by_port: dict[tuple[uuid.UUID, str], set[uuid.UUID]] = {}
        for row in resolved_rows:
            local_if = (row.attributes or {}).get("local_interface")
            if not local_if:
                continue
            by_port.setdefault((row.source_resource_id, str(local_if)), set()).add(
                row.target_resource_id
            )
        for key in sorted(by_port, key=str):
            owner_id, local_if = key
            targets = by_port[key]
            if len(targets) > 1:
                conflicts.append(
                    {
                        "kind": "interface_claims_multiple_peers",
                        "resource_id": str(owner_id),
                        "local_interface": local_if,
                        "target_ids": sorted(str(target) for target in targets),
                    }
                )
        disagreeing_pairs: set[tuple[str, str]] = set()
        for key in sorted(groups, key=str):
            low, high, rel_type = key
            group = groups[key]
            local_interfaces = {str((row.attributes or {}).get("local_interface")) for row in group}
            sources = {row.discovered_by for row in group}
            # Same pair, multiple sources, different port detail: the sources do
            # not agree on HOW the two devices are connected.
            if len(group) > 1 and len(sources) > 1 and len(local_interfaces) > 1:
                disagreeing_pairs.add((str(low), str(high)))
                conflicts.append(
                    {
                        "kind": "sources_disagree_on_interface",
                        "pair": [str(low), str(high)],
                        "relationship_type": rel_type,
                        "local_interfaces": sorted(local_interfaces),
                        "sources": sorted(sources),
                    }
                )
        stats["conflicts"] = len(conflicts)

        # 5. Annotate each row with its canonical view, idempotently.
        for row in resolved_rows:
            low, high = sorted((row.source_resource_id, row.target_resource_id), key=str)
            group = groups[(low, high, row.relationship_type)]
            ambiguous = bool(
                row.source_resource_id in ambiguous_device_ids
                or row.target_resource_id in ambiguous_device_ids
                or self._evidence_address(row) in ambiguous_addresses
            )
            canonical_body = {
                "canonical_key": f"{low}:{high}:{row.relationship_type}",
                "pair": [str(low), str(high)],
                "direction": "forward" if str(row.source_resource_id) == str(low) else "reverse",
                "members": len(group),
                "evidence_sources": sorted({member.discovered_by for member in group}),
                "freshness": self._freshness(row, observed_at),
                "resolution": RESOLUTION_AMBIGUOUS if ambiguous else RESOLUTION_RESOLVED,
                "conflict": bool(
                    (str(row.source_resource_id), str((row.attributes or {}).get("local_interface")))
                    in {(conflict["resource_id"], conflict["local_interface"])
                        for conflict in conflicts
                        if conflict["kind"] == "interface_claims_multiple_peers"}
                    or (str(low), str(high)) in disagreeing_pairs
                ),
            }
            stats["resolved"] += 1
            if canonical_body["freshness"] == FRESHNESS_STALE:
                stats["stale"] += 1
            if ambiguous:
                stats["ambiguous"] += 1

            # Idempotency: the run timestamp is NOT part of "did anything change".
            # Comparing the body means a second run over unchanged evidence is
            # honestly reported as unchanged, and `reconciled_at` keeps meaning
            # "when this view last actually changed".
            previous = (row.attributes or {}).get("canonical") or {}
            previous_body = {key: value for key, value in previous.items() if key != "reconciled_at"}
            if previous_body == canonical_body:
                stats["relationships_unchanged"] += 1
                continue
            canonical = {**canonical_body, "reconciled_at": observed_at.isoformat()}
            if dry_run:
                stats["relationships_updated"] += 1
                continue
            try:
                await self.relationships.update(
                    row, attributes={**(row.attributes or {}), "canonical": canonical}
                )
                stats["relationships_updated"] += 1
            except Exception:  # noqa: BLE001 - one bad row must not fail the run
                stats["errors"] += 1
                logger.exception("canonical annotation failed for relationship %s", row.id)

        stats["conflict_samples"] = conflicts[:20]
        limitations = [
            "Bidirectional rows are PRESERVED (one stored row per direction); the "
            "canonical view groups them as one physical relationship for consumers, "
            "so no relationship migration is required.",
            "Unresolved evidence is reported, never rewritten into an edge and never "
            "deleted: the recorded rows are left exactly as they are.",
        ]
        if ambiguous_addresses:
            limitations.append(
                f"{len(ambiguous_addresses)} address(es) are recorded on more than one device in "
                f"this tenant; {stats['ambiguous']} recorded relationship(s) resolve through such "
                "an identity, so those are reported as AMBIGUOUS - the resolver's deterministic "
                "order decided the endpoint, and that is reported rather than presented as settled."
            )
        if conflicts:
            limitations.append(
                f"{len(conflicts)} conflict(s) between evidence sources are preserved and "
                "flagged; no source was preferred over another and none was hidden."
            )
        stats["limitations"] = limitations
        return stats
