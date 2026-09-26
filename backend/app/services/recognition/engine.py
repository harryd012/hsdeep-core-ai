"""Universal recognition engine: evidence in, normalized resource out.

One entry point for every discovery method. It performs no I/O — callers
(collectors / cloud connectors) supply whatever evidence they already hold:
ICMP response, SNMP sysDescr/sysObjectID, SSH banner, WMI/WinRM output, cloud
resource metadata, Kubernetes object, MAC, or a provider payload.

The engine asks adapters whose fingerprints the evidence can satisfy, picks the
highest-scoring claim with deterministic tie-breaking, and emits the normalized
resource plus a ``recognition`` provenance block. When nothing recognises the
target it returns ``resource_type="unknown"`` — still carrying IP/hostname/MAC —
so an unrecognised device is inventoried and can be classified manually later.
Nothing is invented: absent fields are omitted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.collectors.classification import (
    classify_from_name,
    classify_from_sysdescr,
    classify_meraki_model,
)
from app.services.recognition.identity import (
    identity_confidence,
    identity_keys,
    identity_source,
)
from app.services.recognition.providers import ProviderRegistry
from app.services.recognition.taxonomy import (
    CATEGORY_UNKNOWN,
    category_for,
    normalize_resource_type,
)

UNKNOWN_TYPE = "unknown"

_EVIDENCE_KEYS = (
    "sys_descr", "sys_object_id", "model", "vendor", "name", "hostname",
    "ip", "mac", "serial_number", "external_id", "resource_type", "provider",
)


@dataclass
class Recognition:
    """Provenance for a recognition decision — persisted for auditability."""

    method: str
    matched: bool
    score: float = 0.0
    provider: str | None = None
    evidence_used: list[str] = field(default_factory=list)
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "matched": self.matched,
            "score": round(self.score, 2),
            "provider": self.provider,
            "evidence_used": list(self.evidence_used),
            "reason": self.reason,
        }


@dataclass
class RecognizedResource:
    """The normalized resource produced by recognition."""

    resource_type: str
    resource_category: str
    recognition: Recognition
    provider: str | None = None
    vendor: str | None = None
    name: str = ""
    hostname: str | None = None
    management_ip: str | None = None
    model: str | None = None
    version: str | None = None
    serial_number: str | None = None
    mac_address: str | None = None
    external_id: str | None = None
    region: str | None = None
    availability_zone: str | None = None
    capabilities: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Normalized resource dict; empty optionals are omitted, never faked."""
        out: dict[str, Any] = {
            "resource_type": self.resource_type,
            "resource_category": self.resource_category,
            "name": self.name or self.hostname or self.management_ip or "",
            "recognition": self.recognition.as_dict(),
        }
        for key in (
            "provider", "vendor", "hostname", "management_ip", "model",
            "version", "serial_number", "mac_address", "external_id",
            "region", "availability_zone",
        ):
            value = getattr(self, key)
            if value not in (None, ""):
                out[key] = value
        if self.capabilities:
            out["capabilities"] = list(self.capabilities)
        if self.metadata:
            out["metadata"] = self.metadata
        return out


def _claim_score(adapter_cls, evidence: dict[str, Any]) -> float:
    """Sum of fingerprint weights whose evidence constraints are satisfied."""
    score = 0.0
    for fp in adapter_cls.fingerprints:
        if fp.matches(evidence):
            score += fp.weight
    return score


def _heuristic_hint(evidence: dict[str, Any]) -> tuple[str | None, str | None]:
    """(vendor, type) already derivable from sysDescr / Meraki model / name."""
    sys_descr = evidence.get("sys_descr")
    if sys_descr:
        device_type, vendor, _model = classify_from_sysdescr(sys_descr)
        return vendor, device_type
    model = evidence.get("model")
    if model:
        meraki_type = classify_meraki_model(model)
        if meraki_type and meraki_type != UNKNOWN_TYPE:
            return "Cisco Meraki", meraki_type
    name = evidence.get("name") or evidence.get("hostname")
    if name:
        return None, classify_from_name(name)
    return None, None


def _common_fields(evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "hostname": evidence.get("hostname") or evidence.get("name"),
        "management_ip": evidence.get("ip") or evidence.get("management_ip"),
        "model": evidence.get("model"),
        "version": evidence.get("version"),
        "serial_number": evidence.get("serial_number"),
        "mac_address": evidence.get("mac"),
        "external_id": evidence.get("external_id"),
        "region": evidence.get("region"),
        "availability_zone": evidence.get("availability_zone"),
    }


