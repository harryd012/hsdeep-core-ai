# Azure Backup monitoring

Status: **B1 shipped** (collection, alerts, API, Backup page, dashboard row, Backup Readiness card).
**Phase 1 shipped** (2026-10-31): policies (schedule and retention), restore points, vault storage
used, backup coverage of Azure VMs, item detail page, site filter, CSV export, job history retention.
**Phase 2 shipped** (2026-11-03): one provider-neutral backup model (shared status vocabulary,
storage kind, off-site and immutable flags, item size, backup providers found from the collector
registry) and an RPO per customer (tenant), editable on the Backup page.
**Phase 3.1 shipped** (2026-11-03): Veeam Backup & Replication connector
([veeam-backup-monitoring.md](veeam-backup-monitoring.md)), plus capacity and free space of
fixed-size storage and a free-space alert.
**BR-1 slice 1 shipped** (2026-11-03), Backup Command Center
([plan](../design/BACKUP_RECOVERY_AND_LOG_ANALYTICS_PLAN.md)): filters on every read (provider,
subscription, resource group, region, storage, workload), items by workload type, failure analysis
by error code, a security & compliance check per storage location (adds multi-user authorization,
encryption key, cross-region restore and public network access), and daily storage history with a
growth estimate.
**BR-1 slice 2 shipped** (2026-11-03): *Protect → Backup Center* navigation, overview time range
(24 h – 90 days), status by subscription and region, recovery-point age distribution, KPIs that
open the matching filtered view, and the **Backup Instances** inventory page.
Next (approved plan): Veeam Service Provider Console, AWS Backup and S3-compatible storage
connectors, then a backup storage cost/SLA advisor.

## What it answers

For every connected Azure subscription: does each protected workload have a recent good
backup, which VMs have no backup at all, what failed and why, how many restore points exist and
how long they are kept, how much storage the vaults use, and are the vaults protected against
deletion?

## Setup (per subscription)

1. In Microsoft Entra ID, create an app registration and a client secret.
2. Give its service principal the built-in **Backup Reader** role on the subscription
   (or on each Recovery Services vault). Nothing else is needed: every call is a read.
3. In HSDEEP CORE AI open **Infrastructure → Backup → Connect Azure Backup** and enter the
   directory (tenant) ID, subscription ID, application (client) ID and the secret. The
   secret is encrypted at rest and never returned by the API.
4. The first collection runs immediately; after that every 15 minutes.

The form creates an ordinary monitoring source (`source_type = "azure_backup"`) through
`POST /api/monitoring/sources`, so test-connection, sync, pause and delete work like every
other source.

## Collection

`backend/app/collectors/azure_backup.py`, Azure Resource Manager REST (no extra SDK; token
from `azure-identity`, requests with `httpx`), following `nextLink` paging:

| Call | Gives |
|---|---|
| `GET /subscriptions/{sub}/providers/Microsoft.RecoveryServices/vaults` (2023-04-01) | vaults, soft delete, immutability, storage redundancy |
| `GET {vault}/backupProtectedItems` (2023-04-01) | protected items, protection state, last backup verdict and time, policy |
| `GET {vault}/backupJobs?$filter=startTime eq '…' and endTime eq '…'` | jobs in the last `backup_job_lookback_hours` (72) |
| `GET {vault}/backupPolicies` (2023-04-01) | schedule (frequency, start times UTC) and retention per tier (daily days, weekly weeks, monthly months, yearly years); a SQL/SAP workload policy's full-backup tier is read from `subProtectionPolicy` |
| `GET {vault}/usages` (2023-04-01) | backup storage used: the sum of the `*StorageUsage` byte meters (LRS, GRS, ...), excluding the `*DedupStorageUsage` meters, which re-count the same data |

Restore point count and oldest restore point come from each protected item's `extendedInfo`, so
they cost no extra call. Values Azure does not return stay empty ("Not reported").

Each table (`vaults`, `items`, `jobs`, `policies`, `usage`) reports `collected`, `empty` or `failed`. A failed read keeps what an earlier poll
stored; a failed vault listing fails the run instead of reporting an empty estate. Values Azure
did not return stay empty ("Not reported"), never defaulted.

Each vault is also stored as a `cloud_resource` device, so vaults appear in inventory, count
toward Cloud Operations, and backup alerts link to them.

## Alert rules

Evaluated on every collection as sensor readings (`bk:` prefix), so alerts open, escalate into
incidents, notify and auto-resolve through the existing pipeline, exactly like firewall state.

