"""
Google Cloud Platform (GCP) collector.

Discovers Compute Engine instances, VPC networks, subnetworks and firewall
rules, then normalizes them into the shared `NormalizedBundle` shape.

Connection details from `MonitoringSourceConnectionInfo`:
    config["project_id"]  -> GCP project ID (required)
    config["zones"]       -> optional list[str] zone filter (e.g. ["europe-west1-b"])
    config["zone"]        -> optional single zone (fallback when `zones` absent)
    password              -> service account JSON key, as a JSON string or dict
                             (encrypted at rest)
    api_key               -> alternative: short-lived OAuth2 access token

With neither `zones` nor `zone` set, every zone in the project is inventoried
via the aggregated list API, which is one call instead of one per zone.

Dependencies (NOT currently pinned in requirements.txt; imports are deferred so
this module still loads, and `authenticate()` returns an honest failure telling
the operator what to install):
    google-cloud-compute
    google-auth

SDK clients are synchronous, so calls are dispatched via `asyncio.to_thread`.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.collectors.base import (
    BaseCollector,
    CollectorConfigError,
    CollectorRegistry,
    ConnectionCheck,
    NormalizedBundle,
    NormalizedDevice,
)

logger = logging.getLogger(__name__)

#: Compute Engine instance statuses that mean the VM is actually running.
_RUNNING_STATUS = "RUNNING"
_SCOPE = "https://www.googleapis.com/auth/compute.readonly"


def _import_compute() -> Any:
    """Deferred import so the module loads without the GCP SDK installed."""
    try:
        from google.cloud import compute_v1  # type: ignore[import]
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise CollectorConfigError(
            "google-cloud-compute is required for the GCP collector. "
            "Install it via: pip install google-cloud-compute"
        ) from exc
    return compute_v1


@CollectorRegistry.register("gcp")
class GcpCollector(BaseCollector):
    display_name = "Google Cloud Platform"
    implemented = True

    # -- config helpers -------------------------------------------------------

    def _project_id(self) -> str:
        project_id = (self.source.config or {}).get("project_id")
        if not project_id:
            raise CollectorConfigError(
                "GCP config missing required field: config['project_id']"
            )
        return str(project_id)

    def _zones(self) -> list[str]:
        cfg = self.source.config or {}
        zones = cfg.get("zones")
        if isinstance(zones, str):
            zones = [zones]
        if zones:
            return [str(z) for z in zones if z]
        if cfg.get("zone"):
            return [str(cfg["zone"])]
        return []

    def _require_config(self) -> None:
        self._project_id()
        if not (self.source.password or self.source.api_key):
            raise CollectorConfigError(
                "GCP config missing required field(s): password (service "
                "account JSON) or api_key (OAuth2 access token)"
            )

    def _credentials(self) -> Any:
        """Build read-only GCP credentials from the configured secret."""
        # Validate and normalize the config BEFORE importing the SDK, so a
        # malformed key is reported as a config error even when google-auth is
        # not installed (and the config path stays testable without it).
        info = self.source.password
        if isinstance(info, str):
            try:
                info = json.loads(info)
            except ValueError as exc:
                raise CollectorConfigError(
                    "GCP password must be a service account JSON key "
                    f"(could not parse JSON: {exc})"
                ) from exc
        if not isinstance(info, dict) or "client_email" not in info:
            raise CollectorConfigError(
                "GCP service account JSON is missing 'client_email'"
            )

        try:
            from google.oauth2 import service_account  # type: ignore[import]
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise CollectorConfigError(
                "google-auth is required for the GCP collector. "
                "Install it via: pip install google-auth"
            ) from exc

        return service_account.Credentials.from_service_account_info(
            info, scopes=[_SCOPE]
        )

    # -- BaseCollector interface -----------------------------------------------

    async def authenticate(self) -> ConnectionCheck:
        """Resolve credentials and list one page of instances.

        A successful token refresh alone would not prove the project is
        reachable or that the service account has Compute read access, so this
        performs the cheapest real inventory call (`aggregated_list` with a
        page size of 1).
        """
        try:
            self._require_config()
            compute_v1 = _import_compute()
            credentials = self._credentials()
            project_id = self._project_id()

            def _probe() -> Any:
                client = compute_v1.InstancesClient(credentials=credentials)
                request = compute_v1.AggregatedListInstancesRequest(
                    project=project_id, max_results=1
                )
                for _ in client.aggregated_list(request=request):
                    break
                return True

            await asyncio.to_thread(_probe)
        except CollectorConfigError:
            raise
        except Exception as exc:  # noqa: BLE001
            return ConnectionCheck(
                success=False,
                message=f"GCP authentication failed: {type(exc).__name__}: {exc}",
            )
        return ConnectionCheck(
            success=True, message=f"Connected to GCP project {project_id}."
        )

    async def collect(self) -> dict[str, Any]:
        """Inventory VMs, networks, subnetworks and firewall rules.

        Best-effort per resource type: one failing call is recorded in
        `errors` and the remaining inventory is still returned.
        """
        compute_v1 = _import_compute()
        credentials = self._credentials()
        project_id = self._project_id()
        zones = self._zones()

        raw: dict[str, Any] = {
            "project_id": project_id,
            "zones": zones or ["-"],
            "instances": [],
            "networks": [],
            "subnetworks": [],
            "firewalls": [],
            "errors": [],
        }

        async def _safe(label: str, fn: Any, bucket: str) -> None:
            try:
                rows = await asyncio.to_thread(fn)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "GCP collect(): %s failed: project=%s source_id=%s error_type=%s",
                    label, project_id, self.source.id, type(exc).__name__,
                )
                raw["errors"].append(f"{label}: {type(exc).__name__}")
                return
            raw[bucket].extend(rows)

        async def instances() -> list[dict[str, Any]]:
            """Aggregated list across zones, flattening the zone grouping."""
            client = compute_v1.InstancesClient(credentials=credentials)
            out: list[dict[str, Any]] = []
            if zones:
                for zone in zones:
                    request = compute_v1.ListInstancesRequest(
                        project=project_id, zone=zone
                    )
                    for item in client.list(request=request):
                        row = _to_dict(item)
                        row["_zone"] = zone
                        out.append(row)
                return out
            request = compute_v1.AggregatedListInstancesRequest(project=project_id)
            for zone_name, scoped in client.aggregated_list(request=request):
                for item in getattr(scoped, "instances", None) or []:
                    row = _to_dict(item)
                    # "zones/europe-west1-b" -> "europe-west1-b"
                    row["_zone"] = str(zone_name).rsplit("/", 1)[-1]
                    out.append(row)
            return out

        async def networks() -> list[dict[str, Any]]:
            client = compute_v1.NetworksClient(credentials=credentials)
            request = compute_v1.ListNetworksRequest(project=project_id)
            return [_to_dict(i) for i in client.list(request=request)]

        async def subnetworks() -> list[dict[str, Any]]:
            client = compute_v1.SubnetworksClient(credentials=credentials)
            out: list[dict[str, Any]] = []
            if zones:
                # Regional resources: derive region from the zone name.
                regions = sorted({z.rsplit("-", 1)[0] for z in zones})
                for region in regions:
                    request = compute_v1.ListSubnetworksRequest(
                        project=project_id, region=region
                    )
                    for item in client.list(request=request):
                        row = _to_dict(item)
                        row["_region"] = region
                        out.append(row)
                return out
            request = compute_v1.AggregatedListSubnetworksRequest(
                project=project_id
            )
            for region_name, scoped in client.aggregated_list(request=request):
                for item in getattr(scoped, "subnetworks", None) or []:
                    row = _to_dict(item)
                    row["_region"] = str(region_name).rsplit("/", 1)[-1]
                    out.append(row)
            return out

        async def firewalls() -> list[dict[str, Any]]:
            client = compute_v1.FirewallsClient(credentials=credentials)
            request = compute_v1.ListFirewallsRequest(project=project_id)
            return [_to_dict(i) for i in client.list(request=request)]

        await _safe("instances.aggregatedList", instances, "instances")
        await _safe("networks.list", networks, "networks")
        await _safe("subnetworks.list", subnetworks, "subnetworks")
        await _safe("firewalls.list", firewalls, "firewalls")
        return raw

    def normalize(self, raw: dict[str, Any]) -> NormalizedBundle:
        """Pure mapping: raw GCP API objects -> NormalizedBundle.

        `external_id` is `gcp:<project>:<kind>:<name>` - GCP resource names are
        unique within a project and resource kind. Fields GCP does not return
        are left `None` rather than guessed.
        """
        project = raw.get("project_id") or "project"
        devices: list[NormalizedDevice] = []

        for row in raw.get("instances", []):
            name = row.get("name")
            if not name:
                continue
            zone = row.get("_zone") or _tail(row.get("zone")) or "unknown"
            status = str(row.get("status") or "").upper()
            interfaces = row.get("network_interfaces") or []
            primary = interfaces[0] if interfaces else {}
            devices.append(
                NormalizedDevice(
                    external_id=f"gcp:{project}:instance:{name}",
                    name=name,
                    device_type="cloud_resource",
                    vendor="Google",
                    model=_tail(row.get("machine_type")),
                    hostname=row.get("hostname"),
                    ip_address=primary.get("network_ip") or _nat_ip(primary),
                    status="up" if status == _RUNNING_STATUS else "down",
                    site_hint=zone,
                    custom_metadata={
                        "provider": "gcp",
                        "resource_kind": "compute_instance",
                        "project_id": project,
                        "zone": zone,
                        "state": status or None,
                        "network": _tail(primary.get("network")),
                        "subnetwork": _tail(primary.get("subnetwork")),
                        "self_link": row.get("self_link"),
                    },
                )
            )

        for row in raw.get("networks", []):
            name = row.get("name")
            if not name:
                continue
            routing = row.get("routing_config")
            devices.append(
                NormalizedDevice(
                    external_id=f"gcp:{project}:network:{name}",
                    name=name,
                    device_type="cloud_resource",
                    vendor="Google",
                    status="up",
                    custom_metadata={
                        "provider": "gcp",
                        "resource_kind": "vpc_network",
                        "project_id": project,
                        "auto_create_subnetworks": row.get(
                            "auto_create_subnetworks"
                        ),
                        "routing_mode": routing.get("routing_mode")
                        if isinstance(routing, dict) else None,
                        "self_link": row.get("self_link"),
                    },
                )
            )

        for row in raw.get("subnetworks", []):
            name = row.get("name")
            if not name:
                continue
            region = row.get("_region") or _tail(row.get("region")) or "unknown"
            devices.append(
                NormalizedDevice(
                    external_id=f"gcp:{project}:subnetwork:{name}",
                    name=name,
                    device_type="cloud_resource",
                    vendor="Google",
                    status="up",
                    site_hint=region,
                    custom_metadata={
                        "provider": "gcp",
                        "resource_kind": "subnetwork",
                        "project_id": project,
                        "region": region,
                        "cidr_block": row.get("ip_cidr_range"),
                        "network": _tail(row.get("network")),
                        "self_link": row.get("self_link"),
                    },
                )
            )

        for row in raw.get("firewalls", []):
            name = row.get("name")
            if not name:
                continue
            devices.append(
                NormalizedDevice(
                    external_id=f"gcp:{project}:firewall:{name}",
                    name=name,
                    device_type="cloud_resource",
                    vendor="Google",
                    status="down" if row.get("disabled") else "up",
                    custom_metadata={
                        "provider": "gcp",
                        "resource_kind": "firewall_rule",
                        "project_id": project,
                        "direction": row.get("direction"),
                        "priority": row.get("priority"),
                        "source_ranges": row.get("source_ranges"),
                        "allowed": [
                            a.get("IPProtocol")
                            for a in (row.get("allowed") or [])
                            if isinstance(a, dict)
                        ],
                        "network": _tail(row.get("network")),
                    },
                )
            )

        return NormalizedBundle(devices=devices)


def _to_dict(obj: Any) -> dict[str, Any]:
    """Convert a proto-plus/GCP SDK object into a plain dict.

    Falls back through the protobuf form and `to_dict()` so `normalize()` can
    stay a pure function over plain dicts (which is what the tests feed it).
    """
    if isinstance(obj, dict):
        return obj
    pb = getattr(obj, "_pb", None)
    if pb is not None:
        try:
            from google.protobuf.json_format import (  # type: ignore[import]
                MessageToDict,
            )

            return MessageToDict(pb, preserving_proto_field_name=True)
        except Exception:  # noqa: BLE001 - fall through to to_dict()
            pass
    to_dict = getattr(obj, "to_dict", None)
    if callable(to_dict):
        try:
            return to_dict()
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _tail(value: Any) -> str | None:
    """Last path segment of a GCP reference, e.g. `zones/europe-west1-b`."""
    if not value:
        return None
    return str(value).rsplit("/", 1)[-1]


def _nat_ip(interface: dict[str, Any]) -> str | None:
    """First access-config external IP, when the interface has one.

    Proto field names differ between the JSON form (`nat_i_p`, produced when
    `preserving_proto_field_name` is used) and the REST form (`natIP`), so
    keys are compared with underscores removed and lowercased.
    """
    for config in interface.get("access_configs") or []:
        if not isinstance(config, dict):
            continue
        for key, value in config.items():
            if not value:
                continue
            if key.lower().replace("_", "") in ("natip", "externalipv6"):
                return str(value)
    return None
