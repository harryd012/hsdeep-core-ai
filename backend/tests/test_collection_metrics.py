"""Prometheus wiring for the collector pipeline (app.core.metrics).

A finished run is recorded once with its real status, duration and item
counts; a skipped run counts as skipped; an opened alert is counted by origin
and severity; and a metrics failure never breaks collection or alerting.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from prometheus_client import REGISTRY

from app.core import metrics as prom
from app.models.collector_run import CollectorRun
from app.services import alert_service
from app.services.monitoring import collection_orchestrator as orch


def _sample(name: str, **labels) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def _run(status: str, seconds: float = 3.0, **counts) -> CollectorRun:
    start = datetime.now(timezone.utc)
    return CollectorRun(status=status, started_at=start, finished_at=start + timedelta(seconds=seconds), **counts)


def test_finished_run_is_recorded_once_with_its_status_and_counts():
    st = "metrics_test_snmp"
    orch._observe_run(st, _run("success", seconds=3.0, devices_upserted=2, sensors_upserted=5, alerts_upserted=0))
    orch._observe_run(st, _run("error", seconds=1.0))
    assert _sample("hsdeep_collection_run_total", source_type=st, status="success") == 1
    assert _sample("hsdeep_collection_run_total", source_type=st, status="error") == 1
    assert _sample("hsdeep_collection_run_duration_seconds_sum", source_type=st, status="success") == 3.0
    assert _sample("hsdeep_collection_items_upserted_total", entity_type="device", source_type=st) == 2
    assert _sample("hsdeep_collection_items_upserted_total", entity_type="sensor", source_type=st) == 5


def test_skipped_run_counts_as_skipped_not_as_a_run():
    st = "metrics_test_skip"
    orch._observe_run(st, _run("skipped"))
    assert _sample("hsdeep_collection_skipped_total", source_type=st) == 1
    assert _sample("hsdeep_collection_run_total", source_type=st, status="skipped") == 0


def test_phase_and_alert_metrics():
    orch._observe_phase("metrics_test_phase", "collect", 0.0)
    assert _sample("hsdeep_collector_execution_total", source_type="metrics_test_phase", phase="collect") == 1
    alert_service._observe_created(SimpleNamespace(source="metrics_test_origin", severity="critical"))
    assert _sample("hsdeep_alert_created_total", source="metrics_test_origin", severity="critical") == 1


def test_a_metrics_failure_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("registry broken")

    for name in ("observe_collection_run", "observe_collection_skipped", "observe_collector_phase", "observe_alert_created"):
        monkeypatch.setattr(prom, name, boom)
    orch._observe_run("x", _run("success"))
    orch._observe_run("x", _run("skipped"))
    orch._observe_phase("x", "collect", 0.0)
    alert_service._observe_created(SimpleNamespace(source="x", severity="x"))
