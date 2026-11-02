"""
Veeam Backup & Replication collector (docs/engineering/veeam-backup-monitoring.md).

Reads one backup server through the Veeam Backup & Replication REST API
(read-only use; the only POSTs are the OAuth token request and logout) and
reports it in the provider-neutral backup model:

    repositories  -> storage locations (kind "repository", or "bucket" for
                     object storage), with capacity, free and used space
    backup jobs   -> policies (name, objects), and the protection state of
                     the objects they back up
    backup objects-> protected items (VMs, servers, file shares ...), with
                     their restore point count and newest restore point
    sessions      -> job history

Connection (MonitoringSourceConnectionInfo):
    hostname / port     -> the backup server; REST API port 9419 by default
    username            -> a Veeam user (the built-in Veeam Backup Viewer role is enough)
    password or api_key -> that user's password (encrypted at rest)
    verify_ssl          -> TLS verification; Veeam ships a self-signed certificate
    config["api_version"] -> x-api-version header (default DEFAULT_API_VERSION)

Calls (all paged with skip/limit until `pagination.total` is reached):
    POST /api/oauth2/token                                  (grant_type=password)
    GET  /api/v1/backupInfrastructure/repositories/states
    GET  /api/v1/jobs/states
    GET  /api/v1/backups                                    (jobId, repositoryId per backup)
    GET  /api/v1/backups/{id}/objects                       (one call per backup)
    GET  /api/v1/restorePoints?createdAfterFilter=...       (newest point per object)
    GET  /api/v1/restorePoints?backupObjectIdFilter=...&limit=1   (objects with no recent point, capped)
    GET  /api/v1/sessions?createdAfterFilter=...
    POST /api/oauth2/logout                                 (best effort)

Each table reports collected | empty | failed in ``bundle.backup_status``, so a
failed read never wipes what an earlier poll stored. Nothing is inferred: a
value Veeam did not return stays None.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.collectors.base import (
    BaseCollector,
    CollectorConfigError,
    CollectorRegistry,
    ConnectionCheck,
    NormalizedBackupItem,
    NormalizedBackupJob,
    NormalizedBackupPolicy,
    NormalizedBackupVault,
    NormalizedBundle,
    NormalizedDevice,
)
from app.core.config import get_settings

logger = logging.getLogger(__name__)

DEFAULT_PORT = 9419
#: REST API revision sent in x-api-version. 1.1-rev0 is served by Veeam Backup
#: & Replication 12.0 and later; newer servers keep serving older revisions.
DEFAULT_API_VERSION = "1.1-rev0"
PAGE_SIZE = 500
#: Safety cap on pages followed per listing (a runaway listing must end).
MAX_PAGES = 50
#: Restore points newer than this are read in one listing and matched to
#: their objects; an object with none gets one targeted read (up to the cap).
RESTORE_POINT_WINDOW_DAYS = 7
MAX_POINT_LOOKUPS = 300
GIB = 1024**3

#: Repository types that are object storage (reported as kind "bucket").
_OBJECT_STORAGE = {
    "amazons3", "amazons3glacier", "amazonsnowballedge", "s3compatible", "azureblob", "azurearchive",
    "azuredatabox", "googlecloud", "ibmcloud", "wasabicloud",
}
#: Public cloud object storage: the copy is off-site by definition.
_PUBLIC_CLOUD = {"amazons3", "amazons3glacier", "azureblob", "azurearchive", "googlecloud", "ibmcloud", "wasabicloud"}

_RESULT = {"success": "completed", "warning": "completed_with_warnings", "failed": "failed"}


def _ts(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.year < 1900:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _bytes(gb: Any) -> int | None:
    """Veeam reports repository space in GB (binary); None when absent."""
    return int(round(float(gb) * GIB)) if isinstance(gb, (int, float)) else None


def _repo_kind(repo_type: str) -> str:
    return "bucket" if repo_type.lower() in _OBJECT_STORAGE else "repository"


def _offsite(repo_type: str) -> bool | None:
    """Public cloud object storage is off-site. Anything else (local disk,
    SMB/NFS share, hardened Linux, S3-compatible on premises) depends on where
    it stands, which the API does not say: None, never a guess."""
    return True if repo_type.lower() in _PUBLIC_CLOUD else None


def _immutable(repo_type: str) -> bool | None:
    """A hardened Linux repository always makes its backups immutable. Object
    storage immutability is a per-repository setting the states API does not
    return, so it stays None."""
    return True if repo_type.lower() == "linuxhardened" else None


def _protection(job: dict | None) -> str:
    """Protection state of an object from the job that backs it up."""
    if job is None:
        return "protection_stopped"  # the backup has no job any more
    if str(job.get("status") or "").lower() == "disabled":
        return "protection_paused"
    return "protected"


def _session_status(session: dict) -> str:
    state = str(session.get("state") or "").lower()
    result = session.get("result") or {}
    if state and state != "stopped":
        return "in_progress"
    if result.get("isCanceled"):
        return "cancelled"
    return _RESULT.get(str(result.get("result") or "").lower(), "other")


@CollectorRegistry.register("veeam_backup")
class VeeamBackupCollector(BaseCollector):
    display_name = "Veeam Backup & Replication"
    implemented = True
    category = "server"
    backup_provider = "veeam"

    #: Injected by tests (httpx.MockTransport); None uses the real network.
    transport: httpx.AsyncBaseTransport | None = None

    # -- config ----------------------------------------------------------------

    def _password(self) -> str | None:
        return self.source.password or self.source.api_key

    def _require_config(self) -> None:
        missing = [
            name for name, value in (
                ("hostname", self.source.hostname),
                ("username", self.source.username),
                ("password", self._password()),
            ) if not value
        ]
        if missing:
            raise CollectorConfigError(f"Veeam config missing required field(s): {missing}")

    def _base_url(self) -> str:
        return f"https://{self.source.hostname}:{self.source.port or DEFAULT_PORT}"

    def _api_version(self) -> str:
        return str((self.source.config or {}).get("api_version") or DEFAULT_API_VERSION)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url(),
            headers={"x-api-version": self._api_version(), "Accept": "application/json"},
            timeout=httpx.Timeout(30.0),
            verify=bool(self.source.verify_ssl),
            transport=self.transport,
        )

    async def _login(self, client: httpx.AsyncClient) -> None:
        """Password grant; the access token then rides on every request.
        Credentials go only in the form body and are never logged."""
        resp = await client.post(
            "/api/oauth2/token",
            data={"grant_type": "password", "username": self.source.username, "password": self._password()},
        )
        if resp.status_code in (400, 401, 403):
            raise CollectorConfigError("Veeam: the backup server rejected the username or password.")
        resp.raise_for_status()
        token = (resp.json() or {}).get("access_token")
        if not token:
            raise CollectorConfigError("Veeam: token request returned no access token.")
        client.headers["Authorization"] = f"Bearer {token}"

    async def _logout(self, client: httpx.AsyncClient) -> None:
        try:
            await client.post("/api/oauth2/logout")
        except httpx.HTTPError:
            pass  # the token expires on its own (60 minutes)

    async def _list(self, client: httpx.AsyncClient, path: str, params: dict[str, Any] | None = None) -> list[dict]:
        """Every `data` row across skip/limit pages."""
        rows: list[dict] = []
        skip = 0
        for _ in range(MAX_PAGES):
            resp = await client.get(path, params={**(params or {}), "skip": skip, "limit": PAGE_SIZE})
            resp.raise_for_status()
            body = resp.json() or {}
            page = body.get("data") or []
            rows.extend(page)
            total = (body.get("pagination") or {}).get("total")
            skip += len(page)
            if not page or len(page) < PAGE_SIZE or (isinstance(total, int) and skip >= total):
                break
        return rows

    # -- BaseCollector ---------------------------------------------------------

    async def authenticate(self) -> ConnectionCheck:
        self._require_config()
        async with self._client() as client:
            try:
                await self._login(client)
            except CollectorConfigError as exc:
                return ConnectionCheck(success=False, message=str(exc))
            except httpx.HTTPError as exc:
                return ConnectionCheck(success=False, message=f"Could not reach the Veeam backup server: {type(exc).__name__}")
            await self._logout(client)
        return ConnectionCheck(success=True, message="Connected to Veeam Backup & Replication.")

    async def collect(self) -> dict[str, Any]:
        self._require_config()
        settings = get_settings()
        now = datetime.now(timezone.utc)
        raw: dict[str, Any] = {
            "server": self.source.hostname, "status": {}, "repositories": [], "jobs": [], "backups": [],
            "objects": {}, "points": [], "point_lookups": {}, "sessions": [],
        }
        status = raw["status"]
        async with self._client() as client:
            await self._login(client)
            try:
                # Repositories are the base of everything: if they cannot be
                # read the run fails instead of reporting an empty estate.
                raw["repositories"] = await self._list(client, "/api/v1/backupInfrastructure/repositories/states")
                status["vaults"] = status["usage"] = "collected" if raw["repositories"] else "empty"

                try:
                    raw["jobs"] = await self._list(client, "/api/v1/jobs/states")
                    status["policies"] = "collected" if raw["jobs"] else "empty"
                except httpx.HTTPError as exc:
                    status["policies"] = "failed"
                    logger.warning("Veeam: job states read failed: source_id=%s error_type=%s", self.source.id, type(exc).__name__)

                status["items"] = await self._collect_items(client, raw, now)
                # Without the jobs, protection states cannot be told: keep
                # what an earlier poll stored rather than mark everything stopped.
                if status["policies"] == "failed" and status["items"] != "failed":
                    status["items"] = "failed"

                try:
                    raw["sessions"] = await self._list(
                        client, "/api/v1/sessions",
                        {"createdAfterFilter": (now - timedelta(hours=settings.backup_job_lookback_hours)).isoformat()},
                    )
                    status["jobs"] = "collected" if raw["sessions"] else "empty"
                except httpx.HTTPError as exc:
                    status["jobs"] = "failed"
                    logger.warning("Veeam: sessions read failed: source_id=%s error_type=%s", self.source.id, type(exc).__name__)
            finally:
                await self._logout(client)
        return raw

    async def _collect_items(self, client: httpx.AsyncClient, raw: dict[str, Any], now: datetime) -> str:
        """Backups, their objects and each object's newest restore point."""
        try:
            raw["backups"] = await self._list(client, "/api/v1/backups")
            for backup in raw["backups"]:
                backup_id = backup.get("id")
                if backup_id:
                    raw["objects"][backup_id] = await self._list(client, f"/api/v1/backups/{backup_id}/objects")
            raw["points"] = await self._list(
                client, "/api/v1/restorePoints",
                {"createdAfterFilter": (now - timedelta(days=RESTORE_POINT_WINDOW_DAYS)).isoformat()},
            )
            recent = {(p.get("backupId"), p.get("name")) for p in raw["points"]}
            # Objects with restore points but none recent: read their newest
            # point directly, up to the cap (the rest keep last_backup_at None).
            stale = [
                obj for backup_id, objects in raw["objects"].items() for obj in objects
                if (backup_id, obj.get("name")) not in recent and obj.get("restorePointsCount")
            ]
            if len(stale) > MAX_POINT_LOOKUPS:
                logger.warning("Veeam: restore point lookups capped at %s of %s: source_id=%s",
                               MAX_POINT_LOOKUPS, len(stale), self.source.id)
            for obj in stale[:MAX_POINT_LOOKUPS]:
                resp = await client.get(
                    "/api/v1/restorePoints",
                    params={"backupObjectIdFilter": obj.get("id"), "orderColumn": "CreationTime",
                            "orderAsc": "false", "skip": 0, "limit": 1},
                )
                resp.raise_for_status()
                newest = (resp.json() or {}).get("data") or []
                if newest:
                    raw["point_lookups"][obj.get("id")] = newest[0]
        except httpx.HTTPError as exc:
            logger.warning("Veeam: backups read failed: source_id=%s error_type=%s", self.source.id, type(exc).__name__)
            return "failed"
        return "collected" if any(raw["objects"].values()) else "empty"

    def normalize(self, raw: dict[str, Any]) -> NormalizedBundle:
        bundle = NormalizedBundle(backup_status=dict(raw.get("status") or {}))
        server = raw.get("server")

        repos: dict[str, dict] = {}
        for repo in raw.get("repositories") or []:
            repo_id = str(repo.get("id") or "").lower()
            if not repo_id:
                continue
            repos[repo_id] = repo
            repo_type = str(repo.get("type") or "")
            name = repo.get("name") or repo_id
            online = repo.get("isOnline")
            external_id = f"veeam:{server}:repository:{repo_id}"
            bundle.devices.append(
                NormalizedDevice(
                    external_id=external_id,
                    name=name,
                    device_type="storage",
                    vendor="Veeam",
                    model=f"Backup repository ({repo_type})" if repo_type else "Backup repository",
                    hostname=repo.get("hostName"),
                    status="down" if online is False else "up" if online is True else "unknown",
                    custom_metadata={"provider": "veeam", "backup_server": server, "repository_type": repo_type,
                                     "path": repo.get("path")},
                )
            )
            bundle.backup_vaults.append(
                NormalizedBackupVault(
                    external_id=external_id,
                    name=name,
                    provider="veeam",
                    kind=_repo_kind(repo_type),
                    offsite=_offsite(repo_type),
                    immutable=_immutable(repo_type),
                    location=repo.get("hostName"),
                    storage_used_bytes=_bytes(repo.get("usedSpaceGB")),
                    capacity_bytes=_bytes(repo.get("capacityGB")),
                    free_bytes=_bytes(repo.get("freeGB")),
                    online=online if isinstance(online, bool) else None,
                )
            )

        def vault_of(repo_id: Any) -> str | None:
            key = str(repo_id or "").lower()
            return f"veeam:{server}:repository:{key}" if key in repos else None

        jobs = {str(j.get("id") or "").lower(): j for j in raw.get("jobs") or [] if j.get("id")}
        for job in jobs.values():
            vault = vault_of(job.get("repositoryId"))
            if vault and job.get("name"):
                count = job.get("objectsCount")
                bundle.backup_policies.append(
                    NormalizedBackupPolicy(
                        vault_external_id=vault, name=job["name"], workload_type=job.get("workload") or job.get("type"),
                        protected_items_count=count if isinstance(count, int) else None,
                    )
                )

        newest: dict[tuple[Any, Any], datetime] = {}
        for point in raw.get("points") or []:
            created = _ts(point.get("creationTime"))
            key = (point.get("backupId"), point.get("name"))
            if created and (key not in newest or created > newest[key]):
                newest[key] = created

        backups = {b.get("id"): b for b in raw.get("backups") or [] if b.get("id")}
        for backup_id, objects in (raw.get("objects") or {}).items():
            backup = backups.get(backup_id) or {}
            vault = vault_of(backup.get("repositoryId"))
            if not vault:
                continue
            job = jobs.get(str(backup.get("jobId") or "").lower())
            for obj in objects:
                obj_id = obj.get("id")
                if not obj_id:
                    continue
                last = newest.get((backup_id, obj.get("name")))
                if last is None and obj_id in (raw.get("point_lookups") or {}):
                    last = _ts(raw["point_lookups"][obj_id].get("creationTime"))
                points = obj.get("restorePointsCount")
                bundle.backup_items.append(
                    NormalizedBackupItem(
                        external_id=f"{backup_id}/{obj_id}".lower(),
                        vault_external_id=vault,
                        name=obj.get("name") or str(obj_id),
                        workload_type=" ".join(filter(None, (obj.get("platformName"), obj.get("type")))) or None,
                        protection_state=_protection(job),
                        # Veeam reports results per job run, not per object:
                        # job outcomes are in the job history, never copied here.
                        last_backup_status=None,
                        last_backup_at=last,
                        source_resource_id=obj.get("objectId"),
                        policy_name=job.get("name") if job else None,
                        recovery_point_count=points if isinstance(points, int) else None,
                    )
                )

        for session in raw.get("sessions") or []:
            session_id = str(session.get("id") or "").lower()
            job = jobs.get(str(session.get("jobId") or "").lower())
            vault = vault_of(job.get("repositoryId")) if job else None
            if not session_id or not vault:
                continue
            started, ended = _ts(session.get("creationTime")), _ts(session.get("endTime"))
            status = _session_status(session)
            message = (session.get("result") or {}).get("message")
            bundle.backup_jobs.append(
                NormalizedBackupJob(
                    external_id=session_id,
                    vault_external_id=vault,
                    item_name=session.get("name") or job.get("name"),
                    operation=session.get("sessionType"),
                    status=status,
                    started_at=started,
                    ended_at=ended if status != "in_progress" else None,
                    duration_seconds=(ended - started).total_seconds() if started and ended and status != "in_progress" else None,
                    error_message=message if status in ("failed", "completed_with_warnings") and message else None,
                )
            )
        return bundle
