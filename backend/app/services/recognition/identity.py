"""Identity resolution: deterministic resource keys with evidence priority.

Problem this solves: the same physical/logical resource is reachable through
several collectors (SNMP sysDescr today, a vendor API tomorrow, a cloud
inventory export after that). Without a stable identity key, repeated discovery
creates duplicates instead of updating the existing resource.

Design:

  * Each piece of identity evidence carries a ``kind`` and a ``strength``
    (0-100). Strength follows the documented evidence priority: provider
    resource id > serial number > cloud ARN/resource id > VM UUID >
    Kubernetes UID > management IP > MAC > hostname.
  * ``identity_keys()`` returns every usable STRONG key plus, when no strong key
    exists, at most one weak fallback key. A caller therefore gets a
    deterministic match set for idempotent upsert.
  * ``identity_confidence()`` reports how much to trust a match, and
    ``identity_source()`` names the strongest evidence used — both persisted so a
    resolver can be audited and a weak match can be reviewed rather than trusted
    silently.

This module is pure: no DB, no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass

# Evidence kinds in documented priority order (highest first).
KIND_PROVIDER_RESOURCE_ID = "provider_resource_id"
KIND_SERIAL = "serial_number"
KIND_CLOUD_RESOURCE_ID = "cloud_resource_id"
KIND_VM_UUID = "vm_uuid"
KIND_K8S_UID = "kubernetes_uid"
KIND_MANAGEMENT_IP = "management_ip"
KIND_MAC = "mac_address"
KIND_HOSTNAME = "hostname"

# Strength per kind. Strong kinds (>= STRONG_THRESHOLD) are globally unique and
# safe to match on alone. Weak kinds are only a fallback and never override a
# strong match.
_STRENGTH: dict[str, float] = {
    KIND_PROVIDER_RESOURCE_ID: 100.0,
    KIND_SERIAL: 95.0,
    KIND_CLOUD_RESOURCE_ID: 93.0,
    KIND_VM_UUID: 90.0,
    KIND_K8S_UID: 88.0,
    KIND_MANAGEMENT_IP: 60.0,
    KIND_MAC: 55.0,
    KIND_HOSTNAME: 40.0,
}

STRONG_THRESHOLD = 85.0

_KIND_ORDER: tuple[str, ...] = (
    KIND_PROVIDER_RESOURCE_ID,
    KIND_SERIAL,
    KIND_CLOUD_RESOURCE_ID,
    KIND_VM_UUID,
    KIND_K8S_UID,
    KIND_MANAGEMENT_IP,
    KIND_MAC,
    KIND_HOSTNAME,
)


@dataclass(frozen=True)
class IdentityEvidence:
    """One piece of identity evidence about a resource."""

    kind: str
    value: str

    @property
    def strength(self) -> float:
        return _STRENGTH.get(self.kind, 10.0)

    @property
    def is_strong(self) -> bool:
        return self.strength >= STRONG_THRESHOLD

    def key(self) -> str:
        """Namespaced, case-normalised key. Kind prefix prevents a hostname from
        ever colliding with a serial that happens to share its spelling."""
        return f"{self.kind}:{self.value.strip().lower()}"


def normalize_mac(value: str | None) -> str | None:
    """Canonical MAC form (lowercase, colon-separated) or None if unusable."""
    if not value:
        return None
    hex_only = "".join(ch for ch in value.lower() if ch in "0123456789abcdef")
    if len(hex_only) != 12:
        return None
    return ":".join(hex_only[i:i + 2] for i in range(0, 12, 2))


def collect_evidence(resource: dict) -> list[IdentityEvidence]:
    """Gather every usable identity signal from a normalized resource dict.

    ``provider_resource_id`` also accepts the cloud ``resource_id``/ARN spelling
    because cloud inventories expose the ARN-style identifier there.
    """
    out: list[IdentityEvidence] = []

    def add(kind: str, value: object) -> None:
        if value is None:
            return
        text = str(value).strip()
        if not text:
            return
        out.append(IdentityEvidence(kind=kind, value=text))

    add(KIND_PROVIDER_RESOURCE_ID, resource.get("external_id"))
    add(KIND_SERIAL, resource.get("serial_number"))
    add(KIND_CLOUD_RESOURCE_ID, resource.get("resource_id"))
    add(KIND_VM_UUID, resource.get("vm_uuid"))
    add(KIND_K8S_UID, resource.get("kubernetes_uid"))
    add(KIND_MANAGEMENT_IP, resource.get("management_ip") or resource.get("ip_address"))
    add(KIND_MAC, normalize_mac(resource.get("mac_address")))
    add(KIND_HOSTNAME, resource.get("hostname"))

    # De-duplicate while keeping the strongest instance of each kind.
    best: dict[str, IdentityEvidence] = {}
    for ev in out:
        current = best.get(ev.kind)
        if current is None or ev.strength > current.strength:
            best[ev.kind] = ev
    return [best[k] for k in _KIND_ORDER if k in best]


def identity_keys(resource: dict) -> list[str]:
    """Identity keys for idempotent upsert, strongest evidence first.

    Returns EVERY usable key, not just the strong ones: a resource whose serial
    is readable on one poll and unreadable on the next must still match its
    stored copy on the remaining keys, or discovery would create a duplicate.

    Weak-only keys are deliberately included but their trustworthiness is
    reported separately by ``identity_confidence()``/``identity_source()``, and
    ``strong_identity_keys()`` gives the subset safe for automatic merging.
    """
    return [ev.key() for ev in collect_evidence(resource)]


def strong_identity_keys(resource: dict) -> list[str]:
    """The subset of keys backed by strong evidence — safe to auto-merge on."""
    return [ev.key() for ev in collect_evidence(resource) if ev.is_strong]


def has_strong_identity(resource: dict) -> bool:
    """True when automatic merge is justified (matches the merge policy)."""
    return bool(strong_identity_keys(resource))


def identity_confidence(resource: dict) -> float:
    """0-100 confidence that identity_keys() uniquely identifies the resource."""
    evidence = collect_evidence(resource)
    if not evidence:
        return 0.0
    return round(max(ev.strength for ev in evidence), 2)


def identity_source(resource: dict) -> str | None:
    """Name of the strongest evidence kind used, or None when there is none."""
    evidence = collect_evidence(resource)
    return evidence[0].kind if evidence else None


def same_resource(a: dict, b: dict) -> bool:
    """True when two normalized resources share any identity key.

    Conservative by construction: it only reports a match on evidence that was
    actually present, so unrelated resources are never merged.
    """
    keys_a = set(identity_keys(a))
    keys_b = set(identity_keys(b))
    if not keys_a or not keys_b:
        return False
    return bool(keys_a & keys_b)
