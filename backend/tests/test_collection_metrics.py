"""Prometheus wiring around collection and alerting (app.core.metrics).

Run, phase, skip and tick recording is pinned in test_platform_metrics_phase2.
This file pins the rest: an opened alert is counted once by origin and
severity, and a metrics failure never breaks collection or alerting.
"""
from __future__ import annotations

import enum
from types import SimpleNamespace

from prometheus_client import REGISTRY

from app.core import metrics as platform_metrics
from app.services import alert_service
from tests.test_orchestrator import _FakeCollector, _ns_from_source
from tests.test_platform_metrics_phase2 import _run


def _sample(name: str, **labels) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


class _Severity(enum.Enum):
    CRITICAL = "critical"


def test_opened_alert_is_counted_by_origin_and_severity():
    before = _sample("hsdeep_alert_created_total", source="metrics_test_origin", severity="critical")
    alert_service._observe_opened(SimpleNamespace(source="metrics_test_origin", severity="critical"))
    # An enum severity is recorded by its value, not its repr.
    alert_service._observe_opened(SimpleNamespace(source="metrics_test_origin", severity=_Severity.CRITICAL))
    assert _sample("hsdeep_alert_created_total", source="metrics_test_origin", severity="critical") == before + 2


def test_an_alert_metric_failure_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("registry broken")

    monkeypatch.setattr(platform_metrics, "observe_alert_created", boom)
    alert_service._observe_opened(SimpleNamespace(source="x", severity="x"))


async def test_a_metrics_failure_never_fails_a_run(tenant, monitoring_source_factory, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("registry broken")

    for name in ("observe_collection_run", "observe_collection_skipped", "observe_collector_phase", "observe_collection_failure"):
        monkeypatch.setattr(platform_metrics, name, boom)

    source = _ns_from_source(await monitoring_source_factory())
    outcome = await _run(source, _FakeCollector())
    assert outcome.run_record.status == "success"

    failing = _FakeCollector()
    failing._collect_side_effect = RuntimeError("vendor timeout")
    outcome = await _run(source, failing)
    assert outcome.run_record.status == "error"
