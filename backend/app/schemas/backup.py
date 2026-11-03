"""Pydantic schemas for the backup monitoring API (GET /api/backup/*)."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.services.backup_settings import RPO_MAX_HOURS, RPO_MIN_HOURS

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
    #: azure | veeam | ... (the collector's backup_provider)
    provider: str | None = None


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
    #: The tenant's RPO (its own, or the platform default).
    rpo_hours: int
    rpo_is_default: bool = True
    sources: list[BackupSourceSummary]
    #: Providers with at least one storage location, e.g. ["azure"].
    providers: list[str] = []
    vaults: int
    #: Storage locations that keep an off-site copy / are immutable.
    vaults_offsite: int = 0
    vaults_immutable: int = 0
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
    #: Days covered by ``daily`` and ``jobs_range`` (the time-range filter).
    range_days: int = 14
    #: Job outcomes over ``range_days``.
    jobs_range: BackupJobCounts | None = None
    #: Items by workload type (filters applied).
    workloads: list[BackupWorkloadSummary] = []
    #: Items by subscription / region of their storage (filters applied).
    by_subscription: list[BackupStatusGroup] = []
    by_region: list[BackupStatusGroup] = []
    #: Protected items by how old their newest backup is.
    recovery_point_age: list[BackupAgeBucket] = []
    #: True when any filter narrowed the figures above.
    filtered: bool = False
    filter_options: BackupFilterOptions | None = None


class BackupVaultOut(BaseModel):
    id: uuid.UUID
    device_id: uuid.UUID | None
    name: str
    provider: str
    #: vault | repository | bucket
    kind: str = "vault"
    #: None when the provider does not report it.
    offsite: bool | None = None
    immutable: bool | None = None
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
    #: Size and free space of a fixed-size location (Veeam repository); None otherwise.
    capacity_bytes: int | None = None
    free_bytes: int | None = None
    multi_user_authorization: str | None = None
    encryption: str | None = None
    cross_region_restore: bool | None = None
    public_network_access: str | None = None
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
    #: Backup data held for this item; None when the provider does not report it.
    size_bytes: int | None = None
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


class BackupPolicyRow(BackupPolicyOut):
    """A policy in the policy inventory, with where it lives and what uses it."""

    id: uuid.UUID
    vault_id: uuid.UUID
    vault_name: str
    provider: str
    #: Items stored with this policy name in its storage location.
    items_assigned: int
    observed_at: datetime | None


class BackupJobDetail(BaseModel):
    """One job with its storage location, the item it ran for and that item's recent jobs."""

    job: BackupJobOut
    provider: str
    #: The backup item the job ran for (same storage, same name); None if not found.
    item_id: uuid.UUID | None
    #: The item's other recent jobs, newest first (excludes this one).
    item_jobs: list[BackupJobOut]


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


class BackupSettingsOut(BaseModel):
    """The tenant's backup targets."""

    #: The RPO every verdict and alert for this tenant uses.
    rpo_hours: int
    #: True when the tenant has not set its own RPO.
    rpo_is_default: bool
    default_rpo_hours: int


class BackupSettingsUpdate(BaseModel):
    """Set the tenant's RPO; null returns it to the platform default."""

    rpo_hours: int | None = Field(..., ge=RPO_MIN_HOURS, le=RPO_MAX_HOURS)


class BackupFilterOption(BaseModel):
    value: str
    label: str


class BackupFilterOptions(BaseModel):
    """Values the overview filters can take, from what is stored (unfiltered)."""

    providers: list[str] = []
    subscriptions: list[str] = []
    resource_groups: list[str] = []
    locations: list[str] = []
    workload_types: list[str] = []
    #: Storage locations: value = id, label = name.
    vaults: list[BackupFilterOption] = []


class BackupStatusGroup(BaseModel):
    """Items of one subscription or region by RPO verdict."""

    #: The subscription id / region; None when the storage does not report it.
    key: str | None
    items_total: int
    items_protected: int
    items_within_rpo: int
    items_breaching_rpo: int
    items_never_backed_up: int
    items_not_protected: int
    items_last_failed: int


#: lt_24h | 1_3d | 3_7d | 7_30d | gt_30d | never
AgeBucketKey = Literal["lt_24h", "1_3d", "3_7d", "7_30d", "gt_30d", "never"]


class BackupAgeBucket(BaseModel):
    key: AgeBucketKey
    label: str
    items: int


class BackupWorkloadSummary(BaseModel):
    """Protected items of one workload type (VM, SQL database, file share ...)."""

    #: As the provider reports it; None when it does not.
    workload_type: str | None
    items_total: int
    items_protected: int
    items_within_rpo: int
    items_breaching_rpo: int
    items_never_backed_up: int
    items_not_protected: int
    items_last_failed: int
    #: Sum of the item sizes that are reported; None if none are.
    size_bytes: int | None = None
    recovery_points_total: int | None = None


class BackupFailureGroup(BaseModel):
    """Failed jobs sharing one error code."""

    #: None when the provider did not report a code.
    error_code: str | None
    failed_jobs: int
    items_affected: int
    #: Up to 10 item names, most recent failure first.
    items: list[str]
    vaults: list[str]
    operations: list[str]
    first_seen: datetime | None
    last_seen: datetime | None
    #: The latest failure's message.
    sample_message: str | None


