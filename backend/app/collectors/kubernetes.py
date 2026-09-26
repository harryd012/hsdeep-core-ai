"""
Kubernetes cluster collector.

Inventory a cluster (nodes, namespaces, deployments, services, ingresses)
through the Kubernetes API, then normalize it into the shared
`NormalizedBundle` shape.

Connection details from `MonitoringSourceConnectionInfo`:
    hostname              -> API server URL or host (e.g. k8s.example.com)
    port                  -> API server port (default 6443)
    api_key               -> bearer token (service account token)
    config["kubeconfig"]  -> alternative: full kubeconfig as a dict
    config["context"]     -> optional kubeconfig context name
    config["in_cluster"]  -> bool: use the pod's own service account
    verify_ssl            -> TLS verification (default True)

Dependency: `kubernetes` (the official Python client). NOT currently pinned in
requirements.txt; the import is deferred so this module still loads, and
`authenticate()` returns an honest failure telling the operator to install it.

The client is synchronous, so every call is dispatched through
`asyncio.to_thread` to keep the collection event loop responsive.
"""
from __future__ import annotations

import asyncio
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

_DEFAULT_PORT = 6443


@CollectorRegistry.register("kubernetes")
class KubernetesCollector(BaseCollector):
    display_name = "Kubernetes Cluster"
    implemented = True

    # -- config helpers -------------------------------------------------------

    def _require_config(self) -> None:
        cfg = self.source.config or {}
        if cfg.get("kubeconfig") or cfg.get("in_cluster"):
            return
        if not (self.source.hostname or self.source.ip_address):
            raise CollectorConfigError(
                "Kubernetes config missing required field(s): "
                "hostname (or ip_address), or config['kubeconfig'], "
                "or config['in_cluster']=true"
            )

    def _client(self) -> Any:
        """Build a Kubernetes API client from the configured credentials."""
        # Validate config BEFORE importing the SDK so a missing/incomplete
        # config is reported as a config error even when the client is absent.
        self._require_config()
        try:
            from kubernetes import client, config  # type: ignore[import]
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise CollectorConfigError(
                "kubernetes is required for the Kubernetes collector. "
                "Install it via: pip install kubernetes"
            ) from exc

        cfg = self.source.config or {}

        if cfg.get("in_cluster"):
            config.load_incluster_config()
        elif cfg.get("kubeconfig"):
            config.load_kube_config_from_dict(
                cfg["kubeconfig"], context=cfg.get("context")
            )
        else:
            host = self.source.hostname or self.source.ip_address
            configuration = client.Configuration()
            scheme = "https" if self.source.verify_ssl else "http"
            configuration.host = (
                f"{scheme}://{host}:{self.source.port or _DEFAULT_PORT}"
            )
            configuration.verify_ssl = bool(self.source.verify_ssl)
            if self.source.api_key:
                configuration.api_key = {
                    "authorization": f"Bearer {self.source.api_key}"
                }
            client.Configuration.set_default(configuration)

        return client

    # -- BaseCollector interface -----------------------------------------------

    async def authenticate(self) -> ConnectionCheck:
        """Read the API server version - the cheapest authenticated call."""
        try:
            self._require_config()
            k8s = self._client()

            info = await asyncio.to_thread(lambda: k8s.VersionApi().get_code())
        except CollectorConfigError:
            raise
        except Exception as exc:  # noqa: BLE001
            return ConnectionCheck(
                success=False,
                message=(
                    f"Kubernetes authentication failed: {type(exc).__name__}: {exc}"
                ),
            )
        git_version = getattr(info, "git_version", None) or "unknown"
        return ConnectionCheck(
            success=True, message=f"Connected to Kubernetes API ({git_version})."
        )


    async def collect(self) -> dict[str, Any]:
        """Inventory the cluster. Best-effort per resource kind."""
        k8s = self._client()
        raw: dict[str, Any] = {
            "cluster": self.source.hostname or self.source.ip_address,
            "nodes": [],
            "namespaces": [],
            "deployments": [],
            "services": [],
            "ingresses": [],
            "errors": [],
        }

        async def _safe(label: str, fn: Any, bucket: str) -> None:
            try:
                rows = await asyncio.to_thread(fn)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Kubernetes collect(): %s failed: source_id=%s error_type=%s",
                    label, self.source.id, type(exc).__name__,
                )
                raw["errors"].append(f"{label}: {type(exc).__name__}")
                return
            raw[bucket].extend(_as_dicts(rows))

        def core() -> Any:
            return k8s.CoreV1Api()

        def apps() -> Any:
            return k8s.AppsV1Api()

        def networking() -> Any:
            return k8s.NetworkingV1Api()

        await _safe("list_node", lambda: core().list_node(), "nodes")
        await _safe("list_namespace", lambda: core().list_namespace(), "namespaces")
        await _safe(
            "list_deployment_for_all_namespaces",
            lambda: apps().list_deployment_for_all_namespaces(),
            "deployments",
        )
        await _safe(
            "list_service_for_all_namespaces",
            lambda: core().list_service_for_all_namespaces(),
            "services",
        )
        await _safe(
            "list_ingress_for_all_namespaces",
            lambda: networking().list_ingress_for_all_namespaces(),
            "ingresses",
        )
        return raw

    def normalize(self, raw: dict[str, Any]) -> NormalizedBundle:
        """Pure mapping: raw Kubernetes objects -> NormalizedBundle.

        Kubernetes `metadata.uid` is the stable, cluster-global identity, so
        `external_id` is `<cluster>:<kind>:<uid>`. Objects are emitted as
        `cloud_resource` devices, and status is derived from each kind's own
        status fields - left `unknown` when the API reports none.
        """
        cluster = raw.get("cluster") or "cluster"
        devices: list[NormalizedDevice] = []

        for obj in raw.get("nodes", []):
            name = _meta(obj, "name")
            if not name:
                continue
            ready = _node_ready(obj)
            status = (
                "up" if ready is True
                else ("down" if ready is False else "unknown")
            )
            node_info = (obj.get("status") or {}).get("node_info") or {}
            devices.append(
                _device(
                    cluster, "node", obj, name, status=status,
                    extra={
                        "kubelet_version": node_info.get("kubelet_version"),
                        "container_runtime": node_info.get(
                            "container_runtime_version"
                        ),
                        "internal_ip": _node_ip(obj),
                    },
                )
            )

        for obj in raw.get("namespaces", []):
            name = _meta(obj, "name")
            if not name:
                continue
            devices.append(
                _device(
                    cluster, "namespace", obj, name,
                    status=_phase_status((obj.get("status") or {}).get("phase")),
                )
            )

        for obj in raw.get("deployments", []):
            name = _meta(obj, "name")
            if not name:
                continue
            status_obj = obj.get("status") or {}
            desired = (obj.get("spec") or {}).get("replicas")
            available = status_obj.get("available_replicas") or 0
            devices.append(
                _device(
                    cluster, "deployment", obj, name,
                    status=(
                        "up" if desired and available >= desired
                        else ("warning" if available else "down")
                    ),
                    extra={
                        "namespace": _meta(obj, "namespace"),
                        "desired_replicas": desired,
                        "available_replicas": available,
                    },
                )
            )

        for obj in raw.get("services", []):
            name = _meta(obj, "name")
            if not name:
                continue
            spec = obj.get("spec") or {}
            devices.append(
                _device(
                    cluster, "service", obj, name, status="up",
                    extra={
                        "namespace": _meta(obj, "namespace"),
                        "service_type": spec.get("type"),
                        "cluster_ip": spec.get("cluster_ip"),
                        "ports": [p.get("port") for p in (spec.get("ports") or [])],
                    },
                )
            )

        for obj in raw.get("ingresses", []):
            name = _meta(obj, "name")
            if not name:
                continue
            devices.append(
                _device(
                    cluster, "ingress", obj, name, status="up",
                    extra={
                        "namespace": _meta(obj, "namespace"),
                        "hosts": [
                            r.get("host")
                            for r in ((obj.get("spec") or {}).get("rules") or [])
                        ],
                    },
                )
            )

        return NormalizedBundle(devices=devices)


