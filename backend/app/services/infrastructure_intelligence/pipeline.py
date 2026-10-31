"""
Normalization pipeline (spec sections 3, 16, 29, 30, 38, 40).

Turns a collector's ``NormalizedBundle`` into canonical, capability-scoped
telemetry plus capability assertions, and persists them.

The shape functions are PURE; only ``CanonicalTelemetrySaver`` touches the
database, so normalization can be (and is) tested without one.

What it deliberately does NOT do
--------------------------------
* It never invents a record for a capability the collector did not report. A
  device with no memory reading produces NO memory telemetry - the capability
  report says ``supported / not_collected`` and the API returns the reason.
* It never renames ``metric_type``. The provider's own name is preserved and the
  canonical name is added alongside it, so no existing consumer breaks and no
  historical series is rewritten.
* It never resolves identity itself; it uses the id maps ``BundleSaver`` already
  built, so no second resource can be created here.
* It does not swallow persistence failures.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.services.infrastructure_intelligence import telemetry as T
from app.services.infrastructure_intelligence import vocabulary as V


@dataclass
class NormalizationResult:
    """Canonical output derived from ONE collector bundle."""

    tenant_id: uuid.UUID
    provider: str | None
    #: device_id -> capabilities the bundle ACTUALLY carried data for.
    collected_by_device: dict[uuid.UUID, set[str]] = field(default_factory=dict)
    #: interface_id -> device_id, for interface-scoped telemetry.
    device_by_interface: dict[uuid.UUID, uuid.UUID] = field(default_factory=dict)
    #: Every metric in the bundle, in canonical form.
    metric_records: list[T.CanonicalTelemetry] = field(default_factory=list)
    #: One availability observation per device, from the device's own status.
    availability_records: list[T.CanonicalTelemetry] = field(default_factory=list)
    #: Capabilities that were expected but absent, and unclassifiable readings.
    notes: list[dict] = field(default_factory=list)

    @property
    def all_records(self) -> list[T.CanonicalTelemetry]:
        return [*self.metric_records, *self.availability_records]

    @property
    def devices_seen(self) -> int:
        return len(self.collected_by_device)

    def to_dict(self) -> dict:
        return {
            "tenant_id": str(self.tenant_id),
            "provider": self.provider,
            "devices_seen": self.devices_seen,
            "metric_records": len(self.metric_records),
            "availability_records": len(self.availability_records),
            "capabilities_collected": {
                str(device_id): sorted(caps)
                for device_id, caps in self.collected_by_device.items()
            },
            "notes": list(self.notes),
        }


def provider_capabilities(provider: str | None) -> frozenset[str]:
    """Capabilities the wired provider for ``provider`` can collect (maybe empty)."""
    from app.services.infrastructure_intelligence.providers import (
        capabilities_for_provider,
    )

    return capabilities_for_provider(provider) or frozenset()


def capability_of_metric(metric_type: str | None) -> str:
    """Capability that produced a reading, or ``""`` when nobody mapped it.

    An unmapped name is NOT force-fitted into a capability: the reading is still
    persisted under its provider name (dropping real telemetry would be worse)
    and the caller records it as ``unclassified_metric``.
    """
    return T.capability_for_metric(metric_type) or ""


def capability_evidence(
    assertion_evidence: dict | None,
    capability: str,
    collected: set[str] | frozenset[str],
    classification: str | None,
) -> dict:
    """Provenance payload for ONE persisted capability assertion (Phase 9.4).

    The classification is ADDED to whatever the assertion already carried - it
    never replaces provider evidence - and is omitted entirely when the provider
    has no declared collection method, because a fabricated classification is
    worse than an absent one. This is metadata only: it cannot grant, widen or
    imply any capability, and it never touches an Action-Plane flag.
    """
    payload = dict(assertion_evidence or {"collected": capability in collected})
    if classification is not None:
        payload["classification"] = classification
    return payload


def normalize_bundle(
    bundle,
    *,
    tenant_id: uuid.UUID,
    provider: str | None,
    device_id_by_external: dict[str, uuid.UUID],
    interface_id_by_key: dict[tuple[str, str], uuid.UUID],
    site_id_by_device: dict[uuid.UUID, uuid.UUID] | None = None,
    collected_at: datetime | None = None,
) -> NormalizationResult:
    """Normalize a collector bundle into canonical telemetry + capability facts.

    ``device_id_by_external`` / ``interface_id_by_key`` are the SAME resolved id
    maps ``BundleSaver`` already builds, so this pipeline never re-resolves
    identity and can never create a resource of its own.
    """
    received = collected_at or datetime.now(timezone.utc)
    result = NormalizationResult(tenant_id=tenant_id, provider=provider)
    site_of = site_id_by_device or {}

    for metric in bundle.metrics:
        device_id = (
            device_id_by_external.get(metric.device_external_id)
            if metric.device_external_id else None
        )
        capability = capability_of_metric(metric.metric_type)
        if not capability:
            result.notes.append({
                "kind": "unclassified_metric",
                "metric_type": metric.metric_type,
                "reason": V.REASON_NOT_YET_COLLECTED,
                "detail": (
                    "metric name is not in the canonical catalog; the reading is "
                    "persisted under its provider name and carries no capability"
                ),
            })
        result.metric_records.append(
            T.CanonicalTelemetry(
                tenant_id=tenant_id,
                site_id=site_of.get(device_id) if device_id else None,
                resource_id=device_id,
                device_id=device_id,
                provider=provider,
                capability=capability or "unclassified",
                metric_name=T.canonical_metric_name(metric.metric_type),
                timestamp=metric.recorded_at,
                numeric_value=(
                    float(metric.value) if metric.value is not None else None
                ),
                unit=metric.unit,
                quality=T.quality_for(metric.recorded_at, now=received),
                source_timestamp=metric.recorded_at,
                received_timestamp=received,
                dimensions={"provider_metric_type": metric.metric_type},
            )
        )
        if device_id is not None and capability:
            result.collected_by_device.setdefault(device_id, set()).add(capability)

    for interface in bundle.interfaces:
        device_id = device_id_by_external.get(interface.device_external_id)
        interface_id = interface_id_by_key.get(
            (interface.device_external_id, interface.external_id)
        )
        if device_id is None or interface_id is None:
            continue
        result.device_by_interface[interface_id] = device_id
        result.collected_by_device.setdefault(device_id, set()).update({
            V.CAP_INTERFACES, V.CAP_BANDWIDTH, V.CAP_ERRORS,
        })

    if bundle.l3_status.get("routes") in ("collected", "empty", "partial"):
        # The routing table was read, so routing is collected for every device
        # in the bundle - even one whose table came back empty.
        for nd in bundle.devices:
            device_id = device_id_by_external.get(nd.external_id)
            if device_id is not None:
                result.collected_by_device.setdefault(device_id, set()).add(V.CAP_ROUTING)

    for neighbor in bundle.neighbors:
        device_id = device_id_by_external.get(neighbor.device_external_id)
        if device_id is not None:
            result.collected_by_device.setdefault(device_id, set()).add(V.CAP_TOPOLOGY)

    for device in bundle.devices:
        device_id = device_id_by_external.get(device.external_id)
        if device_id is None:
            continue
        result.collected_by_device.setdefault(device_id, set()).update({
            V.CAP_INVENTORY, V.CAP_AVAILABILITY,
        })
        result.availability_records.append(
            T.CanonicalTelemetry(
                tenant_id=tenant_id,
                site_id=site_of.get(device_id),
                resource_id=device_id,
                device_id=device_id,
                provider=provider,
                capability=V.CAP_AVAILABILITY,
                metric_name=T.M_REACHABILITY,
                timestamp=received,
                boolean_value=V.operational_state(device.status) == V.OPER_STATE_UP,
                text_value=device.status,
                quality=T.QUALITY_GOOD,
                source_timestamp=received,
                received_timestamp=received,
                dimensions={"source_status": device.status},
            )
        )

    result.notes.extend(_expected_but_absent(result, provider))
    return result


def _expected_but_absent(result: NormalizationResult, provider: str | None) -> list[dict]:
    """Capabilities a WIRED provider can supply that this bundle did not carry.

    Reported, never fabricated, so a device behind a silent provider shows up as
    ``supported / not_collected`` instead of looking fully observed.
    """
    declared = provider_capabilities(provider)
    if not declared:
        return []
    observed: set[str] = set()
    for caps in result.collected_by_device.values():
        observed |= caps
    missing = sorted(declared - observed)
    if not missing:
        return []
    return [{
        "kind": "capability_not_collected",
        "provider": provider,
        "capabilities": missing,
        "status": V.COLLECTION_NOT_COLLECTED,
        "reason": V.REASON_NOT_YET_COLLECTED,
    }]


class CanonicalTelemetrySaver:
    """Persists canonical telemetry + capability assertions for one bundle.

    Repositories are constructor-injected, matching ``BundleSaver`` and the rest
    of the service layer, so this class never opens its own session and every
    write stays inside the caller's transaction.
    """

    def __init__(self, *, metric_repo, capability_repo, device_repo):
        self.metric_repo = metric_repo
        self.capability_repo = capability_repo
        self.device_repo = device_repo

    async def save(
        self,
        result: "NormalizationResult",
        *,
        device_type_by_id: dict[uuid.UUID, str] | None = None,
        observed_at: datetime | None = None,
        include_metrics: bool = True,
    ) -> dict:
        """Write the normalized result. Returns real counts, never estimates.

        ``include_metrics=False`` is used by the collector pipeline when the
        bundle's numeric readings were already written through
        ``MetricRepository.record`` in the SAME transaction (the canonical
        columns are passed there directly, so re-writing them here would create
        duplicate rows). Availability observations and capability assertions are
        still written in both modes - they have no other writer.
        """
        from app.services.infrastructure_intelligence.capabilities import (
            SOURCE_COLLECTOR,
            resolve_capabilities,
        )
        from app.services.infrastructure_intelligence.providers import (
            capabilities_for_provider,
            evidence_classification_for_provider,
        )

        now = observed_at or datetime.now(timezone.utc)
        type_of = device_type_by_id or {}
        provider = result.provider
        provider_caps = capabilities_for_provider(provider)
        #: Phase 9.4 classification for every assertion this provider made, or
        #: None when the provider declares no collection method (never guessed).
        classification = evidence_classification_for_provider(provider)

        metrics_written = 0
        if include_metrics:
            metrics_written = await self.metric_repo.record_canonical_batch(
                result.metric_records
            )
        availability_written = await self.metric_repo.record_canonical_batch(
            result.availability_records
        )

        asserted = 0
        enriched = 0
        for device_id, collected in sorted(
            result.collected_by_device.items(), key=lambda kv: str(kv[0])
        ):
            report = resolve_capabilities(
                type_of.get(device_id, "unknown"),
                provider,
                resource_id=device_id,
                provider_capabilities=provider_caps,
                collected=collected,
            )
            for capability, assertion in sorted(report.assertions.items()):
                # Only FACTS are persisted: a capability we actually collected,
                # or one this (resource, provider) pair demonstrably cannot
                # supply. "Supported but not collected" and "not yet evaluated"
                # are derivable and would otherwise write dozens of rows per
                # device on every single poll.
                if (
                    assertion.support == V.SUPPORT_UNKNOWN
                    and assertion.reason == V.REASON_NOT_YET_COLLECTED
                ):
                    continue
                await self.capability_repo.upsert(
                    tenant_id=result.tenant_id,
                    resource_id=device_id,
                    capability=capability,
                    support=assertion.support,
                    status=assertion.status,
                    reason=assertion.reason,
                    provider=provider,
                    source=SOURCE_COLLECTOR,
                    evidence=capability_evidence(
                        assertion.evidence, capability, collected, classification
                    ),
                    asserted_at=now,
                )
                asserted += 1

            if await self.device_repo.apply_resource_facts(
                tenant_id=result.tenant_id,
                device_id=device_id,
                provider=provider,
                last_telemetry_at=now,
                mark_active=True,
            ):
                enriched += 1

        return {
            "metrics_written": metrics_written,
            "availability_written": availability_written,
            "capabilities_asserted": asserted,
            "resources_enriched": enriched,
            "devices_seen": result.devices_seen,
            "unclassified_metrics": sum(
                1 for note in result.notes if note.get("kind") == "unclassified_metric"
            ),
        }
