"""Pydantic schemas for the backup monitoring API (GET /api/backup/*)."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

#: within | breached (no backup inside the RPO) | never (protected, never backed
#: up) | not_protected (protection stopped/paused, or not reported).
RpoState = Literal["within", "breached", "never", "not_protected"]


class BackupSourceSummary(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    is_active: bool
    last_sync_at: datetime | None
    last_error: str | None


class BackupJobCounts(BaseModel):
    total: int = 0
    completed: int = 0
    completed_with_warnings: int = 0
    failed: int = 0
    in_progress: int = 0
    other: int = 0


class BackupDailyJobs(BaseModel):
    day: date
    completed: int = 0
    completed_with_warnings: int = 0
    failed: int = 0
    other: int = 0


class BackupOverview(BaseModel):
    #: False until an Azure Backup source exists for the tenant: every count
    #: below is then a real zero only when ``configured`` is True.
    configured: bool
    generated_at: datetime
    rpo_hours: int
    sources: list[BackupSourceSummary]
    vaults: int
    vaults_soft_delete_off: int
    items_total: int
    items_protected: int
    items_within_rpo: int
    items_breaching_rpo: int
    items_never_backed_up: int
    items_not_protected: int
    items_last_failed: int
    #: Finished jobs that completed (with or without warnings) / finished jobs,
    #: last 24 h. None when no job finished in the window.
    success_rate_24h: float | None
    jobs_24h: BackupJobCounts
    jobs_7d: BackupJobCounts
    daily: list[BackupDailyJobs]
    last_job_at: datetime | None
    observed_at: datetime | None


class BackupVaultOut(BaseModel):
    id: uuid.UUID
    device_id: uuid.UUID | None
    name: str
    provider: str
    location: str | None
    resource_group: str | None
    subscription_id: str | None
    soft_delete_state: str | None
    immutability_state: str | None
    storage_redundancy: str | None
    observed_at: datetime | None
    items_total: int
    items_breaching_rpo: int


class BackupItemOut(BaseModel):
    id: uuid.UUID
    vault_id: uuid.UUID
    vault_name: str
    name: str
    workload_type: str | None
    protection_state: str | None
    last_backup_status: str | None
    last_backup_at: datetime | None
    age_hours: float | None
    rpo_state: RpoState
    policy_name: str | None
    health_status: str | None
    source_resource_id: str | None


class BackupJobOut(BaseModel):
    id: uuid.UUID
    vault_id: uuid.UUID
    vault_name: str
    item_name: str | None
    operation: str | None
    status: str
    started_at: datetime | None
    ended_at: datetime | None
    duration_seconds: float | None
    error_code: str | None
    error_message: str | None
