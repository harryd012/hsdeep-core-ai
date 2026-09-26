"""
Unit tests for KubernetesCollector.normalize() and its config validation.

Pure, no I/O and no cluster. Fixtures use the dict form the API's `to_dict()`
produces, which is also what `collect()` stores after `_as_dicts()`.
"""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from app.collectors.base import CollectorConfigError, NormalizedBundle
from app.collectors.kubernetes import (
    KubernetesCollector,
    _as_dicts,
    _node_ready,
    _phase_status,
)

_EMPTY_RAW = {
    "cluster": "k8s.example.com",
    "nodes": [],
    "namespaces": [],
    "deployments": [],
    "services": [],
    "ingresses": [],
    "errors": [],
}

_CLUSTER_RAW = {
    "cluster": "k8s.example.com",
    "nodes": [
        {
            "metadata": {"name": "node-1", "uid": "uid-node-1"},
            "status": {
                "conditions": [{"type": "Ready", "status": "True"}],
                "node_info": {
                    "kubelet_version": "v1.29.2",
                    "container_runtime_version": "containerd://1.7.13",
                },
                "addresses": [{"type": "InternalIP", "address": "10.0.0.10"}],
            },
        },
        {
            "metadata": {"name": "node-2", "uid": "uid-node-2"},
            "status": {
                "conditions": [{"type": "Ready", "status": "False"}],
                "addresses": [{"type": "InternalIP", "address": "10.0.0.11"}],
            },
        },
        {
            # No conditions at all -> status must be honestly "unknown".
            "metadata": {"name": "node-3", "uid": "uid-node-3"},
            "status": {},
        },
    ],
    "namespaces": [
        {
            "metadata": {"name": "prod", "uid": "uid-ns-prod"},
            "status": {"phase": "Active"},
        },
        {
            "metadata": {"name": "old", "uid": "uid-ns-old"},
            "status": {"phase": "Terminating"},
        },
    ],
    "deployments": [
        {
            "metadata": {"name": "api", "namespace": "prod", "uid": "uid-dep-api"},
            "spec": {"replicas": 3},
            "status": {"available_replicas": 3},
        },
        {
            "metadata": {"name": "web", "namespace": "prod", "uid": "uid-dep-web"},
            "spec": {"replicas": 3},
            "status": {"available_replicas": 1},
        },
        {
            "metadata": {"name": "dead", "namespace": "prod", "uid": "uid-dep-dead"},
            "spec": {"replicas": 2},
            "status": {"available_replicas": 0},
        },
    ],
    "services": [
        {
            "metadata": {"name": "api-svc", "namespace": "prod", "uid": "uid-svc-api"},
            "spec": {
                "type": "ClusterIP",
                "cluster_ip": "10.96.0.1",
                "ports": [{"port": 443}],
            },
        }
    ],
    "ingresses": [
        {
            "metadata": {"name": "web-ing", "namespace": "prod", "uid": "uid-ing-web"},
            "spec": {"rules": [{"host": "web.example.com"}]},
        }
    ],
    "errors": [],
}


def _source(config=None, hostname="k8s.example.com"):
    """Stand-in for MonitoringSourceConnectionInfo (no secrets needed)."""
    return SimpleNamespace(
        id=None,
        tenant_id=None,
        name="k8s-test",
        source_type="kubernetes",
        hostname=hostname,
        ip_address=None,
        port=6443,
        protocol="https",
        snmp_version=None,
        username=None,
        api_key="sa-token",
        password=None,
        config=config or {},
        verify_ssl=True,
    )


