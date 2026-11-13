"""
Integration catalogue (P12 slice 2, step 2c; docs/design/INTEGRATION_FRAMEWORK_PLAN.md).

One list of what can be connected, built at request time from the three
registries that already exist. There is no catalogue table:

  * collectors      `CollectorRegistry`           capability "collect"
  * ITSM providers  ITSM `ProviderRegistry`       capability "tickets"
                    (falls back to its built-in list when it was never
                    initialised)
  * action plane    `ActionProviderRegistry`      capability "actions"

Each entry names the configuration it needs (field names only, never
values) and how many the tenant has configured. An ITSM provider type is
`implemented` only when its `class_path` can be imported, so a registry
entry without code (Jira, Freshservice so far) is shown as planned rather
than offered.

Read-only. Synthetic test collectors are left out.
"""
from __future__ import annotations

import importlib
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.actions.providers  # noqa: F401  (providers self-register on import)
import app.collectors  # noqa: F401  (collectors self-register on import)
from app.actions.base import ACTION_CATALOG, ActionProviderRegistry
from app.collectors.base import CollectorRegistry
from app.models.itsm import ITSMConnection
from app.models.monitoring_source import MonitoringSource
from app.models.servicenow import ServiceNowConnection
from app.services.collector_health_service import TEST_SOURCE_TYPES
from app.services.itsm.provider_registry import BUILTIN_PROVIDERS, get_registry

# What each collector's `_require_config` checks, by field name. Kept next to
# the catalogue rather than on 20 collector classes; tests/test_integration_center.py
# fails when a registered collector has no entry here.
COLLECTOR_REQUIRED_CONFIG: dict[str, tuple[str, ...]] = {
    "aws": ("username (access key ID)", "password (secret access key)"),
    "azure": ("config.tenant_id", "config.subscription_id", "config.client_id", "password or api_key (client secret)"),
    "azure_backup": ("config.tenant_id", "config.subscription_id", "config.client_id",
                     "password or api_key (client secret)"),
    "connectivity": ("hostname",),
    "datadog": ("api_key (API key)", "password (application key)", "config.site or hostname"),
    "fortigate": ("hostname", "api_key or password"),
    "fortimanager": ("hostname", "api_key or password"),
    "gcp": ("config.project_id", "password (service-account JSON) or api_key (access token)"),
    "kubernetes": ("hostname, config.kubeconfig or config.in_cluster",),
    "linux_ssh": ("hostname", "username"),
    "meraki": ("password (API key)",),
    "paloalto": ("hostname", "api_key or password"),
    "pnetlab": ("hostname", "username", "password"),
    "prtg": ("hostname", "username", "password"),
    "snmp": ("hostname", "username (SNMPv3 only)"),
    "veeam_backup": ("hostname", "username", "password or api_key"),
    "vmware": ("hostname", "username", "password"),
    "windows_winrm": ("hostname", "username", "password"),
    "wmi": ("hostname", "username", "password"),
    "zabbix": ("hostname", "password or api_key"),
}

_ITSM_AUTH_FIELDS = {
    "oauth2": "client_id + client_secret",
    "basic": "username + password",
    "pat": "personal access token",
    "api_key": "api_key",
}


def _is_test_type(source_type: str) -> bool:
    return source_type in TEST_SOURCE_TYPES or source_type.startswith("test_")


def _importable(class_path: str | None) -> bool:
    if not class_path or "." not in class_path:
        return False
    module, _, name = class_path.rpartition(".")
    try:
        return hasattr(importlib.import_module(module), name)
    except ImportError:
        return False


def _itsm_providers() -> list[dict[str, Any]]:
    providers = get_registry().list_providers()
    return providers or list(BUILTIN_PROVIDERS)


def collector_entries(counts: dict[str, int]) -> list[dict]:
    out = []
    for cls in CollectorRegistry.list_types():
        t = cls.source_type
        if _is_test_type(t):
            continue
        features = [f"firewall: {c}" for c in cls.firewall_capabilities]
        if cls.backup_provider:
            features.append("backups")
        out.append({
            "kind": "collector", "type": t, "display_name": getattr(cls, "display_name", t),
            "category": cls.category, "implemented": bool(cls.implemented), "capabilities": ["collect"],
            "features": features, "auth_modes": [],
            "required_config": list(COLLECTOR_REQUIRED_CONFIG.get(t, ())), "actions": [],
            "configured": counts.get(t, 0),
        })
    return out