def _as_dicts(rows: Any) -> list[dict[str, Any]]:
    """Kubernetes list endpoints return an object whose `.items` are models.

    Handles the model form and the API's `to_dict()` form so `normalize()`
    stays a pure, SDK-free function.
    """
    items = getattr(rows, "items", None)
    if items is None:
        items = rows if isinstance(rows, list) else []
    out: list[dict[str, Any]] = []
    for item in items or []:
        if isinstance(item, dict):
            out.append(item)
        elif hasattr(item, "to_dict"):
            out.append(item.to_dict())
    return out


def _meta(obj: dict[str, Any], key: str) -> Any:
    return (obj.get("metadata") or {}).get(key)


def _device(
    cluster: str,
    kind: str,
    obj: dict[str, Any],
    name: str,
    *,
    status: str,
    extra: dict[str, Any] | None = None,
) -> NormalizedDevice:
    """One NormalizedDevice for a Kubernetes object."""
    uid = _meta(obj, "uid") or name
    metadata: dict[str, Any] = {
        "provider": "kubernetes",
        "resource_kind": kind,
        "cluster": cluster,
        "namespace": _meta(obj, "namespace"),
    }
    if extra:
        metadata.update({k: v for k, v in extra.items() if v not in (None, [])})
    return NormalizedDevice(
        external_id=f"{cluster}:{kind}:{uid}",
        name=name,
        device_type="cloud_resource",
        vendor="Kubernetes",
        hostname=name,
        status=status,
        custom_metadata=metadata,
    )


def _node_ready(obj: dict[str, Any]) -> bool | None:
    """True/False from the node's Ready condition; None when unreported."""
    for condition in (obj.get("status") or {}).get("conditions") or []:
        if condition.get("type") == "Ready":
            value = str(condition.get("status") or "").lower()
            if value in ("true", "false"):
                return value == "true"
    return None


def _node_ip(obj: dict[str, Any]) -> str | None:
    for address in (obj.get("status") or {}).get("addresses") or []:
        if address.get("type") == "InternalIP":
            return address.get("address")
    return None


def _phase_status(phase: Any) -> str:
    """Namespace phase -> normalized status. Unknown phases stay `unknown`."""
    return {"active": "up", "terminating": "warning"}.get(
        str(phase or "").lower(), "unknown"
    )
