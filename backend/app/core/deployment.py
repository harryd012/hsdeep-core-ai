"""
Deployment model (Section 19: Online and Offline Appliance Architecture).

Pure, dependency-free catalogue of the deployment modes the platform can run
in, plus helpers to reason about synchronization / air-gap behaviour. Used by
configuration tooling, docs, and unit tests. The ORM/scheduler layers read
these constants when deciding whether to emit cloud-bound sync jobs.

Deployment modes:
  * saas            - central multi-tenant SaaS control plane
  * private_cloud   - customer/tenant-dedicated private cloud instance
  * on_premises     - customer-hosted on-premises instance
  * customer_hosted - customer-hosted cloud (a.k.a. bring-your-own-cloud)
  * offline         - disconnected on-site appliance (no constant cloud)
  * air_gapped      - fully offline, physically isolated appliance
  * hybrid          - local collectors/appliance + optional delayed sync
  * edge            - lightweight collector appliance at a remote site

Every mode decides: whether a local UI / auth / DB / collectors / AI exist
and whether cloud sync is allowed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DeploymentMode:
    key: str
    label: str
    description: str
    local_ui: bool
    local_auth: bool
    local_db: bool
    local_collectors: bool
    local_alerting: bool
    local_ai: bool
    cloud_sync_allowed: bool
    requires_cloud: bool
    sync_transport: str = ""
    capabilities: tuple[str, ...] = ()

    def allow_sync(self) -> bool:
        return self.cloud_sync_allowed and self.sync_transport.startswith("outbound-")


_DESCRIPTIONS: dict[str, tuple[str, str, bool, bool, bool, bool, bool, bool, bool, bool, str, tuple[str, ...]]] = {
    # key -> (label, desc, local_ui, local_auth, local_db, local_collectors,
    #         local_alerting, local_ai, cloud_sync, requires_cloud, transport, caps)
    "saas": (
        "SaaS Cloud", "Central multi-tenant control plane hosted by HSDEEP.",
        True, False, False, True, True, False, True, True, "n/a",
        ("multi_tenant", "central_billing", "central_subscription",
         "remote_collector_management"),
    ),
    "private_cloud": (
        "Private Cloud", "Tenant-dedicated cloud instance, often in the customer's cloud account.",
        True, False, False, True, True, False, True, False, "outbound-https",
        ("dedicated", "byoc", "cloud_native"),
    ),
    "on_premises": (
        "On-Premises", "Customer-hosted instance inside their data centre (no cloud egress required).",
        True, True, True, True, True, True, False, False, "",
        ("local_first", "local_ai", "secure_export"),
    ),
    "customer_hosted": (
        "Customer-Hosted", "Customer-hosted cloud deployment (SaaS software on customer cloud).",
        True, False, False, True, True, False, True, False, "outbound-https",
        ("byoc", "cloud_native"),
    ),
    "offline": (
        "Offline Appliance", "Disconnected on-site appliance; optional delayed sync when connectivity returns.",
        True, True, True, True, True, True, True, False, "outbound-https",
        ("local_first", "local_ai", "local_runbooks", "local_audit",
         "local_backup", "delayed_sync"),
    ),
    "air_gapped": (
        "Air-Gapped Appliance", "Physically isolated appliance; no network egress, ever.",
        True, True, True, True, True, True, False, False, "",
        ("local_first", "local_ai", "local_runbooks", "local_audit",
         "local_backup", "secure_export"),
    ),
    "hybrid": (
        "Hybrid", "Local collectors at customer sites with secure outbound-only sync to a control plane.",
        True, True, True, True, True, True, True, False, "outbound-https",
        ("local_buffer", "retry_replay", "dedup", "sync_status",
         "conflict_resolution", "tenant_scoped_sync"),
    ),
    "edge": (
        "Edge Collector", "Lightweight collector appliance at a remote site; forwards to a central instance.",
        False, False, False, True, False, False, True, False, "outbound-https",
        ("local_buffer", "retry_replay", "dedup"),
    ),
}

MODES: dict[str, DeploymentMode] = {
    key: DeploymentMode(
        key=key, label=t[0], description=t[1], local_ui=t[2], local_auth=t[3],
        local_db=t[4], local_collectors=t[5], local_alerting=t[6], local_ai=t[7],
        cloud_sync_allowed=t[8], requires_cloud=t[9], sync_transport=t[10],
        capabilities=t[11],
    )
    for key, t in _DESCRIPTIONS.items()
}

ALL_KEYS: tuple[str, ...] = tuple(_DESCRIPTIONS.keys())


def get_mode(key: str | None) -> DeploymentMode:
    """Return the deployment mode for a key; defaults to SaaS for None/unknown."""
    if not key:
        return MODES["saas"]
    return MODES.get(key.strip().lower(), MODES["saas"])


def offline_eligible(key: str | None) -> bool:
    """Whether this mode can operate without constant cloud connectivity."""
    mode = get_mode(key)
    return mode.local_db and not mode.requires_cloud


def sync_behaviour(mode_key: str | None) -> dict[str, Any]:
    """Describe the sync posture expected of an appliance in this mode

    (buffering / replay / dedup / visibility requirements, see capabilities).
    """
    mode = get_mode(mode_key)
    caps = mode.capabilities
    return {
        "mode": mode.key,
        "sync_allowed": mode.allow_sync(),
        "local_buffer": "local_buffer" in caps,
        "retry_replay": "retry_replay" in caps,
        "duplicate_event_prevention": "dedup" in caps,
        "sync_status_visibility": "sync_status" in caps,
        "conflict_resolution": "conflict_resolution" in caps,
        "tenant_scoped_sync": "tenant_scoped_sync" in caps,
        "local_ai": mode.local_ai,
        "local_runbooks": "local_runbooks" in caps,
        "local_backup": "local_backup" in caps,
        "secure_export_import": "secure_export" in caps,
    }