def itsm_entries(counts: dict[str, int]) -> list[dict]:
    out = []
    for p in _itsm_providers():
        modes = list(p.get("supported_auth_modes") or [])
        auth = " or ".join(_ITSM_AUTH_FIELDS.get(m, m) for m in modes)
        out.append({
            "kind": "itsm", "type": p["provider_type"], "display_name": p.get("display_name") or p["provider_type"],
            "category": "itsm", "implemented": _importable(p.get("class_path")), "capabilities": ["tickets"],
            "features": sorted(k for k, v in (p.get("capabilities") or {}).items() if v), "auth_modes": modes,
            "required_config": ["instance_url"] + ([f"credentials: {auth}"] if auth else []), "actions": [],
            "configured": counts.get(p["provider_type"], 0),
        })
    return out


def action_entries() -> list[dict]:
    out = []
    for kind in ActionProviderRegistry.list_kinds():
        cls = ActionProviderRegistry.get(kind)
        try:
            supported = cls().supported_actions()
        except Exception:  # noqa: BLE001 - a provider that cannot be built still gets listed
            supported = frozenset()
        specs = [ACTION_CATALOG[a] for a in supported if a in ACTION_CATALOG]
        specs.sort(key=lambda s: s.action.value)
        out.append({
            "kind": "action", "type": kind, "display_name": getattr(cls, "display_name", kind),
            "category": "actions", "implemented": bool(cls.implemented), "capabilities": ["actions"],
            "features": [], "auth_modes": [],
            "required_config": sorted({f"device capability: {s.required_capability}" for s in specs}),
            "actions": [{"action": s.action.value, "classification": s.classification.value,
                         "description": s.description} for s in specs],
            # Actions run against devices of configured sources; nothing is configured per provider.
            "configured": None,
        })
    return out


async def _source_counts(db: AsyncSession, tenant_id: uuid.UUID) -> dict[str, int]:
    rows = (await db.execute(
        select(MonitoringSource.source_type, func.count())
        .where(MonitoringSource.tenant_id == tenant_id, MonitoringSource.is_deleted.is_(False))
        .group_by(MonitoringSource.source_type)
    )).all()
    return {t: n for t, n in rows}


async def _itsm_counts(db: AsyncSession, tenant_id: uuid.UUID) -> dict[str, int]:
    rows = (await db.execute(
        select(ITSMConnection.provider_type, func.count())
        .where(ITSMConnection.tenant_id == tenant_id, ITSMConnection.is_deleted.is_(False))
        .group_by(ITSMConnection.provider_type)
    )).all()
    counts = {t: n for t, n in rows}
    sn = (await db.execute(
        select(func.count()).select_from(ServiceNowConnection)
        .where(ServiceNowConnection.tenant_id == tenant_id, ServiceNowConnection.is_deleted.is_(False))
    )).scalar_one()
    counts["servicenow"] = counts.get("servicenow", 0) + sn
    return counts


async def integration_catalog(db: AsyncSession, tenant_id: uuid.UUID) -> dict:
    collectors = collector_entries(await _source_counts(db, tenant_id))
    itsm = itsm_entries(await _itsm_counts(db, tenant_id))
    actions = action_entries()
    items = sorted(collectors, key=lambda e: (e["category"], e["display_name"].lower()))
    items += sorted(itsm, key=lambda e: (not e["implemented"], e["display_name"].lower()))
    items += sorted(actions, key=lambda e: e["display_name"].lower())
    return {
        "summary": {
            "total": len(items), "collectors": len(collectors), "itsm": len(itsm), "actions": len(actions),
            "implemented": sum(1 for e in items if e["implemented"]),
            "configured_types": sum(1 for e in items if e["configured"]),
        },
        "items": items,
    }


__all__ = ["COLLECTOR_REQUIRED_CONFIG", "integration_catalog"]
