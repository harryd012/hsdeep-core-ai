"""
ITSM connections in the Integration Center (P12 slice 2, step 2b;
docs/design/INTEGRATION_FRAMEWORK_PLAN.md).

Two stores hold ITSM connections and their sync jobs, with the same shape:

  * ServiceNow:    `servicenow_connections` / `servicenow_sync_jobs`
  * other ITSM:    `itsm_connections` / `itsm_sync_jobs` (universal framework)

Both become `kind="itsm"` rows. The state is derived only from what was
recorded:

  disabled      the connection is switched off
  failing       the connection reports an error, its last health check failed,
                or its most recent finished job went to the dead-letter queue
  degraded      jobs of the last 24 hours are waiting to be retried, or went to
                the dead-letter queue, although the latest finished job succeeded
  never_synced  no sync recorded and no job ever succeeded
  healthy       otherwise

ITSM sync is event-driven (pushes, pulls, webhooks), so there is no "stale".
Read-only: retrying a dead-letter job stays on the ITSM / ServiceNow routes.
Job payloads (which can hold a webhook body) are never returned.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Float, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.itsm import ITSMConnection, ITSMSyncJob
from app.models.servicenow import ServiceNowConnection, ServiceNowSyncJob

RUN_WINDOW = timedelta(hours=24)
HISTORY_LIMIT = 50
ERROR_CHARS = 500

_FAILED = ("failed", "dead_letter")
_OPEN = ("pending", "processing", "failed")
_FINISHED = ("succeeded", "dead_letter")


@dataclass(frozen=True)
class _Store:
    connection: type
    job: type
    provider: str | None  # fixed provider type, or None to read it from the row


STORES = (
    _Store(ServiceNowConnection, ServiceNowSyncJob, "servicenow"),
    _Store(ITSMConnection, ITSMSyncJob, None),
)


@dataclass
class JobStats:
    jobs_24h: int = 0
    succeeded_24h: int = 0
    failed_24h: int = 0
    dead_letter_24h: int = 0
    retrying_24h: int = 0
    avg_ms: float | None = None
    open_jobs: int = 0
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    last_attempt_at: datetime | None = None
    next_attempt_at: datetime | None = None
    last_status: str | None = None
    last_finished_status: str | None = None
    ever_succeeded: bool = False


def _err(text: str | None) -> str | None:
    if not text:
        return None
    return text if len(text) <= ERROR_CHARS else text[: ERROR_CHARS - 1] + "…"


def itsm_state(conn, stats: JobStats) -> str:
    if not conn.is_active or conn.status == "disabled":
        return "disabled"
    if conn.status == "error" or getattr(conn, "last_health_check_ok", None) is False:
        return "failing"
    if stats.last_finished_status == "dead_letter":
        return "failing"
    if stats.retrying_24h or stats.dead_letter_24h:
        return "degraded"
    if conn.last_sync_at is None and not stats.ever_succeeded:
        return "never_synced"
    return "healthy"


def _credential_configured(conn) -> bool:
    if isinstance(conn, ServiceNowConnection):
        return bool(conn.password_encrypted or conn.oauth_client_secret_encrypted or conn.oauth_access_token_encrypted)
    return bool(conn.credentials_encrypted)


async def _job_stats(db: AsyncSession, job, tenant_id: uuid.UUID, since: datetime,
                     connection_ids: list[uuid.UUID]) -> dict[uuid.UUID, JobStats]:
    if not connection_ids:
        return {}
    recent = job.created_at >= since
    duration = cast(func.extract("epoch", job.completed_at - job.started_at), Float) * 1000.0
    rows = (await db.execute(
        select(
            job.connection_id,
            func.count().filter(recent),
            func.count().filter(recent, job.status == "succeeded"),
            func.count().filter(recent, job.status.in_(_FAILED)),
            func.count().filter(recent, job.status == "dead_letter"),
            func.count().filter(recent, job.status == "failed"),
            func.avg(duration).filter(recent, job.completed_at.is_not(None), job.started_at.is_not(None)),
            func.count().filter(job.status.in_(_OPEN)),
            func.max(job.completed_at).filter(job.status == "succeeded"),
            func.max(job.updated_at).filter(job.status.in_(_FAILED)),
            func.max(job.started_at),
            func.min(job.next_attempt_at).filter(job.status.in_(("pending", "failed"))),
            func.count().filter(job.status == "succeeded"),
        )
        .where(job.tenant_id == tenant_id, job.connection_id.in_(connection_ids))
        .group_by(job.connection_id)
    )).all()
    stats: dict[uuid.UUID, JobStats] = {}
    for (cid, n, ok, bad, dead, retrying, avg, open_jobs, last_ok, last_bad, last_attempt, next_at, ever_ok) in rows:
        stats[cid] = JobStats(
            jobs_24h=n, succeeded_24h=ok, failed_24h=bad, dead_letter_24h=dead, retrying_24h=retrying,
            avg_ms=None if avg is None else float(avg), open_jobs=open_jobs, last_success_at=last_ok,
            last_failure_at=last_bad, last_attempt_at=last_attempt, next_attempt_at=next_at, ever_succeeded=ever_ok > 0,
        )

    async def latest(*where) -> dict:
        q = (select(job.connection_id, job.status)
             .where(job.tenant_id == tenant_id, job.connection_id.in_(connection_ids), *where)
             .distinct(job.connection_id)
             .order_by(job.connection_id, job.created_at.desc(), job.id))
        return dict((await db.execute(q)).all())

    for cid, status in (await latest()).items():
        stats.setdefault(cid, JobStats()).last_status = status
    for cid, status in (await latest(job.status.in_(_FINISHED))).items():
        stats.setdefault(cid, JobStats()).last_finished_status = status
    return stats


def itsm_row(conn, store: _Store, stats: JobStats | None) -> dict:
    stats = stats or JobStats()
    n = stats.jobs_24h
    return {
        "kind": "itsm", "id": conn.id, "name": conn.name, "type": store.provider or conn.provider_type, "vendor": "itsm",
        "endpoint": conn.instance_url,
        "state": itsm_state(conn, stats), "status": conn.status, "health_status": None, "enabled": conn.is_active,
        "credential_configured": _credential_configured(conn), "poll_interval_seconds": None,
        "last_success_at": stats.last_success_at or conn.last_sync_at, "last_failure_at": stats.last_failure_at,
        "last_attempt_at": stats.last_attempt_at, "last_run_status": stats.last_status,
        "next_run_at": stats.next_attempt_at, "consecutive_failures": None, "last_error": _err(conn.last_error),
        "sensors": None, "devices": None,
        "runs_24h": n, "failed_runs_24h": stats.failed_24h,
        "success_rate_24h": round(stats.succeeded_24h / n * 100, 1) if n else None,
        "avg_run_ms": None if stats.avg_ms is None else round(stats.avg_ms),
        "open_jobs": stats.open_jobs, "dead_letter_24h": stats.dead_letter_24h,
    }


async def itsm_rows(db: AsyncSession, tenant_id: uuid.UUID, now: datetime) -> list[dict]:
    items: list[dict] = []
    for store in STORES:
        conns = (await db.execute(
            select(store.connection).where(store.connection.tenant_id == tenant_id, store.connection.is_deleted.is_(False))
        )).scalars().all()
        stats = await _job_stats(db, store.job, tenant_id, now - RUN_WINDOW, [c.id for c in conns])
        items += [itsm_row(c, store, stats.get(c.id)) for c in conns]
    return items


class ITSMConnectionNotFound(LookupError):
    pass


async def itsm_detail(db: AsyncSession, tenant_id: uuid.UUID, connection_id: uuid.UUID, now: datetime) -> dict:
    for store in STORES:
        conn = (await db.execute(
            select(store.connection).where(store.connection.id == connection_id, store.connection.tenant_id == tenant_id,
                                           store.connection.is_deleted.is_(False))
        )).scalar_one_or_none()
        if conn is None:
            continue
        stats = await _job_stats(db, store.job, tenant_id, now - RUN_WINDOW, [conn.id])
        job = store.job
        jobs = (await db.execute(
            select(job).where(job.tenant_id == tenant_id, job.connection_id == conn.id)
            .order_by(job.created_at.desc(), job.id).limit(HISTORY_LIMIT)
        )).scalars().all()
        return {
            "connection": itsm_row(conn, store, stats.get(conn.id)),
            "jobs": [
                {
                    "id": j.id, "job_type": j.job_type, "status": j.status, "created_at": j.created_at,
                    "started_at": j.started_at, "completed_at": j.completed_at, "next_attempt_at": j.next_attempt_at,
                    "retry_count": j.retry_count, "max_retries": j.max_retries,
                    "duration_ms": None if j.started_at is None or j.completed_at is None
                    else round((j.completed_at - j.started_at).total_seconds() * 1000),
                    "error": _err(j.last_error),
                }
                for j in jobs
            ],
        }
    raise ITSMConnectionNotFound(str(connection_id))
