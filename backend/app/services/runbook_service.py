"""
Runbook service layer.

Thin, mirrors `SOPService`'s split: RunbookExecution LIFECYCLE is owned by
`ExecutionTracker`, so this class exposes only the CRUD/lookup paths for
Runbook itself, plus the create-and-request-execution convenience that the
API's `POST /runbooks/{id}/execute` needs.
"""
from __future__ import annotations

import uuid

from app.models.runbook import Runbook
from app.models.runbook_execution import RunbookExecution
from app.repositories.runbook_execution_repository import RunbookExecutionRepository
from app.repositories.runbook_repository import RunbookRepository
from app.services.sop.execution_tracker import ExecutionTracker


class RunbookNotFound(Exception):
    pass


class RunbookService:
    def __init__(self, runbook_repo: RunbookRepository, execution_tracker: ExecutionTracker, execution_repo: RunbookExecutionRepository):
        self.runbook_repo = runbook_repo
        self.execution_tracker = execution_tracker
        self.execution_repo = execution_repo

    async def get(self, runbook_id: uuid.UUID, tenant_id: uuid.UUID) -> Runbook:
        runbook = await self.runbook_repo.get(runbook_id, tenant_id)
        if runbook is None:
            raise RunbookNotFound(f"Runbook {runbook_id} not found")
        return runbook

    async def list_runbooks(self, tenant_id: uuid.UUID, **filters) -> list[Runbook]:
        return await self.runbook_repo.list(tenant_id, **filters)

    async def list_for_sop(self, sop_id: uuid.UUID, **filters) -> list[Runbook]:
        return await self.runbook_repo.list_for_sop(sop_id, **filters)

    async def create_runbook(self, tenant_id: uuid.UUID, **fields) -> Runbook:
        return await self.runbook_repo.create(tenant_id=tenant_id, **fields)

    async def request_execution(
        self, runbook_id: uuid.UUID, tenant_id: uuid.UUID, *, incident_id: uuid.UUID | None, operator_id: uuid.UUID | None
    ) -> RunbookExecution:
        await self.get(runbook_id, tenant_id)  # tenant-scope check, 404 before creating an execution row
        return await self.execution_tracker.request_execution(
            tenant_id=tenant_id, runbook_id=runbook_id, incident_id=incident_id, operator_id=operator_id
        )

    async def list_execution_history(self, tenant_id: uuid.UUID, **filters) -> list[RunbookExecution]:
        return await self.execution_repo.list(tenant_id, **filters)

    async def get_execution(self, execution_id: uuid.UUID, tenant_id: uuid.UUID) -> RunbookExecution:
        execution = await self.execution_repo.get(execution_id, tenant_id)
        if execution is None:
            raise RunbookNotFound(f"RunbookExecution {execution_id} not found")
        return execution