class TestKubernetesNormalize:

    def test_empty_payload_returns_empty_bundle(self):
        bundle = KubernetesCollector(source=None).normalize(_EMPTY_RAW)
        assert isinstance(bundle, NormalizedBundle)
        assert bundle.devices == []

    def test_ready_node_maps_to_up(self):
        bundle = KubernetesCollector(source=None).normalize(_CLUSTER_RAW)
        node = {d.name: d for d in bundle.devices}["node-1"]
        assert node.external_id == "k8s.example.com:node:uid-node-1"
        assert node.device_type == "cloud_resource"
        assert node.vendor == "Kubernetes"
        assert node.status == "up"
        assert node.custom_metadata["kubelet_version"] == "v1.29.2"
        assert node.custom_metadata["internal_ip"] == "10.0.0.10"

    def test_not_ready_node_maps_to_down(self):
        bundle = KubernetesCollector(source=None).normalize(_CLUSTER_RAW)
        assert {d.name: d for d in bundle.devices}["node-2"].status == "down"

    def test_node_without_ready_condition_is_unknown(self):
        """No observed condition must not be reported as an outage."""
        bundle = KubernetesCollector(source=None).normalize(_CLUSTER_RAW)
        assert {d.name: d for d in bundle.devices}["node-3"].status == "unknown"

    def test_namespace_phase_maps(self):
        bundle = KubernetesCollector(source=None).normalize(_CLUSTER_RAW)
        by_name = {d.name: d for d in bundle.devices}
        assert by_name["prod"].status == "up"
        assert by_name["old"].status == "warning"

    def test_deployment_status_from_replica_counts(self):
        bundle = KubernetesCollector(source=None).normalize(_CLUSTER_RAW)
        by_name = {d.name: d for d in bundle.devices}
        assert by_name["api"].status == "up"        # 3/3 available
        assert by_name["web"].status == "warning"   # 1/3 available
        assert by_name["dead"].status == "down"     # 0/2 available

    def test_deployment_metadata_carries_replicas_and_namespace(self):
        bundle = KubernetesCollector(source=None).normalize(_CLUSTER_RAW)
        api = {d.name: d for d in bundle.devices}["api"]
        assert api.custom_metadata["namespace"] == "prod"
        assert api.custom_metadata["desired_replicas"] == 3
        assert api.custom_metadata["available_replicas"] == 3

    def test_service_and_ingress_are_emitted(self):
        bundle = KubernetesCollector(source=None).normalize(_CLUSTER_RAW)
        by_id = {d.external_id: d for d in bundle.devices}

        svc = by_id["k8s.example.com:service:uid-svc-api"]
        assert svc.name == "api-svc"
        assert svc.custom_metadata["cluster_ip"] == "10.96.0.1"
        assert svc.custom_metadata["ports"] == [443]

        ing = by_id["k8s.example.com:ingress:uid-ing-web"]
        assert ing.custom_metadata["hosts"] == ["web.example.com"]

    def test_objects_without_name_are_skipped(self):
        raw = {
            "nodes": [{"status": {}}],
            "namespaces": [{"status": {"phase": "Active"}}],
            "deployments": [{"spec": {"replicas": 1}}],
            "services": [{"spec": {"type": "ClusterIP"}}],
            "ingresses": [{"spec": {"rules": []}}],
        }
        assert KubernetesCollector(source=None).normalize(raw).devices == []

    def test_uid_missing_falls_back_to_name(self):
        """A cluster that omits metadata.uid must still produce a stable key."""
        raw = {
            "cluster": "c1",
            "nodes": [{
                "metadata": {"name": "n1"},
                "status": {"conditions": [{"type": "Ready", "status": "True"}]},
            }],
        }
        bundle = KubernetesCollector(source=None).normalize(raw)
        assert bundle.devices[0].external_id == "c1:node:n1"

    def test_normalize_does_not_mutate_input(self):
        raw = copy.deepcopy(_CLUSTER_RAW)
        KubernetesCollector(source=None).normalize(raw)
        assert raw == _CLUSTER_RAW


class TestKubernetesHelpers:

    def test_as_dicts_accepts_model_objects(self):
        class _Model:
            def __init__(self, name):
                self.name = name

            def to_dict(self):
                return {"metadata": {"name": self.name}}

        class _List:
            def __init__(self, items):
                self.items = items

        rows = _as_dicts(_List([_Model("a"), _Model("b")]))
        assert rows == [{"metadata": {"name": "a"}}, {"metadata": {"name": "b"}}]

    def test_as_dicts_accepts_plain_dicts_and_empty(self):
        assert _as_dicts([{"a": 1}]) == [{"a": 1}]
        assert _as_dicts(None) == []
        assert _as_dicts(object()) == []

    def test_node_ready_reads_condition(self):
        assert _node_ready(
            {"status": {"conditions": [{"type": "Ready", "status": "True"}]}}
        ) is True
        assert _node_ready(
            {"status": {"conditions": [{"type": "Ready", "status": "False"}]}}
        ) is False

    def test_node_ready_none_when_absent(self):
        assert _node_ready({}) is None
        assert _node_ready({"status": {"conditions": []}}) is None

    def test_phase_status_mapping(self):
        assert _phase_status("Active") == "up"
        assert _phase_status("Terminating") == "warning"
        assert _phase_status("SomethingNew") == "unknown"
        assert _phase_status(None) == "unknown"


class TestKubernetesConfig:

    def test_missing_endpoint_raises_config_error(self):
        collector = KubernetesCollector(source=_source(hostname=None))
        with pytest.raises(CollectorConfigError) as exc:
            collector._require_config()
        assert "hostname" in str(exc.value)

    def test_kubeconfig_satisfies_require_config(self):
        collector = KubernetesCollector(
            source=_source(hostname=None, config={"kubeconfig": {"clusters": []}})
        )
        collector._require_config()  # must not raise

    def test_in_cluster_satisfies_require_config(self):
        collector = KubernetesCollector(
            source=_source(hostname=None, config={"in_cluster": True})
        )
        collector._require_config()  # must not raise