| Rule | Alert |
|---|---|
| Protected item with no backup within the tenant's RPO (its own, else `backup_rpo_hours`, 24) | critical |
| Item's last backup failed / completed with warnings | critical / warning |
| Protection stopped or paused / protection error | warning / critical |
| Vault soft delete disabled | warning |
| Fixed-size storage (Veeam repository) free space below 15 % / 5 % | warning / critical |

An item whose protection was stopped resolves its RPO alert (nobody is paged about an item
that is intentionally unprotected); an item that disappears resolves all of its alerts.

## Provider-neutral model

Every backup connector produces the same shapes (`app/collectors/base.py`):
`NormalizedBackupVault` (any storage location), `NormalizedBackupItem`, `NormalizedBackupJob` and
`NormalizedBackupPolicy`, and declares `backup_provider` on its collector class. Nothing outside
the collector knows the vendor.

| Field | Values | Azure Backup maps |
|---|---|---|
| vault `kind` | vault / repository / bucket | `vault` |
| vault `offsite` | true / false / null | geo-redundant storage -> true; LRS / ZRS -> false |
| vault `immutable` | true / false / null | immutability `Locked` -> true; `Unlocked` / `Disabled` -> false (unlocked can be switched off) |
| item `size_bytes` | bytes / null | not reported by Recovery Services (null) |
| item `protection_state` | protected, protection_error, ir_pending, protection_stopped, protection_paused, other | Azure states |
| item `last_backup_status` | completed, completed_with_warnings, failed, in_progress, other | Azure verdicts |
| job `status` | completed, completed_with_warnings, failed, in_progress, cancelled, other | Azure job states |

The vocabulary lives in `app/collectors/backup_vocabulary.py`. The saver passes every item and
job through it before storing or judging them, so a vendor term a connector did not map is
stored as `other` and never reaches the RPO rule, the alerts or the API. The backup views list
sources from every registered collector with a `backup_provider` (`backup_source_types()`), so a
new connector needs no change to the service, API or page. Coverage stays Azure-specific: it
matches Azure VM resource ids.

**RPO per customer.** `backup_settings` holds one optional `rpo_hours` (1-720) per tenant. The
alert rule (`backup_saver`) and every verdict (`backup_service`) resolve it through
`app/services/backup_settings.py`, so they always agree; a tenant without its own RPO uses
`backup_rpo_hours`. A change applies to verdicts at once and to alerts from the next collection.

## Data and API

Tables (migrations `7826ac6377f8`, `a4c8e2f6b1d3`, `b7d2f4a9c1e6`, `c3e8a1f5d7b9`, `d4f9b2c6e8a1`):
`backup_vaults` (incl. `storage_used_bytes`, `kind`, `offsite`, `immutable`, capacity and free space,
`multi_user_authorization`, `encryption`, `cross_region_restore`, `public_network_access`),
`backup_items` (incl. `recovery_point_count`, `oldest_recovery_point_at`, `size_bytes`),
`backup_jobs`, `backup_policies`, `backup_storage_snapshots` (one row per storage location per UTC
day, the last poll of the day wins, kept `backup_snapshot_retention_days` = 400), `backup_settings`,
all tenant-scoped with cascade on tenant delete. `b7d2f4a9c1e6` backfills `offsite` and `immutable`
for existing Azure vaults from their stored redundancy and immutability. Jobs are upserted by the
provider's job id; jobs that started more than `backup_job_retention_days` (90) ago are purged
after each save.

Reads need the `backup.read` permission (Owner, Admin, Operator, Viewer); changing the RPO needs
`backup.write` (Owner, Admin) and is audited (`backup.settings.updated`, old and new value). Every
route takes the tenant from the signed-in principal:

| Route | Returns |
|---|---|
| `GET /api/backup/overview?days=1-90` | KPIs, RPO verdict counts, job counts (24 h / 7 d), 14-day daily outcomes, items by workload type (`workloads`), subscription and region (`by_subscription`, `by_region`), protected items by newest-backup age (`recovery_point_age`), job counts over the range (`jobs_range`), and the values each filter can take (`filter_options`) |
| `GET /api/backup/vaults` | vaults with posture and item counts |
| `GET /api/backup/failures?days=` | failed jobs in the window grouped by error code: jobs, items, storage, first / last seen, latest message |
| `GET /api/backup/security` | per storage location: soft delete, immutability, off-site copy, multi-user authorization, cross-region restore, encryption, public network access as pass / warn / fail / not_reported / not_applicable, and the share of passing checks |
| `GET /api/backup/storage/history?days=7-365` | storage used / capacity / free per day; growth (least squares, bytes/day) and a 30-day projection once 14 days are recorded, labelled as estimates |
| `GET /api/backup/items?vault_id=&rpo_state=&site_id=&q=&protection_state=&last_backup_status=&policy_name=` | items with `rpo_state` (within / breached / never / not_protected), age, restore points, daily retention, and the inventory device and site they protect |
| `GET /api/backup/items/{id}` | one item with its policy (schedule, retention per tier) and its latest 50 jobs; 404 for another tenant's item |
| `GET /api/backup/coverage` | every Azure VM in inventory as protected / protection_stopped / unprotected / not_monitored |
| `GET /api/backup/jobs?days=&status=&limit=` | job history with error codes |
| `GET /api/backup/settings` | the tenant's RPO, whether it is the default, and the default |
| `PUT /api/backup/settings` `{"rpo_hours": 1-720 \| null}` | sets the tenant's RPO; null returns it to the default (`backup.write`) |

**Filters.** `overview`, `vaults`, `items`, `jobs`, `failures`, `security` and `storage/history`
take the same optional query parameters: `provider`, `subscription_id`, `resource_group`,
`location`, `storage_id` (narrow the storage locations; names compare case-insensitively) and
`workload_type` (narrows items, and jobs through the item of the same name in the same storage).
A filtered overview has `filtered: true` and leaves `unprotected_resources` out (coverage is about
inventory VMs and cannot be narrowed by storage or workload).

**Security checks** never pass a setting the provider did not report. Locked immutability passes;
unlocked (can be switched off) is "review". Cross-region restore is "not applicable" for a
single-region Azure vault. Encryption always passes for Azure (backup data is always encrypted at
rest); the key type (customer- or platform-managed) is reported, not judged.

The RPO verdict is computed in one place (`app/services/backup_service.py::rpo_state`) and
matches the alert rule.

**Coverage rule.** An Azure vault backs up VMs in its own subscription, so a VM (an inventory
device whose resource id is a `Microsoft.Compute/virtualMachines` id) is **unprotected** only
when its subscription has an active Azure Backup source and no backup item names it. A VM in
a subscription with no backup source is **not monitored**: its backups cannot be seen, so it
is never reported as unprotected. An item whose protection is stopped or paused makes its VM
**protection stopped**. VMs reach inventory through the Azure monitoring source.

## Product surfaces

- **Protect → Backup Center → Backup Instances** (`/infrastructure/backup/instances`): every
  protected item with search, inventory filters (RPO state, protection, last result, policy) and
  the Command Center filters, optional columns (remembered per browser), paging, CSV export and a
  details drawer. Filters live in the URL, so a view can be bookmarked or shared. Read-only.
- **Protect → Backup Center → Overview** (`/infrastructure/backup`, tab in the URL hash): time range,
  refresh, filter bar (shown when a filter has a choice), KPIs (incl. unprotected VMs, storage used, off-site and immutable
  vaults), job outcomes per day, RPO compliance with the customer's RPO target (editable with
  `backup.write`), items / coverage / jobs / vaults tables (vaults show provider and off-site copy)
  with a site filter and CSV export, a Workloads table, a Failures tab (by error code), a Security
  tab (check matrix per storage location), a storage-used trend on the Storage tab, connect form.
- **Infrastructure → Backup → item** (`/infrastructure/backup/items/{id}`): protection, restore
  points, policy schedule and retention, and the item's job history.
- **Dashboard → Backup Health** row: RPO compliance, failures, job outcomes; a connect link
  until a source exists.
- **Cloud Operations → Backup Readiness**: % of protected items inside the RPO; unavailable
  (never 0) until a source reports protected items.

## Security

Read-only role; secret encrypted with `SECRET_ENCRYPTION_KEY`; tenant isolation on every
query (tested); RBAC on every route (tested). The collector only issues GET requests (tested).

## Not yet

Azure Backup *vaults* (Data Protection, `Microsoft.DataProtection/backupVaults`), Log Analytics
queries and the multi-vault report. Item size is not reported by the Recovery Services API, so
per-item storage is not shown; vault storage is. RPO is per tenant, not yet per site or per item.
Other providers (Veeam, AWS Backup, S3-compatible storage) follow the approved plan and plug into
the provider-neutral model above.
