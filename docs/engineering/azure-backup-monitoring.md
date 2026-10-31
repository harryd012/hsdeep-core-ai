# Azure Backup monitoring

Status: **B1 shipped** (collection, alerts, API, Backup page, dashboard row, Backup Readiness card).
B2 (Log Analytics/KQL enrichment, configurable retention, CSV export, Backup report) and
B3 (multi-subscription scale, Blob export) are planned.

## What it answers

For every connected Azure subscription: does each protected workload have a recent good
backup, what failed and why, and are the vaults protected against deletion?

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

Each table reports `collected`, `empty` or `failed`. A failed read keeps what an earlier poll
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

Tables (migration `7826ac6377f8`): `backup_vaults`, `backup_items`, `backup_jobs`, all
tenant-scoped with cascade on tenant delete. Job history accumulates (upsert by the provider's
job id); retention is B2.

All routes need the `backup.read` permission (Owner, Admin, Operator, Viewer) and take the
tenant from the signed-in principal:

| Route | Returns |
|---|---|
| `GET /api/backup/overview` | KPIs, RPO verdict counts, job counts (24 h / 7 d), 14-day daily outcomes |
| `GET /api/backup/vaults` | vaults with posture and item counts |
| `GET /api/backup/items?vault_id=&rpo_state=` | items with `rpo_state` (within / breached / never / not_protected) and age |
| `GET /api/backup/jobs?days=&status=&limit=` | job history with error codes |

The RPO verdict is computed in one place (`app/services/backup_service.py::rpo_state`) and
matches the alert rule.

## Product surfaces

- **Infrastructure → Backup**: KPIs, job outcomes per day, RPO compliance, items / jobs /
  vaults tables, connect form.
- **Dashboard → Backup Health** row: RPO compliance, failures, job outcomes; a connect link
  until a source exists.
- **Cloud Operations → Backup Readiness**: % of protected items inside the RPO; unavailable
  (never 0) until a source reports protected items.

## Security

Read-only role; secret encrypted with `SECRET_ENCRYPTION_KEY`; tenant isolation on every
query (tested); RBAC on every route (tested). The collector only issues GET requests (tested).

## Not in B1

Azure Backup *vaults* (Data Protection, `Microsoft.DataProtection/backupVaults`), Log Analytics
queries, per-tenant RPO, retention/purge, export and the multi-vault report are B2/B3.
