"""Cloud Operations KPI service tests.

Pure unit tests over fake repositories - no database, no I/O. They pin the
service's contract: three measured cards read through the authoritative
repository methods, three cards explicitly unavailable with a reason, and the
tenant identity is forwarded to every repository call (never re-derived from a
client-supplied value - that is the route's job, see dashboard.py).
"""
from __future__ import annotations

import uuid

from app.services.cloud_operations_service import CloudOperationsService


class _FakeDeviceRepo:
    def __init__(
        self,
        cloud_resource_count: int = 0,
        states: dict[str, int] | None = None,
    ):
        self.cloud_resource_count = cloud_resource_count
        self.states = states or {
            "up": 0,
            "down": 0,
            "stale": 0,
            "unknown": 0,
            "total": 0,
        }
        self.tenant_ids: list[uuid.UUID] = []

    async def count(self, tenant_id, *, device_type=None, **kwargs) -> int:
        self.tenant_ids.append(tenant_id)
        return self.cloud_resource_count if device_type == "cloud_resource" else 0

    async def counts_by_state(self, tenant_id, *, now, freshness_seconds) -> dict[str, int]:
        self.tenant_ids.append(tenant_id)
        return dict(self.states)


class _FakeIncidentRepo:
    def __init__(self, active_total: int = 0, critical_active: int = 0):
        self.active_total = active_total
        self.critical_active = critical_active
        self.tenant_ids: list[uuid.UUID] = []
        self.statuses: list[str] | None = None

    async def active_counts(self, tenant_id, *, statuses) -> tuple[int, int]:
        self.tenant_ids.append(tenant_id)
        self.statuses = list(statuses)
        return (self.active_total, self.critical_active)


def _svc(device_repo=None, incident_repo=None) -> CloudOperationsService:
    return CloudOperationsService(
        device_repo=device_repo or _FakeDeviceRepo(),
        incident_repo=incident_repo or _FakeIncidentRepo(),
    )


class TestCloudResources:
    async def test_counts_cloud_resource_devices_only(self):
        brief = await _svc(
            device_repo=_FakeDeviceRepo(cloud_resource_count=2)
        ).get(uuid.uuid4())
        assert brief.cloud_resources.value == 2
        assert brief.cloud_resources.unavailable is False

    async def test_zero_is_a_real_zero_not_unavailable(self):
        brief = await _svc(device_repo=_FakeDeviceRepo(cloud_resource_count=0)).get(
            uuid.uuid4()
        )
        assert brief.cloud_resources.value == 0
        assert brief.cloud_resources.unavailable is False


class TestResourceHealth:
    async def test_health_buckets_from_device_state(self):
        brief = await _svc(
            device_repo=_FakeDeviceRepo(
                states={"up": 5, "down": 1, "stale": 3, "unknown": 1, "total": 10}
            )
        ).get(uuid.uuid4())
        h = brief.resource_health.value
        assert h == {"online": 5, "offline": 1, "stale": 3, "unknown": 1, "total": 10}
        assert brief.resource_health.unavailable is False

    async def test_empty_estate_is_a_real_zero_total(self):
        brief = await _svc(device_repo=_FakeDeviceRepo()).get(uuid.uuid4())
        assert brief.resource_health.value["total"] == 0
        assert brief.resource_health.unavailable is False


class TestActiveIncidents:
    async def test_active_incidents_show_total_with_critical_detail(self):
        brief = await _svc(
            incident_repo=_FakeIncidentRepo(active_total=12, critical_active=3)
        ).get(uuid.uuid4())
        assert brief.active_incidents.value == 12
        assert brief.active_incidents.detail == {"total": 12, "critical": 3}
        assert brief.active_incidents.unavailable is False

    async def test_active_statuses_are_the_lifecycle_set(self):
        incident_repo = _FakeIncidentRepo(active_total=1)
        await _svc(incident_repo=incident_repo).get(uuid.uuid4())
        assert incident_repo.statuses == ["open", "acknowledged", "investigating"]

    async def test_zero_incidents_is_a_real_zero(self):
        brief = await _svc(incident_repo=_FakeIncidentRepo(active_total=0)).get(
            uuid.uuid4()
        )
        assert brief.active_incidents.value == 0
        assert brief.active_incidents.unavailable is False


class TestUnavailableCards:
    async def test_cloud_spend_is_unavailable_with_reason(self):
        brief = await _svc().get(uuid.uuid4())
        assert brief.cloud_spend.unavailable is True
        assert brief.cloud_spend.value is None
        assert "billing" in brief.cloud_spend.unavailable_reason.lower()

    async def test_policy_compliance_is_unavailable_with_reason(self):
        brief = await _svc().get(uuid.uuid4())
        assert brief.policy_compliance.unavailable is True
        assert brief.policy_compliance.value is None
        assert "policy" in brief.policy_compliance.unavailable_reason.lower()

    async def test_backup_readiness_is_unavailable_with_reason(self):
        brief = await _svc().get(uuid.uuid4())
        assert brief.backup_readiness.unavailable is True
        assert brief.backup_readiness.value is None
        assert "backup" in brief.backup_readiness.unavailable_reason.lower()


class TestTenantIsolation:
    async def test_tenant_id_is_forwarded_to_every_repository(self):
        tid = uuid.uuid4()
        device_repo = _FakeDeviceRepo(cloud_resource_count=1)
        incident_repo = _FakeIncidentRepo(active_total=1)
        await _svc(device_repo=device_repo, incident_repo=incident_repo).get(tid)
        assert device_repo.tenant_ids == [tid, tid]  # count + counts_by_state
        assert incident_repo.tenant_ids == [tid]


class TestFailurePropagation:
    async def test_device_repo_failure_propagates(self):
        class _FailingRepo(_FakeDeviceRepo):
            async def count(self, tenant_id, **kwargs):
                raise RuntimeError("db connection lost")

        try:
            await _svc(device_repo=_FailingRepo()).get(uuid.uuid4())
            assert False, "expected RuntimeError"
        except RuntimeError as e:
            assert "db connection lost" in str(e)
