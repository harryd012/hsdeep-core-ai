"""
Platform observability deployment (Phase 3) and the rest of Phase 2.

Static checks on deployment/observability/ (no Docker needed):
  * pinned images, UIs bound to 127.0.0.1, persistent volumes, health checks;
  * secrets only as mounted files, never inline or as environment values;
  * Grafana: no anonymous access, no sign-up, provisioned read-only datasource;
  * Prometheus: token from credentials_file; production scrapes each backend
    pod by role;
  * the K8s NetworkPolicy admits only Prometheus to the backend ports;
  * the README's metric catalogue lists every hsdeep_* metric the code emits.

Runtime checks: the /health check records dependency gauges, the DB pool
collector exposes pool state, and ServiceNow metrics carry no connection_id.
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path

import httpx
import pytest
import yaml
from prometheus_client import REGISTRY, generate_latest

ROOT = Path(__file__).resolve().parents[2]
OBS = ROOT / "deployment" / "observability"


def _yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class TestCompose:
    compose = _yaml(OBS / "docker-compose.yml")

    def test_images_pinned(self):
        for name, svc in self.compose["services"].items():
            image = svc["image"]
            default = re.search(r":-([^}]+)\}", image)
            tag = default.group(1) if default else image.rsplit(":", 1)[-1]
            assert tag and tag != "latest" and re.match(r"v?\d+\.\d+\.\d+$", tag), (name, image)

    def test_ports_private_and_volumes_persistent(self):
        for name, svc in self.compose["services"].items():
            for port in svc.get("ports", []):
                assert port.startswith("127.0.0.1:"), (name, port)
            assert "healthcheck" in svc, name
        assert set(self.compose["volumes"]) == {"prometheus-data", "grafana-data"}

    def test_secrets_are_read_only_files(self):
        prom = self.compose["services"]["prometheus"]
        graf = self.compose["services"]["grafana"]
        assert "./secrets/metrics_token:/run/secrets/metrics_token:ro" in prom["volumes"]
        assert "./secrets/grafana_admin_password:/run/secrets/grafana_admin_password:ro" in graf["volumes"]
        env = graf["environment"]
        assert env["GF_SECURITY_ADMIN_PASSWORD__FILE"] == "/run/secrets/grafana_admin_password"
        assert "GF_SECURITY_ADMIN_PASSWORD" not in env
        assert env["GF_AUTH_ANONYMOUS_ENABLED"] == "false"
        assert env["GF_USERS_ALLOW_SIGN_UP"] == "false"

    def test_secrets_dir_ignored_and_empty_in_git(self):
        ignore = (OBS / "secrets" / ".gitignore").read_text(encoding="utf-8").splitlines()
        assert "*" in ignore and "!.gitignore" in ignore
        assert "METRICS_BEARER_TOKEN=" not in (OBS / ".env.example").read_text(encoding="utf-8")


class TestPrometheusConfig:
    def _backend_job(self, name: str) -> dict:
        cfg = _yaml(OBS / "prometheus" / name)
        assert "/etc/prometheus/rules/*.yml" in cfg["rule_files"]
        job = next(j for j in cfg["scrape_configs"] if j["job_name"] == "hsdeep-backend")
        assert job["metrics_path"] == "/metrics"
        assert job["authorization"] == {"type": "Bearer", "credentials_file": "/run/secrets/metrics_token"}
        assert "credentials" not in job["authorization"]
        return job

    def test_development_scrapes_host_backend(self):
        job = self._backend_job("prometheus.yml")
        assert job["static_configs"][0]["targets"] == ["host.docker.internal:8000"]

    def test_production_scrapes_each_role_by_pod(self):
        job = self._backend_job("prometheus.production.yml")
        sd = job["kubernetes_sd_configs"][0]
        assert sd["role"] == "pod" and sd["namespaces"]["names"] == ["hsdeep"]
        relabel = job["relabel_configs"]
        assert any(r.get("regex") == "hsdeep-backend" and r.get("action") == "keep" for r in relabel)
        assert any(r.get("target_label") == "job" and
                   r["source_labels"] == ["__meta_kubernetes_pod_label_app_kubernetes_io_component"] for r in relabel)


class TestGrafanaProvisioning:
    def test_datasource_private_and_read_only(self):
        ds = _yaml(OBS / "grafana" / "provisioning" / "datasources" / "prometheus.yml")["datasources"][0]
        assert ds["url"] == "http://prometheus:9090"
        assert ds["editable"] is False and ds["access"] == "proxy"

    def test_dashboards_from_files_only(self):
        prov = _yaml(OBS / "grafana" / "provisioning" / "dashboards" / "dashboards.yml")["providers"][0]
        assert prov["allowUiUpdates"] is False and prov["disableDeletion"] is True
        assert prov["options"]["path"] == "/var/lib/grafana/dashboards"


def test_network_policy_admits_only_prometheus():
    docs = [d for d in yaml.safe_load_all((ROOT / "deployment" / "k8s" / "10-networkpolicy.yaml")
                                          .read_text(encoding="utf-8")) if d]
    pol = next(d for d in docs if d["metadata"]["name"] == "backend-metrics-scrape")
    assert pol["spec"]["policyTypes"] == ["Ingress"]
    (rule,) = pol["spec"]["ingress"]
    (peer,) = rule["from"]  # one peer: namespace AND pod selector must both match
    assert peer["namespaceSelector"]["matchLabels"] == {"kubernetes.io/metadata.name": "monitoring"}
    assert peer["podSelector"]["matchLabels"] == {"app.kubernetes.io/name": "prometheus"}
    assert sorted(p["port"] for p in rule["ports"]) == [8000, 8001, 8002]


def test_readme_catalogue_lists_every_emitted_metric():
    import app.main  # noqa: F401 - loads every platform metric module

    readme = (OBS / "README.md").read_text(encoding="utf-8")
    names = {m.name for m in REGISTRY.collect() if m.name.startswith("hsdeep_")}
    assert names, "no hsdeep_ metrics registered"
    for name in sorted(names):
        short = name.replace("hsdeep_db_pool", "")
        assert name in readme or (name.startswith("hsdeep_db_pool") and short in readme), name


class TestDependencyAndPoolMetrics:
    async def test_health_check_records_dependency_gauges(self, monkeypatch):
        import app.api.routes.health as health

        async def _db_up():
            return True

        async def _redis_down():
            return "unreachable"

        async def _no_ollama():
            return "unavailable"

        monkeypatch.setattr(health, "check_db_connection", _db_up)
        monkeypatch.setattr(health, "redis_health", _redis_down)
        monkeypatch.setattr(health, "_check_ollama", _no_ollama)
        await health.health()
        assert REGISTRY.get_sample_value("hsdeep_dependency_up", {"dependency": "database"}) == 1
        assert REGISTRY.get_sample_value("hsdeep_dependency_up", {"dependency": "redis"}) == 0
        assert REGISTRY.get_sample_value(
            "hsdeep_dependency_last_check_timestamp_seconds", {"dependency": "redis"}) > 0

    def test_pool_collector_exposes_state(self):
        import app.core.metrics  # noqa: F401

        text = generate_latest().decode()
        for name in ("hsdeep_db_pool_size", "hsdeep_db_pool_checked_out", "hsdeep_db_pool_saturation_ratio"):
            assert re.search(rf"^{name} \d", text, re.M), name


def test_servicenow_metrics_carry_no_connection_id():
    from app.services.servicenow import metrics as sn

    with sn.measure_webhook_latency(uuid.uuid4()):
        pass
    sn.failed_sync_total.labels(job_type="incident_push").inc()
    for collector in (sn.push_latency_seconds, sn.pull_latency_seconds, sn.webhook_latency_seconds,
                      sn.retry_total, sn.conflict_total, sn.failed_sync_total, sn.queue_depth):
        assert "connection_id" not in collector._labelnames, collector
