"""Veeam Backup & Replication connector (docs/engineering/veeam-backup-monitoring.md).

The REST API is replayed with httpx.MockTransport using the response shapes
of the Veeam Backup & Replication REST API reference (data + pagination
envelopes; job states, sessions, repository states, backups, backup objects,
restore points). Covers login and paging, the mapping onto the
provider-neutral backup model, the repository free-space rule, persistence
through BundleSaver with alerts, failed reads, and the API across providers.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy import select

from app.collectors.base import CollectorConfigError, NormalizedBackupVault
from app.collectors.veeam_backup import VeeamBackupCollector
from app.core.auth import create_access_token
from app.main import app
from app.models.backup import BackupItem, BackupJob, BackupPolicy, BackupVault
from app.repositories.alert_repository import AlertRepository
from app.services.alert_service import AlertService
from app.services.monitoring.backup_saver import vault_readings

NOW = datetime.now(timezone.utc).replace(microsecond=0)
HOST = "vbr01.corp.local"
REPO_LOCAL = "88788f9e-d8f5-4eb4-bc4f-9b3f5403bcec"
REPO_S3 = "5f2a2c14-6f2e-4a6e-9f0b-6a1f2b3c4d5e"
REPO_HARDENED = "1c0d7b8e-2f3a-4b5c-8d9e-0f1a2b3c4d5e"
JOB_VMS = "b0f9a6c5-6c3c-4b7a-9d1e-2f3a4b5c6d7e"
JOB_FILES = "c1e0b7d6-7d4d-4c8b-0e2f-3a4b5c6d7e8f"
BACKUP_VMS = "d2f1c8e7-8e5e-4d9c-1f30-4b5c6d7e8f90"
BACKUP_ORPHAN = "e3a2d9f8-9f6f-4ea0-2a41-5c6d7e8f9a01"


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _page(rows: list[dict]) -> dict:
    return {"data": rows, "pagination": {"total": len(rows), "count": len(rows), "skip": 0, "limit": 500}}


REPOSITORIES = [
    {"type": "WinLocal", "id": REPO_LOCAL, "name": "Default Backup Repository", "description": "",
     "hostId": "6745a759-2205-4cd2-b172-8ec8f7e60ef8", "hostName": HOST, "path": "D:\\Backups",
     "capacityGB": 1000.0, "freeGB": 40.0, "usedSpaceGB": 900.0, "isOnline": True},
    {"type": "AmazonS3", "id": REPO_S3, "name": "S3 offsite", "description": "", "hostId": None, "hostName": None,
     "path": "bucket/veeam", "capacityGB": None, "freeGB": None, "usedSpaceGB": 250.0, "isOnline": True},
    {"type": "LinuxHardened", "id": REPO_HARDENED, "name": "Hardened repo", "description": "",
     "hostId": "7845a759-2205-4cd2-b172-8ec8f7e60ef9", "hostName": "lhr01", "path": "/mnt/backup",
     "capacityGB": 2000.0, "freeGB": 1500.0, "usedSpaceGB": 500.0, "isOnline": False},
]
JOB_STATES = [
    {"id": JOB_VMS, "name": "Daily VMs", "description": "", "type": "Backup", "status": "inactive",
     "lastResult": "Failed", "workload": "vm", "lastRun": _iso(NOW - timedelta(hours=5)),
     "nextRun": _iso(NOW + timedelta(hours=19)), "repositoryId": REPO_LOCAL,
     "repositoryName": "Default Backup Repository", "objectsCount": 3, "sessionId": None},
    {"id": JOB_FILES, "name": "File servers", "description": "", "type": "Backup", "status": "disabled",
     "lastResult": "Success", "workload": "server", "lastRun": _iso(NOW - timedelta(days=3)),
     "nextRun": None, "repositoryId": REPO_HARDENED, "repositoryName": "Hardened repo",
     "objectsCount": 1, "sessionId": None},
]
BACKUPS = [
    {"id": BACKUP_VMS, "jobId": JOB_VMS, "policyUniqueId": None, "name": "Daily VMs", "platformName": "VMware",
     "platformId": "00000000-0000-0000-0000-000000000000", "creationTime": _iso(NOW - timedelta(days=60)),
     "repositoryId": REPO_LOCAL},
    {"id": BACKUP_ORPHAN, "jobId": None, "policyUniqueId": None, "name": "Old job (deleted)", "platformName": "VMware",
     "platformId": "00000000-0000-0000-0000-000000000000", "creationTime": _iso(NOW - timedelta(days=200)),
     "repositoryId": REPO_LOCAL},
]
OBJECTS = {
    BACKUP_VMS: [
        {"viType": "VirtualMachine", "objectId": "vm-101", "path": "vc01/DC/vm-101", "platformName": "VMware",
         "id": "a0000000-0000-0000-0000-000000000001", "name": "web-01", "type": "VM",
         "platformId": "00000000-0000-0000-0000-000000000000", "restorePointsCount": 14},
        {"viType": "VirtualMachine", "objectId": "vm-102", "path": "vc01/DC/vm-102", "platformName": "VMware",
         "id": "a0000000-0000-0000-0000-000000000002", "name": "db-01", "type": "VM",
         "platformId": "00000000-0000-0000-0000-000000000000", "restorePointsCount": 9},
        {"viType": "VirtualMachine", "objectId": "vm-103", "path": "vc01/DC/vm-103", "platformName": "VMware",
         "id": "a0000000-0000-0000-0000-000000000003", "name": "new-vm", "type": "VM",
         "platformId": "00000000-0000-0000-0000-000000000000", "restorePointsCount": 0},
    ],
    BACKUP_ORPHAN: [
        {"viType": "VirtualMachine", "objectId": "vm-050", "path": "vc01/DC/vm-050", "platformName": "VMware",
         "id": "a0000000-0000-0000-0000-000000000050", "name": "legacy-app", "type": "VM",
         "platformId": "00000000-0000-0000-0000-000000000000", "restorePointsCount": 3},
    ],
}


def _point(name, backup_id, created):
    return {"platformName": "VMware", "type": "Increment", "malwareStatus": "Clean", "id": str(uuid.uuid4()),
            "name": name, "platformId": "00000000-0000-0000-0000-000000000000", "creationTime": _iso(created),
            "backupId": backup_id, "sessionId": None, "allowedOperations": [], "backupFileId": str(uuid.uuid4())}


RECENT_POINTS = [
    _point("web-01", BACKUP_VMS, NOW - timedelta(hours=5)),
    _point("web-01", BACKUP_VMS, NOW - timedelta(hours=29)),
]
#: db-01 has no point in the 7-day window: the newest comes from a targeted read.
OLD_POINT_DB = _point("db-01", BACKUP_VMS, NOW - timedelta(days=10))
OLD_POINT_LEGACY = _point("legacy-app", BACKUP_ORPHAN, NOW - timedelta(days=150))
SESSIONS = [
    {"sessionType": "BackupJob", "state": "Stopped", "id": "f0000000-0000-0000-0000-000000000001",
     "name": "Daily VMs", "jobId": JOB_VMS, "creationTime": _iso(NOW - timedelta(hours=5)),
     "endTime": _iso(NOW - timedelta(hours=4, minutes=40)), "progressPercent": 100,
     "result": {"result": "Failed", "message": "Processing db-01 Error: Datastore is full", "isCanceled": False},
     "resourceId": None, "resourceReference": None, "parentSessionId": None, "usn": 1},
    {"sessionType": "BackupJob", "state": "Stopped", "id": "f0000000-0000-0000-0000-000000000002",
     "name": "Daily VMs", "jobId": JOB_VMS, "creationTime": _iso(NOW - timedelta(hours=29)),
     "endTime": _iso(NOW - timedelta(hours=28, minutes=30)), "progressPercent": 100,
     "result": {"result": "Warning", "message": "Changed block tracking is disabled", "isCanceled": False},
     "resourceId": None, "resourceReference": None, "parentSessionId": None, "usn": 2},
    {"sessionType": "BackupJob", "state": "Working", "id": "f0000000-0000-0000-0000-000000000003",
     "name": "Daily VMs", "jobId": JOB_VMS, "creationTime": _iso(NOW - timedelta(minutes=10)),
     "endTime": None, "progressPercent": 40,
     "result": {"result": "None", "message": "", "isCanceled": False},
     "resourceId": None, "resourceReference": None, "parentSessionId": None, "usn": 3},
    {"sessionType": "Infrastructure", "state": "Stopped", "id": "f0000000-0000-0000-0000-000000000004",
     "name": "Rescan of host", "jobId": None, "creationTime": _iso(NOW - timedelta(hours=1)),
     "endTime": _iso(NOW - timedelta(minutes=59)), "progressPercent": 100,
     "result": {"result": "Success", "message": "", "isCanceled": False},
     "resourceId": None, "resourceReference": None, "parentSessionId": None, "usn": 4},
]


def _transport(*, repos_fail=False, sessions_fail=False, jobs_fail=False, bad_login=False,
               split_objects=False, seen: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        path, params = request.url.path, request.url.params
        assert request.headers.get("x-api-version") == "1.1-rev0"
        if path == "/api/oauth2/token":
            form = parse_qs(request.content.decode())
            assert form["grant_type"] == ["password"] and form["username"] == ["svc-veeam"]
            if bad_login:
                return httpx.Response(401, json={"errorCode": "Unauthorized"})
            return httpx.Response(200, json={"access_token": "tok", "token_type": "bearer", "refresh_token": "r",
                                             "expires_in": 3600})
        if path == "/api/oauth2/logout":
            return httpx.Response(200)
        assert request.headers.get("authorization") == "Bearer tok"
        if path == "/api/v1/backupInfrastructure/repositories/states":
            return httpx.Response(500) if repos_fail else httpx.Response(200, json=_page(REPOSITORIES))
        if path == "/api/v1/jobs/states":
            return httpx.Response(500) if jobs_fail else httpx.Response(200, json=_page(JOB_STATES))
        if path == "/api/v1/backups":
            return httpx.Response(200, json=_page(BACKUPS))
        if path.startswith("/api/v1/backups/") and path.endswith("/objects"):
            rows = OBJECTS[path.split("/")[4]]
            if split_objects and len(rows) > 1:
                # Two pages: the first page is full (limit=2), the second has the rest.
                skip = int(params.get("skip", "0"))
                page = rows[skip:skip + 2]
                return httpx.Response(200, json={"data": page, "pagination": {
                    "total": len(rows), "count": len(page), "skip": skip, "limit": 2}})
            return httpx.Response(200, json=_page(rows))
        if path == "/api/v1/restorePoints":
            if params.get("backupObjectIdFilter") == OBJECTS[BACKUP_VMS][1]["id"]:
                return httpx.Response(200, json=_page([OLD_POINT_DB]))
            if params.get("backupObjectIdFilter") == OBJECTS[BACKUP_ORPHAN][0]["id"]:
                return httpx.Response(200, json=_page([OLD_POINT_LEGACY]))
            assert params.get("createdAfterFilter")
            return httpx.Response(200, json=_page(RECENT_POINTS))
        if path == "/api/v1/sessions":
            return httpx.Response(500) if sessions_fail else httpx.Response(200, json=_page(SESSIONS))
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _collector(transport, **source_kw) -> VeeamBackupCollector:
    fields = dict(id=uuid.uuid4(), source_type="veeam_backup", hostname=HOST, port=None, username="svc-veeam",
                  password=None, api_key="s3cret", verify_ssl=False, config={})
    fields.update(source_kw)
    collector = VeeamBackupCollector(SimpleNamespace(**fields))
    collector.transport = transport
    return collector


async def _bundle(**kw):
    collector = _collector(_transport(**kw))
    return collector.normalize(await collector.collect())


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


def _client(tenant_id, permissions=frozenset({"backup.read"})):
    token = create_access_token(user_id=uuid.uuid4(), tenant_id=tenant_id, email="noc@hsdeep.local",
                                is_superuser=False, permissions=permissions)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                             headers={"Authorization": f"Bearer {token}"})


# ---------------------------------------------------------------------------
# Collector and mapping
# ---------------------------------------------------------------------------


async def test_collect_logs_in_pages_and_only_reads(monkeypatch):
    import app.collectors.veeam_backup as veeam

    monkeypatch.setattr(veeam, "PAGE_SIZE", 2)
    seen: list[httpx.Request] = []
    collector = _collector(_transport(split_objects=True, seen=seen))
    raw = await collector.collect()
    assert raw["status"] == {"vaults": "collected", "usage": "collected", "policies": "collected",
                             "items": "collected", "jobs": "collected"}
    assert len(raw["objects"][BACKUP_VMS]) == 3  # both pages read
    assert str(seen[0].url) == f"https://{HOST}:9419/api/oauth2/token"
    posts = [r.url.path for r in seen if r.method != "GET"]
    assert posts == ["/api/oauth2/token", "/api/oauth2/logout"]
    # The password never appears in a URL or header.
    assert not any("s3cret" in str(r.url) or "s3cret" in str(r.headers) for r in seen)


async def test_normalize_maps_onto_the_neutral_model():
    bundle = await _bundle()
    vaults = {v.name: v for v in bundle.backup_vaults}
    local, s3, hardened = vaults["Default Backup Repository"], vaults["S3 offsite"], vaults["Hardened repo"]
    assert (local.provider, local.kind, local.offsite, local.immutable) == ("veeam", "repository", None, None)
    assert (s3.kind, s3.offsite, s3.capacity_bytes) == ("bucket", True, None)
    assert (hardened.immutable, hardened.online) == (True, False)
    assert local.capacity_bytes == 1000 * 1024**3 and local.free_bytes == 40 * 1024**3
    devices = {d.name: d for d in bundle.devices}
    assert devices["Hardened repo"].status == "down" and devices["S3 offsite"].device_type == "storage"

    items = {i.name: i for i in bundle.backup_items}
    assert set(items) == {"web-01", "db-01", "new-vm", "legacy-app"}
    assert items["web-01"].last_backup_at == NOW - timedelta(hours=5)   # newest of the recent points
    assert items["db-01"].last_backup_at == NOW - timedelta(days=10)    # targeted read
    assert items["new-vm"].last_backup_at is None                       # no restore point at all
    assert items["web-01"].protection_state == "protected" and items["web-01"].policy_name == "Daily VMs"
    assert items["legacy-app"].protection_state == "protection_stopped"  # its job was deleted
    assert all(i.last_backup_status is None for i in items.values())     # Veeam has no per-object verdict
    assert items["web-01"].recovery_point_count == 14 and items["web-01"].workload_type == "VMware VM"
    assert {p.name for p in bundle.backup_policies} == {"Daily VMs", "File servers"}

    jobs = {j.external_id[-1]: j for j in bundle.backup_jobs}
    assert set(jobs) == {"1", "2", "3"}  # the infrastructure session has no job
    assert (jobs["1"].status, jobs["1"].error_message) == ("failed", "Processing db-01 Error: Datastore is full")
    assert jobs["2"].status == "completed_with_warnings" and jobs["1"].duration_seconds == 20 * 60
    assert (jobs["3"].status, jobs["3"].ended_at) == ("in_progress", None)


async def test_bad_credentials_fail_the_run_and_the_connection_check():
    with pytest.raises(CollectorConfigError):
        await _collector(_transport(bad_login=True)).collect()
    check = await _collector(_transport(bad_login=True)).authenticate()
    assert check.success is False and "s3cret" not in check.message
    assert (await _collector(_transport()).authenticate()).success is True


async def test_missing_config_is_reported_before_any_request():
    with pytest.raises(CollectorConfigError):
        await _collector(_transport(), username=None).collect()


async def test_failed_reads_are_reported_not_empty():
    raw = await _collector(_transport(sessions_fail=True, jobs_fail=True)).collect()
    assert raw["status"]["jobs"] == "failed" and raw["status"]["policies"] == "failed"
    # Without job states, protection cannot be told: items keep their stored state.
    assert raw["status"]["items"] == "failed"
    with pytest.raises(httpx.HTTPStatusError):
        await _collector(_transport(repos_fail=True)).collect()


def test_free_space_rule():
    def vault(free_gb):
        return NormalizedBackupVault(external_id="r", name="repo", provider="veeam", kind="repository",
                                     capacity_bytes=100 * 1024**3, free_bytes=free_gb * 1024**3)

    status = {gb: vault_readings(vault(gb))[0].status for gb in (50, 10, 3)}
    assert status == {50: "up", 10: "warning", 3: "down"}
    elastic = NormalizedBackupVault(external_id="b", name="bucket", provider="veeam", kind="bucket")
    assert vault_readings(elastic) == []


# ---------------------------------------------------------------------------
# Persistence, alerts and API
# ---------------------------------------------------------------------------


async def _evaluate(db, result):
    service = AlertService(AlertRepository(db))
    out = []
    for sensor in result.changed_sensors:
        evaluation = await service.evaluate_sensor(sensor)
        if evaluation.alert is not None:
            out.append(evaluation.alert)
    return out


async def test_save_stores_repositories_items_jobs_and_raises_alerts(db, monitoring_source_factory):
    source = await monitoring_source_factory(source_type="veeam_backup", hostname=HOST)
    result = await _saver(db).save(source, await _bundle())
    await db.commit()
    assert (result.backup_vaults_updated, result.backup_items_upserted, result.backup_jobs_upserted) == (3, 4, 3)

    vaults = {v.name: v for v in (await db.execute(select(BackupVault).where(BackupVault.tenant_id == source.tenant_id))).scalars()}
    local = vaults["Default Backup Repository"]
    assert (local.provider, local.kind, local.free_bytes) == ("veeam", "repository", 40 * 1024**3)
    assert local.device_id is not None and vaults["S3 offsite"].offsite is True
    items = (await db.execute(select(BackupItem).where(BackupItem.tenant_id == source.tenant_id))).scalars().all()
    assert len(items) == 4
    policies = (await db.execute(select(BackupPolicy).where(BackupPolicy.tenant_id == source.tenant_id))).scalars().all()
    assert {p.name for p in policies} == {"Daily VMs", "File servers"}
    jobs = (await db.execute(select(BackupJob).where(BackupJob.tenant_id == source.tenant_id))).scalars().all()
    assert {j.status for j in jobs} == {"failed", "completed_with_warnings", "in_progress"}

    titles = [a.title for a in await _evaluate(db, result) if a.status == "open"]
    await db.commit()
    assert any("Default Backup Repository free space" in t for t in titles)   # 4% free
    assert any("db-01" in t and "backup age" in t for t in titles)           # 10 days old
    assert any("new-vm" in t and "backup age" in t for t in titles)          # never
    assert any("legacy-app" in t and "protection" in t for t in titles)      # job deleted
    assert not any("web-01" in t for t in titles)


@pytest.mark.usefixtures("seeded_subscription")
async def test_api_reports_veeam_through_the_neutral_api(db, monitoring_source_factory):
    source = await monitoring_source_factory(source_type="veeam_backup", hostname=HOST)
    await _saver(db).save(source, await _bundle())
    await db.commit()

    async with _client(source.tenant_id) as client:
        overview = (await client.get("/api/backup/overview")).json()
        assert overview["configured"] is True and overview["providers"] == ["veeam"]
        assert overview["sources"][0]["provider"] == "veeam"
        assert (overview["vaults"], overview["vaults_offsite"], overview["vaults_immutable"]) == (3, 1, 1)
        # Coverage is Azure-only: no Azure source, so it is not judged.
        assert overview["unprotected_resources"] is None
        vaults = {v["name"]: v for v in (await client.get("/api/backup/vaults")).json()}
        assert vaults["Default Backup Repository"]["capacity_bytes"] == 1000 * 1024**3
        assert vaults["S3 offsite"]["kind"] == "bucket"
        breached = {i["name"] for i in (await client.get("/api/backup/items", params={"rpo_state": "breached"})).json()}
        assert breached == {"db-01"}


async def test_targeted_restore_point_reads_are_capped(monkeypatch):
    import app.collectors.veeam_backup as veeam

    monkeypatch.setattr(veeam, "MAX_POINT_LOOKUPS", 1)
    seen: list[httpx.Request] = []
    raw = await _collector(_transport(seen=seen)).collect()
    targeted = [r for r in seen if r.url.params.get("backupObjectIdFilter")]
    # db-01 and legacy-app both need one; the cap allows only the first.
    assert len(targeted) == 1 and len(raw["point_lookups"]) == 1
