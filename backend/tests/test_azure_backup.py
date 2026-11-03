"""Azure Backup monitoring, phase B1 (docs/engineering/azure-backup-monitoring.md).

Collector (ARM REST, replayed with httpx.MockTransport using the documented
response shapes), normalisation, alert rules, persistence through BundleSaver,
alerts opening and resolving through the sensor path, the API (RBAC and
tenant isolation) and the Cloud Operations Backup Readiness card.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.collectors.azure_backup import AzureBackupCollector, _filter_time, snake
from app.collectors.base import NormalizedBackupItem, NormalizedBackupVault, NormalizedBundle
from app.core.auth import create_access_token
from app.main import app
from app.models.alert import Alert
from app.models.backup import BackupItem, BackupJob, BackupVault
from app.repositories.alert_repository import AlertRepository
from app.services.alert_service import AlertService
from app.services.monitoring.backup_saver import item_readings, vault_readings

NOW = datetime.now(timezone.utc).replace(microsecond=0)
SUB = "00000000-0000-0000-0000-0000000000aa"
VAULT_ID = f"/subscriptions/{SUB}/resourceGroups/rg-backup/providers/Microsoft.RecoveryServices/vaults/vault-prod"


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _vault(soft="Enabled", immutability="Locked", redundancy="GeoRedundant"):
    return {
        "id": VAULT_ID,
        "name": "vault-prod",
        "location": "westeurope",
        "type": "Microsoft.RecoveryServices/vaults",
        "properties": {
            "provisioningState": "Succeeded",
            "securitySettings": {"softDeleteSettings": {"softDeleteState": soft}, "immutabilitySettings": {"state": immutability}},
            "redundancySettings": {"standardTierStorageRedundancy": redundancy},
        },
    }


def _item(name, *, state="Protected", status="Completed", last: datetime | None = NOW - timedelta(hours=3),
          extended: dict | None = None):
    item = {
        "id": f"{VAULT_ID}/backupFabrics/Azure/protectionContainers/iaasvmcontainer;rg;{name}/protectedItems/vm;rg;{name}",
        "name": f"vm;rg;{name}",
        "properties": {
            "protectedItemType": "Microsoft.Compute/virtualMachines",
            "friendlyName": name,
            "workloadType": "VM",
            "protectionState": state,
            "lastBackupStatus": status,
            # Azure reports year 1 for "never backed up".
            "lastBackupTime": _iso(last) if last else "0001-01-01T00:00:00Z",
            "sourceResourceId": f"/subscriptions/{SUB}/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/{name}",
            "policyId": f"{VAULT_ID}/backupPolicies/DailyPolicy",
            "healthStatus": "Passed",
        },
    }
    if extended is not None:
        item["properties"]["extendedInfo"] = extended
    return item


def _job(job_id, *, status="Completed", item="web-01", hours_ago=2, minutes=14, error=None):
    start = NOW - timedelta(hours=hours_ago)
    props = {
        "jobType": "AzureIaaSVMJob",
        "entityFriendlyName": item,
        "backupManagementType": "AzureIaasVM",
        "operation": "Backup",
        "status": status,
        "startTime": _iso(start),
        "endTime": _iso(start + timedelta(minutes=minutes)) if status != "InProgress" else None,
    }
    if error:
        props["errorDetails"] = [{"errorCode": error[0], "errorString": error[1]}]
    return {"id": f"{VAULT_ID}/backupJobs/{job_id}", "name": job_id, "properties": props}


ITEMS = [
    _item("web-01", extended={"recoveryPointCount": 30, "oldestRecoveryPoint": _iso(NOW - timedelta(days=30)), "policyInconsistent": False}),
    _item("db-01", status="Failed", last=NOW - timedelta(hours=40)),
    _item("old-app", state="ProtectionStopped", last=NOW - timedelta(days=60)),
    _item("new-vm", state="IRPending", status=None, last=None),
]
JOBS = [
    _job("job-1"),
    _job("job-2", status="Failed", item="db-01", error=("UserErrorVmNotFound", "The VM was not found.")),
    _job("job-3", status="CompletedWithWarnings", item="web-01", hours_ago=26),
    _job("job-4", status="InProgress", item="web-01", hours_ago=0),
]


#: Documented shapes: an IaaS VM policy (daily schedule, long-term retention)
#: and a SQL workload policy whose full backup sits under subProtectionPolicy.
POLICIES = [
    {
        "id": f"{VAULT_ID}/backupPolicies/DailyPolicy",
        "name": "DailyPolicy",
        "properties": {
            "backupManagementType": "AzureIaasVM",
            "protectedItemsCount": 3,
            "schedulePolicy": {"schedulePolicyType": "SimpleSchedulePolicy", "scheduleRunFrequency": "Daily",
                               "scheduleRunTimes": ["2018-01-24T02:00:00Z"]},
            "retentionPolicy": {
                "retentionPolicyType": "LongTermRetentionPolicy",
                "dailySchedule": {"retentionTimes": ["2018-01-24T02:00:00Z"], "retentionDuration": {"count": 30, "durationType": "Days"}},
                "weeklySchedule": {"daysOfTheWeek": ["Sunday"], "retentionDuration": {"count": 12, "durationType": "Weeks"}},
            },
        },
    },
    {
        "id": f"{VAULT_ID}/backupPolicies/SqlPolicy",
        "name": "SqlPolicy",
        "properties": {
            "backupManagementType": "AzureWorkload",
            "workLoadType": "SQLDataBase",
            "subProtectionPolicy": [
                {"policyType": "Log", "schedulePolicy": {"schedulePolicyType": "LogSchedulePolicy"}},
                {"policyType": "Full",
                 "schedulePolicy": {"schedulePolicyType": "SimpleSchedulePolicy", "scheduleRunFrequency": "Weekly",
                                    "scheduleRunTimes": ["2018-01-24T22:30:00Z"]},
                 "retentionPolicy": {"retentionPolicyType": "SimpleRetentionPolicy",
                                     "retentionDuration": {"count": 2, "durationType": "Weeks"}}},
            ],
        },
    },
]
#: Vault usage meters (bytes); the dedup meter measures the same data and is ignored.
USAGES = [
    {"unit": "Bytes", "currentValue": 1_500_000_000, "name": {"value": "GRSStorageUsage", "localizedValue": "Cloud - GRS"}},
    {"unit": "Bytes", "currentValue": 500_000_000, "name": {"value": "LRSStorageUsage", "localizedValue": "Cloud - LRS"}},
    {"unit": "Bytes", "currentValue": 900_000_000, "name": {"value": "GRSDedupStorageUsage", "localizedValue": "Dedup - GRS"}},
    {"unit": "Count", "currentValue": 4, "name": {"value": "ProtectedItemCount", "localizedValue": "Protected Items"}},
]


def _arm_transport(*, items_fail=False, vaults_fail=False, seen: list | None = None):
    """Replays the ARM listings; the item list is split across two pages."""

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        path = request.url.path
        if path.endswith("/providers/Microsoft.RecoveryServices/vaults"):
            if vaults_fail:
                return httpx.Response(403, json={"error": {"code": "AuthorizationFailed"}})
            return httpx.Response(200, json={"value": [_vault()]})
        if path.endswith("/backupProtectedItems"):
            if items_fail:
                return httpx.Response(500, json={"error": {"code": "InternalError"}})
            if request.url.params.get("page") == "2":
                return httpx.Response(200, json={"value": ITEMS[2:]})
            return httpx.Response(200, json={"value": ITEMS[:2], "nextLink": f"https://management.azure.com{path}?api-version=2023-04-01&page=2"})
        if path.endswith("/backupJobs"):
            return httpx.Response(200, json={"value": JOBS})
        if path.endswith("/backupPolicies"):
            return httpx.Response(200, json={"value": POLICIES})
        if path.endswith("/usages"):
            return httpx.Response(200, json={"value": USAGES})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _collector(transport) -> AzureBackupCollector:
    source = SimpleNamespace(
        id=uuid.uuid4(), source_type="azure_backup", password="secret", api_key=None,
        config={"tenant_id": "t", "subscription_id": SUB, "client_id": "c"},
    )
    collector = AzureBackupCollector(source)
    collector.transport = transport

    async def token() -> str:
        return "test-token"

    collector._bearer_token = token  # type: ignore[method-assign]
    return collector


# ---------------------------------------------------------------------------
# Collector and normalisation
# ---------------------------------------------------------------------------


def test_snake_and_filter_time():
    assert snake("CompletedWithWarnings") == "completed_with_warnings"
    assert snake("GeoRedundant") == "geo_redundant"
    assert snake(None) is None
    assert _filter_time(datetime(2026, 1, 31, 23, 59, 59, tzinfo=timezone.utc)) == "2026-01-31 11:59:59 PM"


async def test_collect_reads_every_page_and_only_issues_gets():
    seen: list[httpx.Request] = []
    collector = _collector(_arm_transport(seen=seen))
    raw = await collector.collect()
    bundle = collector.normalize(raw)

    assert {r.method for r in seen} == {"GET"}
    assert all(r.headers["Authorization"] == "Bearer test-token" for r in seen)
    job_call = next(r for r in seen if r.url.path.endswith("/backupJobs"))
    assert "startTime eq '" in job_call.url.params["$filter"] and "endTime eq '" in job_call.url.params["$filter"]
    assert sum(1 for r in seen if r.url.path.endswith("/backupProtectedItems")) == 2  # followed nextLink
    assert bundle.backup_status == {
        "vaults": "collected", "items": "collected", "jobs": "collected", "policies": "collected", "usage": "collected",
    }

    vault = bundle.backup_vaults[0]
    assert (vault.soft_delete_state, vault.immutability_state, vault.storage_redundancy) == ("enabled", "locked", "geo_redundant")
    # GRS + LRS meters; the dedup meter re-counts the same data and is left out.
    assert vault.storage_used_bytes == 2_000_000_000
    assert vault.resource_group == "rg-backup" and vault.subscription_id == SUB
    device = bundle.devices[0]
    assert (device.external_id, device.device_type, device.vendor) == (VAULT_ID.lower(), "cloud_resource", "Microsoft")

    items = {i.name: i for i in bundle.backup_items}
    assert len(items) == 4
    assert items["db-01"].last_backup_status == "failed"
    assert items["old-app"].protection_state == "protection_stopped"
    assert items["new-vm"].protection_state == "ir_pending"
    assert items["new-vm"].last_backup_at is None  # year 1 means "never"
    assert items["web-01"].policy_name == "DailyPolicy"
    assert items["web-01"].recovery_point_count == 30
    assert items["web-01"].oldest_recovery_point_at == NOW - timedelta(days=30)
    assert items["db-01"].recovery_point_count is None, "not reported stays None, never 0"

    policies = {p.name: p for p in bundle.backup_policies}
    daily = policies["DailyPolicy"]
    assert (daily.schedule_frequency, daily.schedule_times) == ("daily", ["02:00"])
    assert (daily.daily_retention_days, daily.weekly_retention_weeks) == (30, 12)
    assert (daily.monthly_retention_months, daily.yearly_retention_years) == (None, None)
    assert daily.protected_items_count == 3
    sql = policies["SqlPolicy"]  # full-backup tier nested under subProtectionPolicy
    assert (sql.workload_type, sql.schedule_frequency, sql.schedule_times) == ("SQLDataBase", "weekly", ["22:30"])
    assert sql.daily_retention_days == 14, "2 weeks of simple retention is 14 days"

    jobs = {j.external_id.rsplit("/", 1)[-1]: j for j in bundle.backup_jobs}
    assert jobs["job-1"].duration_seconds == 14 * 60
    assert (jobs["job-2"].status, jobs["job-2"].error_code) == ("failed", "UserErrorVmNotFound")
    assert jobs["job-4"].status == "in_progress" and jobs["job-4"].duration_seconds is None


async def test_failed_item_read_is_reported_not_empty():
    collector = _collector(_arm_transport(items_fail=True))
    bundle = collector.normalize(await collector.collect())
    assert bundle.backup_status["items"] == "failed"
    assert bundle.backup_status["jobs"] == "collected"
    assert bundle.backup_items == []


async def test_vault_listing_failure_fails_the_run():
    collector = _collector(_arm_transport(vaults_fail=True))
    with pytest.raises(httpx.HTTPStatusError):
        await collector.collect()


# ---------------------------------------------------------------------------
# Rules (pure)
# ---------------------------------------------------------------------------


def _ni(**kw) -> NormalizedBackupItem:
    base = dict(external_id=f"{VAULT_ID}/x/protecteditems/vm1", vault_external_id=VAULT_ID, name="vm1",
                protection_state="protected", last_backup_status="completed", last_backup_at=NOW - timedelta(hours=2))
    base.update(kw)
    return NormalizedBackupItem(**base)


def _by_key(readings):
    return {r.external_id.rsplit(":", 1)[-1]: r for r in readings}


def test_item_rules():
    healthy = _by_key(item_readings(_ni(), "vault", NOW, 24))
    assert {k: r.status for k, r in healthy.items()} == {"rpo": "up", "last": "up", "protection": "up"}

    breached = _by_key(item_readings(_ni(last_backup_at=NOW - timedelta(hours=30)), "vault", NOW, 24))
    assert breached["rpo"].status == "down" and breached["rpo"].value == 30.0

    never = _by_key(item_readings(_ni(last_backup_at=None, last_backup_status=None, protection_state="ir_pending"), "vault", NOW, 24))
    assert never["rpo"].status == "down" and never["rpo"].value is None and "last" not in never

    failed = _by_key(item_readings(_ni(last_backup_status="failed"), "vault", NOW, 24))
    assert failed["last"].status == "down"
    warned = _by_key(item_readings(_ni(last_backup_status="completed_with_warnings"), "vault", NOW, 24))
    assert warned["last"].status == "warning"

    stopped = _by_key(item_readings(_ni(protection_state="protection_stopped", last_backup_at=NOW - timedelta(days=90)), "vault", NOW, 24))
    assert stopped["rpo"].status == "unknown"  # resolves, never pages
    assert stopped["protection"].status == "warning"
    error = _by_key(item_readings(_ni(protection_state="protection_error"), "vault", NOW, 24))
    assert error["protection"].status == "down"


def test_vault_rules():
    def vault(state):
        return NormalizedBackupVault(external_id=VAULT_ID, name="v", soft_delete_state=state)

    assert vault_readings(vault(None)) == []
    assert vault_readings(vault("enabled"))[0].status == "up"
    assert vault_readings(vault("always_on"))[0].status == "up"
    assert vault_readings(vault("disabled"))[0].status == "warning"


# ---------------------------------------------------------------------------
# Persistence and alerts
# ---------------------------------------------------------------------------


def _saver(db):
    from app.repositories.device_repository import DeviceRepository
    from app.repositories.event_repository import EventRepository
    from app.repositories.interface_repository import InterfaceRepository
    from app.repositories.metric_repository import MetricRepository
    from app.repositories.sensor_repository import SensorRepository
    from app.services.monitoring.bundle_saver import BundleSaver

    return BundleSaver(
        device_repo=DeviceRepository(db), interface_repo=InterfaceRepository(db),
        sensor_repo=SensorRepository(db), metric_repo=MetricRepository(db), event_repo=EventRepository(db),
    )


async def _bundle(**transport_kw) -> NormalizedBundle:
    collector = _collector(_arm_transport(**transport_kw))
    return collector.normalize(await collector.collect())


async def _evaluate(db, result) -> list[Alert]:
    service = AlertService(AlertRepository(db))
    out = []
    for sensor in result.changed_sensors:
        evaluation = await service.evaluate_sensor(sensor)
        if evaluation.alert is not None:
            out.append(evaluation.alert)
    return out


async def test_save_stores_vault_items_jobs_and_raises_alerts(db, monitoring_source_factory):
    source = await monitoring_source_factory(source_type="azure_backup")
    saver = _saver(db)
    result = await saver.save(source, await _bundle())
    await db.commit()
    assert (result.backup_vaults_updated, result.backup_items_upserted, result.backup_jobs_upserted) == (1, 4, 4)

    vault = (await db.execute(select(BackupVault).where(BackupVault.tenant_id == source.tenant_id))).scalar_one()
    assert vault.device_id is not None and vault.soft_delete_state == "enabled"
    assert (await db.execute(select(BackupItem).where(BackupItem.vault_id == vault.id))).scalars().all().__len__() == 4
    assert len((await db.execute(select(BackupJob).where(BackupJob.vault_id == vault.id))).scalars().all()) == 4

    alerts = await _evaluate(db, result)
    await db.commit()
    open_titles = sorted(a.title.split(" — ", 1)[-1] for a in alerts if a.status == "open")
    # db-01: last backup failed AND outside the 24 h RPO; new-vm never backed
    # up; old-app protection stopped. web-01 is healthy.
    assert any("db-01" in t and "last backup" in t for t in open_titles)
    assert any("db-01" in t and "backup age" in t for t in open_titles)
    assert any("new-vm" in t and "backup age" in t for t in open_titles)
    assert any("old-app" in t and "protection" in t for t in open_titles)
    assert not any("web-01" in t for t in open_titles)
    assert all(a.device_id == vault.device_id for a in alerts)


async def test_rerun_upserts_jobs_and_retires_missing_items(db, monitoring_source_factory):
    source = await monitoring_source_factory(source_type="azure_backup")
    saver = _saver(db)
    await saver.save(source, await _bundle())
    await db.commit()

    bundle = await _bundle()
    bundle.backup_items = [i for i in bundle.backup_items if i.name != "old-app"]
    result = await saver.save(source, bundle)
    await db.commit()
    assert result.backup_items_retired == 1
    vault = (await db.execute(select(BackupVault).where(BackupVault.tenant_id == source.tenant_id))).scalar_one()
    names = {i.name for i in (await db.execute(select(BackupItem).where(BackupItem.vault_id == vault.id))).scalars()}
    assert "old-app" not in names
    assert len((await db.execute(select(BackupJob).where(BackupJob.vault_id == vault.id))).scalars().all()) == 4  # no duplicates


async def test_failed_item_read_keeps_stored_items(db, monitoring_source_factory):
    source = await monitoring_source_factory(source_type="azure_backup")
    saver = _saver(db)
    await saver.save(source, await _bundle())
    await db.commit()
    result = await saver.save(source, await _bundle(items_fail=True))
    await db.commit()
    assert result.backup_items_upserted == 0 and result.backup_items_retired == 0
    vault = (await db.execute(select(BackupVault).where(BackupVault.tenant_id == source.tenant_id))).scalar_one()
    assert len((await db.execute(select(BackupItem).where(BackupItem.vault_id == vault.id))).scalars().all()) == 4


async def test_failed_backup_alert_resolves_when_backup_succeeds(db, monitoring_source_factory):
    source = await monitoring_source_factory(source_type="azure_backup")
    saver = _saver(db)
    first = await saver.save(source, await _bundle())
    alerts = await _evaluate(db, first)
    await db.commit()
    failed = next(a for a in alerts if "db-01" in a.title and "last backup" in a.title)

    bundle = await _bundle()
    for item in bundle.backup_items:
        if item.name == "db-01":
            item.last_backup_status = "completed"
            item.last_backup_at = NOW - timedelta(hours=1)
    second = await saver.save(source, bundle)
    await _evaluate(db, second)
    await db.commit()
    await db.refresh(failed)
    assert failed.status == "resolved"


# ---------------------------------------------------------------------------
# API, RBAC, tenant isolation, Cloud Operations card
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def other_tenant(seeded_subscription):
    from sqlalchemy import delete

    from app.core.db import AsyncSessionLocal
    from app.models.subscription import Subscription
    from app.models.tenant import Tenant

    async with AsyncSessionLocal() as s:
        other = Tenant(name="Other MSP", slug=f"other-{uuid.uuid4().hex[:8]}")
        s.add(other)
        await s.flush()
        s.add(Subscription(
            tenant_id=other.id, plan_id=seeded_subscription.plan_id, status="active",
            current_period_start=NOW, current_period_end=NOW + timedelta(days=30), billing_interval="month",
        ))
        await s.commit()
    yield other.id
    async with AsyncSessionLocal() as s:
        await s.execute(delete(Subscription).where(Subscription.tenant_id == other.id))
        await s.execute(delete(Tenant).where(Tenant.id == other.id))
        await s.commit()


def _client(tenant_id, permissions=frozenset({"backup.read"}), user_id: uuid.UUID | None = None):
    token = create_access_token(
        user_id=user_id or uuid.uuid4(), tenant_id=tenant_id, email="noc@hsdeep.local",
        is_superuser=False, permissions=permissions,
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                             headers={"Authorization": f"Bearer {token}"})


@pytest.mark.usefixtures("seeded_subscription")
async def test_overview_before_any_source_is_not_configured(tenant):
    async with _client(tenant.id) as client:
        overview = (await client.get("/api/backup/overview")).json()
    assert overview["configured"] is False and overview["items_total"] == 0
    assert overview["success_rate_24h"] is None


@pytest.mark.usefixtures("seeded_subscription")
async def test_api_overview_items_jobs_and_isolation(db, monitoring_source_factory, other_tenant):
    source = await monitoring_source_factory(source_type="azure_backup")
    await _saver(db).save(source, await _bundle())
    await db.commit()

    async with _client(source.tenant_id) as client:
        overview = (await client.get("/api/backup/overview")).json()
        assert overview["configured"] is True
        assert (overview["vaults"], overview["items_total"], overview["items_protected"]) == (1, 4, 3)
        assert (overview["items_within_rpo"], overview["items_breaching_rpo"], overview["items_never_backed_up"]) == (1, 1, 1)
        assert (overview["items_not_protected"], overview["items_last_failed"]) == (1, 1)
        # 24 h: job-1 completed, job-2 failed, job-4 in progress -> 1 of 2 finished succeeded.
        assert overview["jobs_24h"]["failed"] == 1 and overview["success_rate_24h"] == 50.0
        assert overview["jobs_7d"]["total"] == 4 and len(overview["daily"]) == 14

        breached = (await client.get("/api/backup/items", params={"rpo_state": "breached"})).json()
        assert [i["name"] for i in breached] == ["db-01"]
        failed = (await client.get("/api/backup/jobs", params={"status": "failed"})).json()
        assert failed[0]["error_code"] == "UserErrorVmNotFound" and failed[0]["vault_name"] == "vault-prod"
        vaults = (await client.get("/api/backup/vaults")).json()
        assert vaults[0]["items_total"] == 4 and vaults[0]["items_breaching_rpo"] == 2

    async with _client(other_tenant) as client:
        other = (await client.get("/api/backup/overview")).json()
        assert other["items_total"] == 0 and other["vaults"] == 0
        assert (await client.get("/api/backup/items")).json() == []
        assert (await client.get("/api/backup/jobs")).json() == []

    async with _client(source.tenant_id, permissions=frozenset({"devices.read"})) as client:
        for path in ("/api/backup/overview", "/api/backup/vaults", "/api/backup/items", "/api/backup/jobs"):
            denied = await client.get(path)
            assert denied.status_code == 403, path


async def test_cloud_operations_backup_readiness(db, monitoring_source_factory):
    from app.repositories.device_repository import DeviceRepository
    from app.repositories.incident_repository import IncidentRepository
    from app.services.backup_service import BackupService
    from app.services.cloud_operations_service import CloudOperationsService

    def service():
        return CloudOperationsService(DeviceRepository(db), IncidentRepository(db), backup_service=BackupService(db))

    source = await monitoring_source_factory(source_type="azure_backup")
    card = (await service().get(source.tenant_id)).backup_readiness
    assert card.unavailable is True and "no protected items" in card.unavailable_reason

    await _saver(db).save(source, await _bundle())
    await db.commit()
    card = (await service().get(source.tenant_id)).backup_readiness
    # 3 protected items (web-01, db-01, new-vm); only web-01 is inside the RPO.
    assert card.unavailable is False and card.value == 33
    assert card.detail["breaching_rpo"] == 1 and card.detail["never_backed_up"] == 1


# ---------------------------------------------------------------------------
# Phase 1: policies, storage, restore points, job retention, coverage, detail
# ---------------------------------------------------------------------------


async def test_save_stores_policies_storage_and_restore_points(db, monitoring_source_factory):
    from app.models.backup import BackupPolicy

    source = await monitoring_source_factory(source_type="azure_backup")
    await _saver(db).save(source, await _bundle())
    await db.commit()

    vault = (await db.execute(select(BackupVault).where(BackupVault.tenant_id == source.tenant_id))).scalar_one()
    assert vault.storage_used_bytes == 2_000_000_000
    policies = {p.name: p for p in (await db.execute(select(BackupPolicy).where(BackupPolicy.vault_id == vault.id))).scalars()}
    assert set(policies) == {"DailyPolicy", "SqlPolicy"}
    assert policies["DailyPolicy"].schedule_times == ["02:00"] and policies["DailyPolicy"].daily_retention_days == 30
    web = (await db.execute(select(BackupItem).where(BackupItem.vault_id == vault.id, BackupItem.name == "web-01"))).scalar_one()
    assert web.recovery_point_count == 30

    # A failed usage/policy read keeps what an earlier poll stored.
    bundle = await _bundle()
    bundle.backup_status.update(usage="failed", policies="failed")
    bundle.backup_vaults[0].storage_used_bytes = None
    bundle.backup_policies = []
    await _saver(db).save(source, bundle)
    await db.commit()
    await db.refresh(vault)
    assert vault.storage_used_bytes == 2_000_000_000
    assert len((await db.execute(select(BackupPolicy).where(BackupPolicy.vault_id == vault.id))).scalars().all()) == 2


async def test_job_history_older_than_retention_is_purged(db, monitoring_source_factory):
    from app.core.config import get_settings

    source = await monitoring_source_factory(source_type="azure_backup")
    await _saver(db).save(source, await _bundle())
    await db.commit()
    vault = (await db.execute(select(BackupVault).where(BackupVault.tenant_id == source.tenant_id))).scalar_one()
    keep_days = get_settings().backup_job_retention_days
    db.add(BackupJob(
        tenant_id=source.tenant_id, vault_id=vault.id, external_id="ancient", status="completed",
        started_at=NOW - timedelta(days=keep_days + 1),
    ))
    await db.commit()

    await _saver(db).save(source, await _bundle())
    await db.commit()
    ids = {j.external_id for j in (await db.execute(select(BackupJob).where(BackupJob.vault_id == vault.id))).scalars()}
    assert "ancient" not in ids and len(ids) == 4


async def _vm(db, tenant_id, name, sub=SUB, site_id=None):
    from app.models.device import Device

    device = Device(
        tenant_id=tenant_id, name=name, device_type="cloud_resource", status="up", site_id=site_id,
        external_id=f"/subscriptions/{sub}/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/{name}",
    )
    db.add(device)
    await db.flush()
    return device


@pytest.mark.usefixtures("seeded_subscription")
async def test_coverage_item_detail_site_filter_and_isolation(db, monitoring_source_factory, other_tenant):
    from app.models.site import Site

    source = await monitoring_source_factory(
        source_type="azure_backup", config={"tenant_id": "t", "subscription_id": SUB, "client_id": "c"},
    )
    site = Site(tenant_id=source.tenant_id, name="Mohali", code="MOH")
    db.add(site)
    await db.flush()
    await _vm(db, source.tenant_id, "web-01", site_id=site.id)             # protected
    await _vm(db, source.tenant_id, "old-app")                             # protection stopped
    await _vm(db, source.tenant_id, "orphan-vm")                           # same subscription, no backup item
    await _vm(db, source.tenant_id, "elsewhere", sub="11111111-2222-3333-4444-555555555555")  # not connected
    await db.commit()
    await _saver(db).save(source, await _bundle())
    await db.commit()

    async with _client(source.tenant_id) as client:
        cov = (await client.get("/api/backup/coverage")).json()
        assert cov["configured"] is True and cov["monitored_subscriptions"] == [SUB]
        states = {r["name"]: r["state"] for r in cov["resources"]}
        assert states == {
            "web-01": "protected", "old-app": "protection_stopped",
            "orphan-vm": "unprotected", "elsewhere": "not_monitored",
        }
        assert (cov["total"], cov["unprotected"], cov["not_monitored"]) == (4, 1, 1)
        assert (await client.get("/api/backup/overview")).json()["unprotected_resources"] == 1

        items = (await client.get("/api/backup/items")).json()
        web = next(i for i in items if i["name"] == "web-01")
        assert (web["recovery_point_count"], web["retention_days"]) == (30, 30)
        assert web["site_id"] == str(site.id) and web["device_id"] is not None
        at_site = (await client.get("/api/backup/items", params={"site_id": str(site.id)})).json()
        assert [i["name"] for i in at_site] == ["web-01"]

        detail = (await client.get(f"/api/backup/items/{web['id']}")).json()
        assert detail["policy"]["name"] == "DailyPolicy" and detail["policy"]["weekly_retention_weeks"] == 12
        assert {j["status"] for j in detail["jobs"]} == {"completed", "completed_with_warnings", "in_progress"}
        assert all(j["item_name"] == "web-01" for j in detail["jobs"])
        vaults = (await client.get("/api/backup/vaults")).json()
        assert (vaults[0]["storage_used_bytes"], vaults[0]["policies"]) == (2_000_000_000, 2)

    async with _client(other_tenant) as client:
        assert (await client.get(f"/api/backup/items/{web['id']}")).status_code == 404
        other = (await client.get("/api/backup/coverage")).json()
        assert other["configured"] is False and other["resources"] == []

    async with _client(source.tenant_id, permissions=frozenset({"devices.read"})) as client:
        for path in ("/api/backup/coverage", f"/api/backup/items/{web['id']}"):
            assert (await client.get(path)).status_code == 403, path


# ---------------------------------------------------------------------------
# Phase 2: provider-neutral model, status vocabulary, per-tenant RPO
# ---------------------------------------------------------------------------


def test_vocabulary_maps_unknown_vendor_terms_to_other():
    from app.collectors.backup_vocabulary import JOB_STATUSES, PROTECTION_STATES, canonical
    from app.collectors.base import NormalizedBackupJob
    from app.services.monitoring.backup_saver import canonical_item, canonical_job

    assert canonical("protected", PROTECTION_STATES) == "protected"
    assert canonical("invalid", PROTECTION_STATES) == "other"
    assert canonical(None, JOB_STATUSES) is None
    item = canonical_item(NormalizedBackupItem(
        external_id="x", vault_external_id="v", name="x", protection_state="soft_deleted", last_backup_status="partial",
    ))
    assert (item.protection_state, item.last_backup_status) == ("other", "other")
    job = canonical_job(NormalizedBackupJob(external_id="j", vault_external_id="v", item_name=None, operation=None,
                                            status="queued"))
    assert job.status == "other"


@pytest.mark.parametrize(
    ("redundancy", "immutability", "offsite", "immutable"),
    [("GeoRedundant", "Locked", True, True), ("LocallyRedundant", "Unlocked", False, False),
     ("ZoneRedundant", "Disabled", False, False)],
)
async def test_azure_vault_reports_offsite_and_immutable(redundancy, immutability, offsite, immutable):
    def handler(request):
        if request.url.path.endswith("/vaults"):
            return httpx.Response(200, json={"value": [_vault(immutability=immutability, redundancy=redundancy)]})
        return httpx.Response(200, json={"value": []})

    collector = _collector(httpx.MockTransport(handler))
    vault = collector.normalize(await collector.collect()).backup_vaults[0]
    assert (vault.kind, vault.offsite, vault.immutable) == ("vault", offsite, immutable)


def test_backup_sources_come_from_the_collector_registry():
    from app.services.backup_service import backup_source_types

    assert backup_source_types()["azure_backup"] == "azure"
    assert backup_source_types("azure") == {"azure_backup": "azure"}
    assert backup_source_types("veeam") == {"veeam_backup": "veeam"}
    assert backup_source_types("nobody") == {}


async def test_tenant_rpo_drives_alerts_and_verdicts(db, monitoring_source_factory):
    from app.services.backup_service import BackupService
    from app.services.backup_settings import set_tenant_rpo

    source = await monitoring_source_factory(source_type="azure_backup")
    # db-01's last backup is 40 h old: breached at the default 24 h, within at 48 h.
    await set_tenant_rpo(db, source.tenant_id, 48)
    await db.commit()
    result = await _saver(db).save(source, await _bundle())
    await db.commit()
    open_titles = [a.title for a in await _evaluate(db, result) if a.status == "open"]
    await db.commit()
    # The alert rule uses the tenant RPO too: no backup-age alert for db-01.
    assert not any("db-01" in t and "backup age" in t for t in open_titles)
    assert any("new-vm" in t and "backup age" in t for t in open_titles)
    items = {i.name: i for i in await BackupService(db).items(source.tenant_id)}
    assert items["db-01"].rpo_state == "within"
    overview = await BackupService(db).overview(source.tenant_id)
    assert (overview.rpo_hours, overview.rpo_is_default) == (48, False)
    assert (overview.vaults_offsite, overview.vaults_immutable, overview.providers) == (1, 1, ["azure"])

    await set_tenant_rpo(db, source.tenant_id, None)
    await db.commit()
    items = {i.name: i for i in await BackupService(db).items(source.tenant_id)}
    assert items["db-01"].rpo_state == "breached"


@pytest.mark.usefixtures("seeded_subscription")
async def test_settings_api_rbac_validation_audit_and_isolation(db, monitoring_source_factory, other_tenant):
    from app.core.config import get_settings
    from app.models.knowledge_audit import AuditEvent

    source = await monitoring_source_factory(source_type="azure_backup")
    default = get_settings().backup_rpo_hours

    async with _client(source.tenant_id) as client:
        assert (await client.get("/api/backup/settings")).json() == {
            "rpo_hours": default, "rpo_is_default": True, "default_rpo_hours": default,
        }
        # backup.read alone cannot change targets.
        assert (await client.put("/api/backup/settings", json={"rpo_hours": 12})).status_code == 403

    writer = frozenset({"backup.read", "backup.write"})
    async with _client(source.tenant_id, permissions=writer, user_id=uuid.UUID(int=0)) as client:
        for bad in (0, 721, "x"):
            assert (await client.put("/api/backup/settings", json={"rpo_hours": bad})).status_code == 422, bad
        body = (await client.put("/api/backup/settings", json={"rpo_hours": 12})).json()
        assert (body["rpo_hours"], body["rpo_is_default"]) == (12, False)
        assert (await client.get("/api/backup/overview")).json()["rpo_hours"] == 12

    async with _client(other_tenant) as client:
        assert (await client.get("/api/backup/settings")).json()["rpo_is_default"] is True

    async with _client(source.tenant_id, permissions=writer, user_id=uuid.UUID(int=0)) as client:
        body = (await client.put("/api/backup/settings", json={"rpo_hours": None})).json()
        assert (body["rpo_hours"], body["rpo_is_default"]) == (default, True)

    events = (
        await db.execute(
            select(AuditEvent)
            .where(AuditEvent.tenant_id == source.tenant_id, AuditEvent.action == "backup.settings.updated")
            .order_by(AuditEvent.created_at)
        )
    ).scalars().all()
    assert [(e.old_values, e.new_values) for e in events] == [
        ({"rpo_hours": None}, {"rpo_hours": 12}), ({"rpo_hours": 12}, {"rpo_hours": None}),
    ]
    assert events[0].actor_email == "noc@hsdeep.local"


# ---------------------------------------------------------------------------
# BR-1 Backup Command Center: filters, workloads, failures, security, history
# ---------------------------------------------------------------------------


async def test_azure_vault_security_settings_are_read_as_reported():
    vault = _vault()
    vault["properties"]["securitySettings"]["multiUserAuthorization"] = "Enabled"
    vault["properties"]["redundancySettings"]["crossRegionRestore"] = "Disabled"
    vault["properties"]["publicNetworkAccess"] = "Enabled"
    vault["properties"]["encryption"] = {"keyVaultProperties": {"keyUri": "https://kv.vault.azure.net/keys/k/1"}}

    def handler(request):
        if request.url.path.endswith("/vaults"):
            return httpx.Response(200, json={"value": [vault, {**_vault(), "id": VAULT_ID + "-2", "name": "vault-2"}]})
        return httpx.Response(200, json={"value": []})

    collector = _collector(httpx.MockTransport(handler))
    first, second = collector.normalize(await collector.collect()).backup_vaults
    assert (first.multi_user_authorization, first.encryption, first.cross_region_restore, first.public_network_access) == (
        "enabled", "customer_managed", False, "enabled",
    )
    # Not in the response: not reported - except encryption, which Azure always applies.
    assert (second.multi_user_authorization, second.cross_region_restore, second.public_network_access) == (None, None, None)
    assert second.encryption == "platform_managed"


def _sv(**kw):
    base = dict(
        provider="azure", soft_delete_state=None, immutable=None, immutability_state=None, offsite=None,
        storage_redundancy=None, multi_user_authorization=None, cross_region_restore=None, encryption=None,
        public_network_access=None,
    )
    return SimpleNamespace(**{**base, **kw})


def test_security_checks_never_pass_what_is_not_reported():
    from app.services.backup_service import security_checks

    status = lambda v: {c.key: c.status for c in security_checks(v)}  # noqa: E731
    assert set(status(_sv(provider="veeam")).values()) == {"not_reported"}
    hardened = status(_sv(
        soft_delete_state="always_on", immutable=True, immutability_state="locked", offsite=True,
        storage_redundancy="geo_redundant", multi_user_authorization="enabled", cross_region_restore=True,
        encryption="customer_managed", public_network_access="disabled",
    ))
    assert set(hardened.values()) == {"pass"}
    weak = status(_sv(
        soft_delete_state="disabled", immutable=False, immutability_state="unlocked", offsite=False,
        storage_redundancy="locally_redundant", multi_user_authorization="disabled", encryption="platform_managed",
        public_network_access="enabled",
    ))
    assert weak == {
        "soft_delete": "fail", "immutability": "warn", "offsite": "warn", "multi_user_authorization": "warn",
        "cross_region_restore": "not_applicable", "encryption": "pass", "public_network_access": "warn",
    }
    assert status(_sv(immutable=False, immutability_state="disabled"))["immutability"] == "fail"


def test_growth_estimate_is_a_least_squares_slope():
    from datetime import date

    from app.services.backup_service import _growth_per_day

    start = date(2026, 1, 1)
    assert _growth_per_day([(start + timedelta(days=d), 1000 + 50 * d) for d in range(14)]) == pytest.approx(50.0)
    assert _growth_per_day([(start, 1), (start, 2)]) is None


@pytest.mark.usefixtures("seeded_subscription")
async def test_command_center_filters_failures_security_and_isolation(db, monitoring_source_factory, other_tenant):
    source = await monitoring_source_factory(source_type="azure_backup")
    bundle = await _bundle()
    for item in bundle.backup_items:
        if item.name == "db-01":
            item.workload_type = "SQLDataBase"
    await _saver(db).save(source, bundle)
    await db.commit()
    vault_id = (await db.execute(select(BackupVault.id).where(BackupVault.tenant_id == source.tenant_id))).scalar_one()

    async with _client(source.tenant_id) as client:
        overview = (await client.get("/api/backup/overview")).json()
        assert overview["filtered"] is False
        workloads = {w["workload_type"]: w for w in overview["workloads"]}
        assert workloads["SQLDataBase"]["items_total"] == 1 and workloads["SQLDataBase"]["items_last_failed"] == 1
        assert sum(w["items_total"] for w in overview["workloads"]) == 4
        options = overview["filter_options"]
        assert options["subscriptions"] == [SUB] and options["resource_groups"] == ["rg-backup"]
        assert options["locations"] == ["westeurope"] and "SQLDataBase" in options["workload_types"]
        assert options["vaults"] == [{"value": str(vault_id), "label": "vault-prod"}]

        # Workload filter narrows items and, through them, jobs.
        sql = (await client.get("/api/backup/overview", params={"workload_type": "SQLDataBase"})).json()
        assert sql["filtered"] is True and sql["items_total"] == 1 and sql["unprotected_resources"] is None
        assert sql["jobs_7d"]["total"] == 1 and sql["jobs_7d"]["failed"] == 1
        names = [i["name"] for i in (await client.get("/api/backup/items", params={"workload_type": "SQLDataBase"})).json()]
        assert names == ["db-01"]
        assert len((await client.get("/api/backup/jobs", params={"workload_type": "SQLDataBase"})).json()) == 1

        # Storage filters: a region with no storage shows nothing.
        empty = (await client.get("/api/backup/overview", params={"location": "eastus"})).json()
        assert (empty["vaults"], empty["items_total"], empty["jobs_7d"]["total"]) == (0, 0, 0)
        same = (await client.get("/api/backup/overview", params={"storage_id": str(vault_id), "resource_group": "RG-BACKUP"})).json()
        assert (same["vaults"], same["items_total"]) == (1, 4)
        assert (await client.get("/api/backup/vaults", params={"location": "eastus"})).json() == []

        failures = (await client.get("/api/backup/failures")).json()
        assert (failures["failed_jobs"], failures["items_affected"]) == (1, 1)
        group = failures["groups"][0]
        assert (group["error_code"], group["items"], group["vaults"]) == ("UserErrorVmNotFound", ["db-01"], ["vault-prod"])
        assert group["sample_message"] == "The VM was not found."
        assert (await client.get("/api/backup/failures", params={"workload_type": "VM"})).json()["failed_jobs"] == 0

        security = (await client.get("/api/backup/security")).json()
        checks = {c["key"]: c["status"] for c in security["locations"][0]["checks"]}
        # Fixture vault: soft delete on, locked, geo-redundant; MUA / CRR / network not in the response.
        assert (checks["soft_delete"], checks["immutability"], checks["offsite"]) == ("pass", "pass", "pass")
        assert (checks["multi_user_authorization"], checks["public_network_access"]) == ("not_reported", "not_reported")
        assert security["score"] == 100.0

        history = (await client.get("/api/backup/storage/history")).json()
        assert history["history_days"] == 1 and history["growth_bytes_per_day"] is None
        assert history["points"][0]["storage_used_bytes"] == 2_000_000_000

        assert (await client.get("/api/backup/storage/history", params={"days": 3})).status_code == 422

    async with _client(other_tenant) as client:
        assert (await client.get("/api/backup/failures")).json()["failed_jobs"] == 0
        assert (await client.get("/api/backup/security")).json() == {"locations": [], "summary": [], "score": None}
        assert (await client.get("/api/backup/storage/history")).json()["points"] == []
        assert (await client.get("/api/backup/overview")).json()["filter_options"]["vaults"] == []

    async with _client(source.tenant_id, permissions=frozenset({"devices.read"})) as client:
        for path in ("/api/backup/failures", "/api/backup/security", "/api/backup/storage/history"):
            assert (await client.get(path)).status_code == 403, path


async def test_storage_snapshots_are_daily_and_give_a_growth_estimate(db, monitoring_source_factory):
    from app.models.backup import BackupStorageSnapshot
    from app.services.backup_service import BackupService

    source = await monitoring_source_factory(source_type="azure_backup")
    saver = _saver(db)
    await saver.save(source, await _bundle())
    await saver.save(source, await _bundle())  # same day: one row, updated
    await db.commit()
    vault = (await db.execute(select(BackupVault).where(BackupVault.tenant_id == source.tenant_id))).scalar_one()
    rows = (await db.execute(select(BackupStorageSnapshot).where(BackupStorageSnapshot.vault_id == vault.id))).scalars().all()
    assert len(rows) == 1 and (rows[0].storage_used_bytes, rows[0].items_total) == (2_000_000_000, 4)

    today = datetime.now(timezone.utc).date()
    for back in range(1, 20):
        db.add(BackupStorageSnapshot(
            tenant_id=source.tenant_id, vault_id=vault.id, day=today - timedelta(days=back),
            storage_used_bytes=2_000_000_000 - back * 10_000_000,
        ))
    await db.commit()

    now = datetime.now(timezone.utc)
    short = await BackupService(db).storage_history(source.tenant_id, days=7, now=now)
    assert short.history_days == 7 and short.growth_bytes_per_day is None
    history = await BackupService(db).storage_history(source.tenant_id, days=30, now=now)
    assert history.history_days == 20 and history.points[0].day == today - timedelta(days=19)
    assert history.growth_bytes_per_day == pytest.approx(10_000_000)
    assert history.projected_used_bytes_30d == 2_000_000_000 + 300_000_000


@pytest.mark.usefixtures("seeded_subscription")
async def test_overview_time_range_breakdowns_age_and_item_search(db, monitoring_source_factory):
    source = await monitoring_source_factory(source_type="azure_backup")
    await _saver(db).save(source, await _bundle())
    await db.commit()

    async with _client(source.tenant_id) as client:
        default = (await client.get("/api/backup/overview")).json()
        assert default["range_days"] == 14 and len(default["daily"]) == 14
        month = (await client.get("/api/backup/overview", params={"days": 30})).json()
        assert month["range_days"] == 30 and len(month["daily"]) == 30
        assert month["jobs_range"]["total"] == 4
        day = (await client.get("/api/backup/overview", params={"days": 1})).json()
        # job-3 started 26 h ago: outside a 1-day range.
        assert day["jobs_range"]["total"] == 3 and len(day["daily"]) == 1
        for bad in (0, 91):
            assert (await client.get("/api/backup/overview", params={"days": bad})).status_code == 422

        # Every item sits in one subscription and one region.
        assert [(g["key"], g["items_total"], g["items_breaching_rpo"]) for g in default["by_subscription"]] == [(SUB, 4, 1)]
        assert [(g["key"], g["items_within_rpo"]) for g in default["by_region"]] == [("westeurope", 1)]
        # Protected items only: web-01 (3 h), db-01 (40 h), new-vm (never); old-app is stopped.
        ages = {b["key"]: b["items"] for b in default["recovery_point_age"]}
        assert ages == {"lt_24h": 1, "1_3d": 1, "3_7d": 0, "7_30d": 0, "gt_30d": 0, "never": 1}

        search = lambda **p: client.get("/api/backup/items", params=p)  # noqa: E731
        assert [i["name"] for i in (await search(q="DB-0")).json()] == ["db-01"]
        assert [i["name"] for i in (await search(q="virtualmachines/web")).json()] == ["web-01"]
        assert (await search(q="%")).json() == []  # wildcards are literal
        assert [i["name"] for i in (await search(protection_state="protection_stopped")).json()] == ["old-app"]
        assert [i["name"] for i in (await search(last_backup_status="failed")).json()] == ["db-01"]
        assert len((await search(policy_name="DailyPolicy")).json()) == 4
