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
    #: Sum of the storage the vaults report; None until any vault reports it.
    storage_used_bytes: int | None = None
    #: Restore points across items that report a count; None if none do.
    recovery_points_total: int | None = None
    #: Azure VMs in a connected subscription with no backup item. None when
    #: no backup source is configured (coverage cannot be judged).
    unprotected_resources: int | None = None


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
    storage_used_bytes: int | None = None
    policies: int = 0


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
    recovery_point_count: int | None = None
    oldest_recovery_point_at: datetime | None = None
    #: Daily restore points kept by the item's policy; None if not reported.
    retention_days: int | None = None
    #: The inventory device this item protects (matched by resource id).
    device_id: uuid.UUID | None = None
    site_id: uuid.UUID | None = None


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


class BackupPolicyOut(BaseModel):
    name: str
    workload_type: str | None
    schedule_frequency: str | None
    schedule_times: list[str]
    daily_retention_days: int | None
    weekly_retention_weeks: int | None
    monthly_retention_months: int | None
    yearly_retention_years: int | None
    protected_items_count: int | None


class BackupItemDetail(BaseModel):
    """One protected item with its policy and recent job history."""

    item: BackupItemOut
    policy: BackupPolicyOut | None
    jobs: list[BackupJobOut]


#: protected | protection_stopped (item exists, protection stopped or paused)
#: | unprotected (no backup item) | not_monitored (subscription not connected)
CoverageState = Literal["protected", "protection_stopped", "unprotected", "not_monitored"]


class BackupCoverageResource(BaseModel):
    device_id: uuid.UUID
    name: str
    resource_id: str
    subscription_id: str | None
    site_id: uuid.UUID | None
    state: CoverageState


class BackupCoverage(BaseModel):
    """Which inventory resources a backup provider could protect, and do."""

    configured: bool
    #: Subscriptions with an active backup source.
    monitored_subscriptions: list[str]
    total: int
    protected: int
    protection_stopped: int
    unprotected: int
    not_monitored: int
    resources: list[BackupCoverageResource]
