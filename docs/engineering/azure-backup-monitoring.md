# Azure Backup monitoring

Status: **B1 shipped** (collection, alerts, API, Backup page, dashboard row, Backup Readiness card).
**Phase 1 shipped** (2026-10-31): policies (schedule and retention), restore points, vault storage
used, backup coverage of Azure VMs, item detail page, site filter, CSV export, job history retention.
Next (approved plan): one provider-neutral backup model, then Veeam, AWS Backup and S3-compatible
storage connectors, then a backup storage cost/SLA advisor.

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
| Protected item with no backup within `backup_rpo_hours` (24) | critical |
| Item's last backup failed / completed with warnings | critical / warning |
| Protection stopped or paused / protection error | warning / critical |
| Vault soft delete disabled | warning |

An item whose protection was stopped resolves its RPO alert (nobody is paged about an item
that is intentionally unprotected); an item that disappears resolves all of its alerts.

## Data and API

Tables (migrations `7826ac6377f8`, `a4c8e2f6b1d3`): `backup_vaults` (incl. `storage_used_bytes`),
`backup_items` (incl. `recovery_point_count`, `oldest_recovery_point_at`), `backup_jobs`,
`backup_policies`, all tenant-scoped with cascade on tenant delete. Jobs are upserted by the
provider's job id; jobs that started more than `backup_job_retention_days` (90) ago are purged
after each save.

All routes need the `backup.read` permission (Owner, Admin, Operator, Viewer) and take the
tenant from the signed-in principal:

| Route | Returns |
|---|---|
| `GET /api/backup/overview` | KPIs, RPO verdict counts, job counts (24 h / 7 d), 14-day daily outcomes |
| `GET /api/backup/vaults` | vaults with posture and item counts |
| `GET /api/backup/items?vault_id=&rpo_state=&site_id=` | items with `rpo_state` (within / breached / never / not_protected), age, restore points, daily retention, and the inventory device and site they protect |
| `GET /api/backup/items/{id}` | one item with its policy (schedule, retention per tier) and its latest 50 jobs; 404 for another tenant's item |
| `GET /api/backup/coverage` | every Azure VM in inventory as protected / protection_stopped / unprotected / not_monitored |
| `GET /api/backup/jobs?days=&status=&limit=` | job history with error codes |

The RPO verdict is computed in one place (`app/services/backup_service.py::rpo_state`) and
matches the alert rule.

**Coverage rule.** An Azure vault backs up VMs in its own subscription, so a VM (an inventory
device whose resource id is a `Microsoft.Compute/virtualMachines` id) is **unprotected** only
when its subscription has an active Azure Backup source and no backup item names it. A VM in
a subscription with no backup source is **not monitored**: its backups cannot be seen, so it
is never reported as unprotected. An item whose protection is stopped or paused makes its VM
**protection stopped**. VMs reach inventory through the Azure monitoring source.

## Product surfaces

- **Infrastructure → Backup**: KPIs (incl. unprotected VMs and storage used), job outcomes per
  day, RPO compliance, items / coverage / jobs / vaults tables with a site filter and CSV export,
  connect form.
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
queries, per-tenant RPO and the multi-vault report. Item size is not reported by the Recovery
Services API, so per-item storage is not shown; vault storage is. Other providers (Veeam, AWS
Backup, S3-compatible storage) follow the approved plan.
