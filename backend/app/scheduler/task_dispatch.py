"""
Task dispatch — the missing half of the scheduler.

Before this module the scheduler ran ONLY monitoring collection
(`jobs.tick`). Nothing ever moved a DB-backed `Task` out of ``created``, so
queued tasks accumulated indefinitely even though the routing engine and the
task state machine were fully implemented and working.

This job closes that gap using ONLY components that already exist:

  ``RoutingEngine``  - selects a real, available agent
  ``TaskService``    - performs the real ``created -> assigned`` transition
  ``AgentRepository``- real workload accounting

It introduces no new queue, no new task system, no new state model, no Redis
usage, and no simulated execution. Tasks it cannot route stay queued and are
reported, rather than being faked into a later state.
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy import select, text

from app.core.db import AsyncSessionLocal, engine
from app.models.task import Task
from app.repositories.agent_repository import AgentRepository
from app.repositories.task_repository import TaskRepository
from app.services.task_routing import NoAgentAvailable, RoutingEngine
from app.services.task_service import TaskNotFound, TaskService, TaskTransitionNotAllowed

logger = logging.getLogger(__name__)

# Distinct from jobs.TICK_LOCK_KEY so dispatch and collection never block one
# another. Stable across restarts/workers, which is what makes it work when
# uvicorn runs more than one worker.
DISPATCH_LOCK_KEY = 91827365

# Bounded per tick so a large backlog is drained gradually instead of in one
# long transaction. The next tick continues where this one stopped.
DISPATCH_BATCH_PER_TENANT = 25


async def dispatch_queued_tasks() -> None:
    """One dispatch tick.

    Guarded by a Postgres advisory lock so only one worker dispatches at a
    time. No-ops (never raises) when the lock is held elsewhere. Mirrors the
    connection-scoped lock handling in ``jobs.tick``: the try-lock and unlock
    must run on the same underlying connection.
    """
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": DISPATCH_LOCK_KEY}
        )
        if not bool(result.scalar_one()):
            logger.debug("Task dispatch: advisory lock held by another worker, skipping.")
            return
        try:
            await _dispatch_once()
        finally:
            await conn.execute(
                text("SELECT pg_advisory_unlock(:key)"), {"key": DISPATCH_LOCK_KEY}
            )


async def _tenant_ids_with_queued_tasks(db) -> list[uuid.UUID]:
    """Tenants that actually have work waiting, so idle tenants cost nothing."""
    rows = await db.execute(
        select(Task.tenant_id).where(Task.status == "created").distinct()
    )
    return [row[0] for row in rows.all()]


async def _dispatch_once() -> None:
    """Route and assign queued tasks for every tenant that has any.

    Each task is committed independently, so one unroutable task cannot roll
    back the tasks already dispatched in the same pass.
    """
    dispatched = 0
    unroutable = 0

    async with AsyncSessionLocal() as db:
        task_repo = TaskRepository(db)
        agent_repo = AgentRepository(db)
        task_service = TaskService(task_repo=task_repo)
        routing = RoutingEngine(agent_repo=agent_repo)

        for tenant_id in await _tenant_ids_with_queued_tasks(db):
            queued = await task_repo.list(
                tenant_id, status="created", limit=DISPATCH_BATCH_PER_TENANT
            )
            # The repository orders newest-first for API pagination; dispatch
            # is a queue, so serve the oldest work first (FIFO).
            queued.sort(key=lambda t: t.created_at or 0)

            for task in queued:
                try:
                    # Same selection the /tasks/{id}/route endpoint performs:
                    # task type first, then skills, then any available agent.
                    candidates = await routing.find_candidates(
                        tenant_id, task, limit=1
                    )
                    if not candidates:
                        unroutable += 1
                        logger.info(
                            "Task dispatch: no agent available for task %s (%s); left queued.",
                            task.task_number,
                            task.task_type,
                        )
                        continue

                    agent = candidates[0]
                    await task_service.assign_task(
                        task.id,
                        tenant_id,
                        agent.id,
                        manager_agent_id=agent.manager_id,
                    )
                    await agent_repo.increment_workload(agent.id, tenant_id)
                    await db.commit()
                    dispatched += 1
                    logger.info(
                        "Task dispatch: %s -> assigned to %s (%s)",
                        task.task_number,
                        agent.name,
                        agent.id,
                    )
                except (NoAgentAvailable, TaskTransitionNotAllowed, TaskNotFound) as exc:
                    await db.rollback()
                    unroutable += 1
                    logger.info(
                        "Task dispatch: %s not dispatched (%s: %s).",
                        task.task_number,
                        type(exc).__name__,
                        exc,
                    )
                except Exception:  # noqa: BLE001
                    # One bad task must not stop the whole queue from draining.
                    await db.rollback()
                    logger.exception(
                        "Task dispatch: unexpected error dispatching %s.", task.task_number
                    )

    if dispatched or unroutable:
        logger.info(
            "Task dispatch tick complete: %s dispatched, %s left queued.",
            dispatched,
            unroutable,
        )