def recognize(evidence: dict[str, Any]) -> RecognizedResource:
    """Recognise one target from the evidence a collector already holds.

    Supply at least one identifier. Recognised keys: ``sys_descr``,
    ``sys_object_id``, ``model``, ``vendor``, ``name``, ``hostname``, ``ip``,
    ``mac``, ``serial_number``, ``external_id``, ``resource_type``,
    ``provider``, ``region``, ``availability_zone``, ``capabilities``,
    ``metadata``.
    """
    evidence = dict(evidence or {})
    evidence_used = [k for k in _EVIDENCE_KEYS if evidence.get(k)]
    common = _common_fields(evidence)
    name = evidence.get("name") or common["hostname"] or common["management_ip"] or ""

    # ---- 1. provider-declared type wins outright ---------------------------
    declared_type = evidence.get("resource_type")
    declared_provider = evidence.get("provider")
    if declared_type:
        rtype = normalize_resource_type(declared_type)
        adapter = (
            ProviderRegistry.try_get(declared_provider) if declared_provider else None
        )
        caps = evidence.get("capabilities")
        return RecognizedResource(
            resource_type=rtype,
            resource_category=category_for(rtype),
            provider=declared_provider or (adapter.provider if adapter else None),
            vendor=evidence.get("vendor"),
            name=name,
            capabilities=tuple(caps) if caps else (
                tuple(adapter.capabilities) if adapter else ()
            ),
            metadata=dict(evidence.get("metadata") or {}),
            recognition=Recognition(
                method="provider_declared",
                matched=True,
                score=100.0,
                provider=declared_provider,
                evidence_used=evidence_used,
                reason=f"provider declared resource_type={rtype}",
            ),
            **common,
        )

    # ---- 2. adapter fingerprints over the supplied evidence ----------------
    best_adapter = None
    best_score = 0.0
    for provider in ProviderRegistry.providers():
        adapter_cls = ProviderRegistry.get(provider)
        score = _claim_score(adapter_cls, evidence)
        # Strictly-greater wins and providers() is sorted, so equal scores always
        # resolve to the same adapter (deterministic selection).
        if score > best_score:
            best_adapter, best_score = adapter_cls, score

    vendor_hint, type_hint = _heuristic_hint(evidence)
    hinted_type = normalize_resource_type(type_hint or UNKNOWN_TYPE)

    if best_adapter is not None:
        return RecognizedResource(
            resource_type=hinted_type,
            resource_category=category_for(hinted_type),
            provider=best_adapter.provider,
            vendor=(evidence.get("vendor") or best_adapter.display_name
                    or best_adapter.provider),
            name=name,
            capabilities=tuple(best_adapter.capabilities),
            metadata=dict(evidence.get("metadata") or {}),
            recognition=Recognition(
                method="adapter_fingerprint",
                matched=True,
                score=best_score,
                provider=best_adapter.provider,
                evidence_used=evidence_used,
                reason=f"fingerprint match for provider={best_adapter.provider}",
            ),
            **common,
        )

    if hinted_type != UNKNOWN_TYPE:
        return RecognizedResource(
            resource_type=hinted_type,
            resource_category=category_for(hinted_type),
            vendor=evidence.get("vendor") or vendor_hint,
            name=name,
            metadata=dict(evidence.get("metadata") or {}),
            recognition=Recognition(
                method="heuristic",
                matched=True,
                score=0.0,
                evidence_used=evidence_used,
                reason="classified from sysDescr/model/name heuristic",
            ),
            **common,
        )

    # ---- 3. honest unknown: still inventoried, never force-fit -------------
    return RecognizedResource(
        resource_type=UNKNOWN_TYPE,
        resource_category=CATEGORY_UNKNOWN,
        vendor=evidence.get("vendor"),
        name=name,
        metadata=dict(evidence.get("metadata") or {}),
        recognition=Recognition(
            method="unrecognized",
            matched=False,
            score=0.0,
            evidence_used=evidence_used,
            reason="no adapter fingerprint or heuristic matched the evidence",
        ),
        **common,
    )


def enrich_identity(resource: dict[str, Any]) -> dict[str, Any]:
    """Attach identity keys/confidence/source for idempotent upsert."""
    resource["identity_keys"] = identity_keys(resource)
    resource["identity_confidence"] = identity_confidence(resource)
    resource["identity_source"] = identity_source(resource)
    return resource