class BackupFailureAnalysis(BaseModel):
    days: int
    failed_jobs: int
    items_affected: int
    groups: list[BackupFailureGroup]


#: pass | warn | fail | not_reported (provider did not say) |
#: not_applicable (e.g. cross-region restore on a single-region vault)
CheckStatus = Literal["pass", "warn", "fail", "not_reported", "not_applicable"]


class BackupSecurityCheck(BaseModel):
    key: str
    label: str
    status: CheckStatus
    #: What the provider reported, in words; None when not reported.
    value: str | None


class BackupSecurityLocation(BaseModel):
    vault_id: uuid.UUID
    name: str
    provider: str
    kind: str
    location: str | None
    checks: list[BackupSecurityCheck]
    passed: int
    #: Checks with a verdict (pass, warn or fail).
    evaluated: int


class BackupSecurityCheckSummary(BaseModel):
    key: str
    label: str
    passed: int = 0
    warned: int = 0
    failed: int = 0
    not_reported: int = 0
    not_applicable: int = 0


class BackupSecurityPosture(BaseModel):
    """Security settings of every storage location, checked one by one."""

    locations: list[BackupSecurityLocation]
    summary: list[BackupSecurityCheckSummary]
    #: Passed / evaluated checks across all locations, %; None when none evaluated.
    score: float | None


class BackupStoragePoint(BaseModel):
    day: date
    storage_used_bytes: int | None
    capacity_bytes: int | None
    free_bytes: int | None
    #: Storage locations with a snapshot that day.
    locations_reporting: int


class BackupStorageHistory(BaseModel):
    """Daily storage across the selected locations, from the poll snapshots."""

    days: int
    points: list[BackupStoragePoint]
    #: Days with a storage value in the window.
    history_days: int
    #: Days of history a growth estimate needs.
    min_history_days: int
    #: Least-squares growth of storage used, bytes/day: an estimate, None
    #: until ``min_history_days`` days are recorded.
    growth_bytes_per_day: float | None = None
    #: Storage used in 30 days at that rate (estimate); None as above.
    projected_used_bytes_30d: int | None = None


BackupOverview.model_rebuild()


#: ready | not_ready | failed_over (running in the recovery location)
DrReadiness = Literal["ready", "not_ready", "failed_over"]


class ReplicatedItemOut(BaseModel):
    id: uuid.UUID
    vault_id: uuid.UUID
    vault_name: str
    name: str
    source_resource_id: str | None
    replication_type: str | None
    protected_item_type: str | None
    source_location: str | None
    target_location: str | None
    protection_state: str | None
    protection_state_description: str | None
    replication_health: str | None
    failover_health: str | None
    active_location: str | None
    test_failover_state: str | None
    last_failover_at: datetime | None
    last_test_failover_at: datetime | None
    rpo_seconds: int | None
    rpo_calculated_at: datetime | None
    policy_name: str | None
    recovery_vm_name: str | None
    current_scenario: str | None
    allowed_operations: list[str]
    health_errors: list[dict]
    observed_at: datetime | None
    #: Platform DR-readiness rule (see docs); reasons say why it is not ready.
    readiness: DrReadiness
    readiness_reasons: list[str]
    #: Recovery plans this item belongs to.
    recovery_plans: list[str] = []
    #: The inventory device this item replicates (matched by resource id).
    device_id: uuid.UUID | None = None


class RecoveryPlanOut(BaseModel):
    id: uuid.UUID
    vault_id: uuid.UUID
    vault_name: str
    name: str
    primary_location: str | None
    recovery_location: str | None
    failover_deployment_model: str | None
    replication_providers: list[str]
    allowed_operations: list[str]
    current_scenario: str | None
    current_scenario_status: str | None
    last_planned_failover_at: datetime | None
    last_unplanned_failover_at: datetime | None
    last_test_failover_at: datetime | None
    groups: int
    items_total: int
    #: Member items found among the replicated items, and how many are DR-ready.
    items_found: int
    items_ready: int
    readiness: DrReadiness
    readiness_reasons: list[str]
    observed_at: datetime | None


class ReplicationRegionGroup(BaseModel):
    source_location: str | None
    target_location: str | None
    items: int
    ready: int
    unhealthy: int


class ReplicationOverview(BaseModel):
    """Disaster-recovery replication across the tenant's Recovery Services vaults."""

    #: True when an Azure Backup source exists (replication is read by it).
    configured: bool
    #: collected | empty | failed | None (not read yet) - across vaults; failed
    #: when any vault's read failed (usually the Site Recovery Reader role is missing).
    replication_status: str | None
    generated_at: datetime
    dr_test_max_age_days: int
    items_total: int
    health_normal: int
    health_warning: int
    health_critical: int
    health_not_reported: int
    failover_health_warning: int
    failover_health_critical: int
    failed_over: int
    never_tested: int
    test_overdue: int
    ready: int
    #: Ready / items, %; None without items.
    readiness_pct: float | None
    #: Largest replication lag reported, seconds.
    max_rpo_seconds: int | None
    plans_total: int
    plans_ready: int
    by_region: list[ReplicationRegionGroup]
