"""
Scheduler lifecycle: wires `jobs.tick()` into an APScheduler `AsyncIOScheduler`
and performs the one-time startup reconciliation sweep for `CollectorRun`
rows stuck in `status="running"` (e.g. the process died mid-run on a
previous deploy).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.config import get_settings
from app.core.db import AsyncSessionLocal
from app.repositories.collector_run_repository import CollectorRunRepository
from app.scheduler.email_jobs import EMAIL_DELIVERY_INTERVAL_SECONDS, deliver_email_outbox_job
from app.scheduler.incident_lifecycle import close_stale_incidents_job
from app.scheduler.jobs import tick
from app.scheduler.task_dispatch import dispatch_queued_tasks

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger(__name__)

_TICK_INTERVAL_SECONDS = 60
# Task dispatch runs on the same cadence as collection. It is a separate job so
# a slow collection pass can never delay queued work from being routed.
_TASK_DISPATCH_INTERVAL_SECONDS = 60


async def _reconcile_stale_runs() -> None:
    """Startup-only sweep: any `CollectorRun` still `status="running"` with
    a `started_at` older than `collector_run_stale_minutes` almost
    certainly means the process died mid-run on a previous deploy (there is
    no crash-recovery path that would leave a row "running" otherwise,
    since `CollectionOrchestrator.run()` always calls `finish()` in a
    try/finally-equivalent structure). Mark those as `status="error"` so
    they stop blocking `get_running_for_source()`'s concurrent-run-skip
    guard for that source forever.
    """
    settings = get_settings()
    threshold = datetime.now(timezone.utc) - timedelta(minutes=settings.collector_run_stale_minutes)

    async with AsyncSessionLocal() as db:
        run_repo = CollectorRunRepository(db)
        stale_runs = await run_repo.list_stale_running(threshold)
        for run in stale_runs:
            await run_repo.finish(
                run,
                status="error",
                error=(
                    "Reconciled at startup: run was still 'running' past the "
                    f"{settings.collector_run_stale_minutes}-minute staleness threshold "
                    "(process likely restarted mid-run)."
                ),
            )
        if stale_runs:
            await db.commit()
            logger.warning("Startup reconciliation: marked %s stale run(s) as error.", len(stale_runs))
        else:
            logger.info("Startup reconciliation: no stale running CollectorRun rows found.")


def start_scheduler(app: "FastAPI") -> AsyncIOScheduler:
    """Called from main.py's startup event. Registers the 60s interval job
    and stores the AsyncIOScheduler on app.state so shutdown can stop it
    cleanly. Does NOT run the reconciliation sweep itself (that's async and
    must be awaited by the caller before/after this is called) - see
    `main.py`'s startup event for the full sequence.
    """
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        tick,
        "interval",
        seconds=_TICK_INTERVAL_SECONDS,
        id="monitoring_collection_tick",
        max_instances=1,
        coalesce=True,
    )
    # Without this job nothing ever moves a task out of `created`, so queued
    # tasks accumulate forever. See app/scheduler/task_dispatch.py.
    scheduler.add_job(
        dispatch_queued_tasks,
        "interval",
        seconds=_TASK_DISPATCH_INTERVAL_SECONDS,
        id="task_dispatch_tick",
        max_instances=1,
        coalesce=True,
    )
    # Auto-close incidents that have gone quiet (stale-close lifecycle rule).
    scheduler.add_job(
        close_stale_incidents_job,
        "interval",
        seconds=get_settings().incident_stale_close_interval_seconds,
        id="incident_stale_close",
        max_instances=1,
        coalesce=True,
    )
    # Password reset, verification and invitation emails are queued by the
    # API and sent here, so an SMTP outage never fails a request.
    scheduler.add_job(
        deliver_email_outbox_job,
        "interval",
        seconds=EMAIL_DELIVERY_INTERVAL_SECONDS,
        id="email_outbox_delivery",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    app.state.scheduler = scheduler
    logger.info("Scheduler started: collection tick every %ss.", _TICK_INTERVAL_SECONDS)
    logger.info(
        "Scheduler started: task dispatch tick every %ss.", _TASK_DISPATCH_INTERVAL_SECONDS
    )
    return scheduler


def stop_scheduler(app: "FastAPI") -> None:
    scheduler = getattr(app.state, "scheduler", None)
    if scheduler is not None:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")
