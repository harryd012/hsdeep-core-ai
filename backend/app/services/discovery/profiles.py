"""
Discovery profiles (Phase 9.4 §1/§2).

A profile is a NAMED, reusable bundle of configuration that a ``DiscoveryScope``
already holds: allowed_protocols / max_depth / max_hosts / max_concurrency /
timeout / excluded_targets.

Why this is not a second policy system
--------------------------------------
A profile supplies DEFAULTS for fields the caller did not state. The
``DiscoveryScope`` row stays the single source of truth for what a tenant is
actually allowed to scan, and ``scope_runner`` stays the only execution
authority. Applying a profile is a pure function over a dict; it probes nothing,
persists nothing and re-implements no part of the scope pipeline.

Deliberate non-goals
--------------------
* No profile carries a command, an executable, an argv list or a vendor CLI flag.
  A profile is a configuration object, so anything able to express "run this on
  the host" is out of scope by construction. ``ALLOWED_PROFILE_FIELDS`` and
  ``validate_profile`` enforce that structurally rather than by comment.
* A profile can never widen authority: it cannot exceed the API schema's bounds
  or name a protocol this platform does not implement. Both are validated at
  import time, so a bad profile fails on startup, not on a customer's scan.
* A profile never silently becomes a broader one. An unknown name raises; there
  is no fallback, because "scanned more than asked" is unrecoverable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from dataclasses import fields as dataclass_fields
from typing import Any, Iterable, Mapping

from app.models.discovery_scope import ALLOWED_PROTOCOLS
from app.services.infrastructure_intelligence import vocabulary as V

PROFILE_BASIC = "basic"
PROFILE_BALANCED = "balanced"
PROFILE_DEEP = "deep"
PROFILE_CLOUD = "cloud"
PROFILE_TOPOLOGY = "topology"

#: Declaration order is the API's presentation order and is deterministic.
PROFILE_NAMES: tuple[str, ...] = (
    PROFILE_BASIC,
    PROFILE_BALANCED,
    PROFILE_DEEP,
    PROFILE_CLOUD,
    PROFILE_TOPOLOGY,
)

#: The profile returned when a caller asks for "the default" without naming one.
#: BALANCED is also the profile whose limits equal the DiscoveryScope model
#: defaults, so "the default profile" and "a default scope" can never disagree.
DEFAULT_PROFILE = PROFILE_BALANCED

#: The ONLY fields a profile may carry. Anything else - a command, a script, an
#: argv list, a vendor flag - is rejected when the module loads rather than
#: ignored, because a silently ignored field is how a scanner option creeps into
#: what is supposed to be a data object.
ALLOWED_PROFILE_FIELDS: frozenset[str] = frozenset({
    "name",
    "description",
    "allowed_protocols",
    "max_depth",
    "max_hosts",
    "max_concurrency",
    "timeout",
    "excluded_targets",
    "metadata",
})

#: Bounds mirrored from ``DiscoveryScopeCreate`` (app/schemas/discovery_scope.py).
#: Mirrored deliberately: a profile that could never be persisted must fail HERE,
#: at import, instead of at the moment a customer creates a scope.
DEPTH_BOUNDS = (0, 10)
HOSTS_BOUNDS = (1, 65536)
CONCURRENCY_BOUNDS = (1, 256)
TIMEOUT_BOUNDS = (0.0, 60.0)


class UnknownDiscoveryProfile(ValueError):
    """Raised when a profile name is not one this platform declares."""


@dataclass(frozen=True)
class DiscoveryProfile:
    """Provider-neutral, reusable defaults for one kind of discovery run."""

    name: str
    description: str
    allowed_protocols: tuple[str, ...]
    max_depth: int
    max_hosts: int
    max_concurrency: int
    timeout: float
    excluded_targets: tuple[str, ...] = ()
    #: Non-secret intent hints for the UI/logs. Never execution parameters.
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def evidence_classifications(self) -> tuple[str, ...]:
        """Classifications this profile's protocols produce (derived, not stored).

        Derived through the canonical vocabulary so a profile can never declare
        an evidence classification its own protocols contradict.
        """
        found = {
            V.evidence_classification(protocol) for protocol in self.allowed_protocols
        }
        return tuple(sorted(c for c in found if c))

    def scope_defaults(self) -> dict[str, Any]:
        """The DiscoveryScope fields this profile supplies.

        Lists, not tuples: these values are written to JSONB columns, and a
        tuple would be persisted as a different type than the API accepts.
        """
        return {
            "allowed_protocols": list(self.allowed_protocols),
            "max_depth": self.max_depth,
            "max_hosts": self.max_hosts,
            "max_concurrency": self.max_concurrency,
            "timeout": self.timeout,
            "excluded_targets": list(self.excluded_targets),
        }

    def to_dict(self) -> dict[str, Any]:
        """API projection: scope fields + what the profile means."""
        payload: dict[str, Any] = dict(self.scope_defaults())
        payload.update({
            "name": self.name,
            "description": self.description,
            "evidence_classifications": list(self.evidence_classifications),
            "is_default": self.name == DEFAULT_PROFILE,
            "metadata": dict(self.metadata),
        })
        return payload


def validate_profile(profile: DiscoveryProfile) -> None:
    """Raise ``ValueError`` when a profile could not be persisted as a scope.

    Called for every declared profile at import time, so an invalid profile is a
    startup failure rather than a runtime surprise.
    """
    declared = {spec.name for spec in dataclass_fields(profile)}
    extra = declared - ALLOWED_PROFILE_FIELDS
    if extra:
        raise ValueError(
            f"profile {profile.name!r} declares non-configuration field(s) "
            f"{sorted(extra)}; a profile may only carry "
            f"{sorted(ALLOWED_PROFILE_FIELDS)}"
        )

    if profile.name not in PROFILE_NAMES:
        raise ValueError(
            f"profile name {profile.name!r} is not in {list(PROFILE_NAMES)}"
        )
    if not profile.description.strip():
        raise ValueError(f"profile {profile.name!r} needs a description")

    if not profile.allowed_protocols:
        raise ValueError(
            f"profile {profile.name!r} allows no protocol; use an explicit "
            "empty scope instead of a profile that discovers nothing"
        )
    unsupported = sorted(set(profile.allowed_protocols) - ALLOWED_PROTOCOLS)
    if unsupported:
        raise ValueError(
            f"profile {profile.name!r} names unsupported protocol(s) "
            f"{unsupported}; this platform implements {sorted(ALLOWED_PROTOCOLS)}"
        )

    for label, value, (low, high) in (
        ("max_depth", profile.max_depth, DEPTH_BOUNDS),
        ("max_hosts", profile.max_hosts, HOSTS_BOUNDS),
        ("max_concurrency", profile.max_concurrency, CONCURRENCY_BOUNDS),
    ):
        if not isinstance(value, int) or not low <= value <= high:
            raise ValueError(
                f"profile {profile.name!r} {label}={value!r} is outside {low}..{high}"
            )
    low, high = TIMEOUT_BOUNDS
    if not low < profile.timeout <= high:
        raise ValueError(
            f"profile {profile.name!r} timeout={profile.timeout!r} is outside "
            f"0..{high} seconds"
        )


BASIC = DiscoveryProfile(
    name=PROFILE_BASIC,
    description=(
        "Reachability and basic presence: ICMP only, one hop, low concurrency. "
        "A safe first pass that costs the network almost nothing."
    ),
    allowed_protocols=("icmp",),
    max_depth=1,
    max_hosts=128,
    max_concurrency=16,
    timeout=0.5,
    metadata={"intent": "initial_discovery"},
)

BALANCED = DiscoveryProfile(
    name=PROFILE_BALANCED,
    description=(
        "Normal enterprise discovery: ICMP plus the common identity protocols "
        "(SNMP/SSH/HTTP/HTTPS) at the DiscoveryScope default limits."
    ),
    allowed_protocols=("icmp", "snmp", "ssh", "http", "https"),
    max_depth=1,
    max_hosts=256,
    max_concurrency=32,
    timeout=1.0,
    metadata={"intent": "operational_default"},
)

DEEP = DiscoveryProfile(
    name=PROFILE_DEEP,
    description=(
        "Detailed infrastructure discovery: every implemented host protocol, "
        "two hops, a larger host budget and a longer per-probe allowance."
    ),
    allowed_protocols=("icmp", "snmp", "ssh", "http", "https", "wmi", "winrm"),
    max_depth=2,
    max_hosts=1024,
    max_concurrency=64,
    timeout=2.0,
    metadata={"intent": "detailed_discovery"},
)

CLOUD = DiscoveryProfile(
    name=PROFILE_CLOUD,
    description=(
        "Cloud/provider inventory: API-classified evidence only, through the "
        "existing cloud collectors. No host-level probing."
    ),
    allowed_protocols=("api",),
    max_depth=1,
    max_hosts=256,
    max_concurrency=16,
    timeout=5.0,
    metadata={"intent": "cloud_inventory"},
)

TOPOLOGY = DiscoveryProfile(
    name=PROFILE_TOPOLOGY,
    description=(
        "Relationship evidence: the protocols that can actually observe an "
        "adjacency (SNMP for LLDP/CDP neighbours, SSH, ICMP), walked deeper so "
        "neighbours are re-probed. No topology is inferred from IP adjacency."
    ),
    allowed_protocols=("icmp", "snmp", "ssh"),
    max_depth=3,
    max_hosts=512,
    max_concurrency=32,
    timeout=1.5,
    metadata={"intent": "topology_evidence"},
)

#: The declared catalogue. Iteration order is ``PROFILE_NAMES`` - never dict
#: insertion luck - so every consumer sees the same order.
PROFILES: dict[str, DiscoveryProfile] = {
    profile.name: profile
    for profile in (BASIC, BALANCED, DEEP, CLOUD, TOPOLOGY)
}

#: Fail fast: an invalid declared profile must not reach a customer's scan.
for _profile in PROFILES.values():
    validate_profile(_profile)
del _profile


def profile_names() -> tuple[str, ...]:
    """The declared profile names, in declaration order."""
    return PROFILE_NAMES


def resolve_profile(name: str | None = None) -> DiscoveryProfile:
    """The profile for ``name``; ``None``/empty means the default profile.

    An unknown name raises ``UnknownDiscoveryProfile`` and NEVER falls back to a
    broader profile. Matching is exact (case-sensitive) so that "which profile
    ran?" has exactly one answer for any given string.
    """
    if name is None or name == "":
        return PROFILES[DEFAULT_PROFILE]
    if not isinstance(name, str):
        raise UnknownDiscoveryProfile(
            f"unknown discovery profile {name!r}; expected one of {list(PROFILE_NAMES)}"
        )
    profile = PROFILES.get(name.strip())
    if profile is None:
        raise UnknownDiscoveryProfile(
            f"unknown discovery profile {name!r}; expected one of {list(PROFILE_NAMES)}"
        )
    return profile


def apply_profile(
    name: str | None,
    fields: Mapping[str, Any],
    *,
    explicit: Iterable[str] = (),
) -> dict[str, Any]:
    """Compose a profile's defaults UNDER the caller's own values.

    ``explicit`` names the keys the caller actually stated; those always win, so
    a profile can never override an explicit operator decision. With no ``name``
    the input is returned unchanged - a profile is applied only when one is asked
    for, which is what keeps every existing caller's behaviour identical.
    """
    composed = dict(fields)
    if not name:
        return composed
    profile = resolve_profile(name)
    stated = set(explicit)
    for key, value in profile.scope_defaults().items():
        if key in stated:
            continue
        composed[key] = value
    return composed


def profile_catalog() -> list[dict[str, Any]]:
    """Every profile as the API returns it, in deterministic order."""
    return [PROFILES[name].to_dict() for name in PROFILE_NAMES]
