# Veeam Backup & Replication monitoring

Status: **Phase 3.1 shipped** (2026-11-03): read-only collector for one Veeam Backup &
Replication server per source, reported through the provider-neutral backup model
(see [azure-backup-monitoring.md](azure-backup-monitoring.md#provider-neutral-model)), so the
Backup page, dashboard row, Backup Readiness card, RPO rule and alerts work unchanged.
Not yet tested against a real backup server: **STATUS = Needs Verification** for the exact
response shapes on a live server (the tests replay the shapes of the official REST API reference).

## What it answers

For every connected backup server: does each backed-up VM or server have a recent restore
point, which objects are no longer protected (job disabled or deleted), which job runs failed
and why, how full each repository is, and which repositories are off-site or immutable.

## Setup (per backup server)

1. Veeam Backup & Replication **12 or later** (REST API on port **9419**).
2. Create a Veeam user and give it the built-in **Veeam Backup Viewer** role (read-only).
3. Infrastructure → Backup → Connect a backup provider → **Veeam Backup & Replication**: backup
   server name, port, username, password. TLS verification is off by default because Veeam
   installs a self-signed certificate; turn it on when the server has a trusted certificate.
4. The password is encrypted at rest (`SECRET_ENCRYPTION_KEY`) and never returned. The source
   polls every 15 minutes.

`config["api_version"]` overrides the `x-api-version` header (default `1.1-rev0`, served by
Veeam Backup & Replication 12.0 and later; newer servers keep serving older revisions).

## Collection

`backend/app/collectors/veeam_backup.py`. Every listing is paged with `skip`/`limit` (500) until
`pagination.total`.

| Call | Gives |
|---|---|
| `POST /api/oauth2/token` (password grant) | access token (60 min); `POST /api/oauth2/logout` at the end |
| `GET /api/v1/backupInfrastructure/repositories/states` | repositories: type, host, path, capacity, free and used GB, online |
| `GET /api/v1/jobs/states` | jobs: status (running / inactive / disabled), last result, repository, object count |
| `GET /api/v1/backups` | each backup's job and repository |
| `GET /api/v1/backups/{id}/objects` | backed-up objects (VMs, servers) and their restore point counts |
| `GET /api/v1/restorePoints?createdAfterFilter=` (7 days) | recent restore points, matched to objects by backup and name |
| `GET /api/v1/restorePoints?backupObjectIdFilter=&orderColumn=CreationTime&orderAsc=false&limit=1` | newest point of an object with none in the last 7 days (at most 300 per poll) |
| `GET /api/v1/sessions?createdAfterFilter=` (`backup_job_lookback_hours`, 72) | job runs |

Each table reports collected / empty / failed. A failed read keeps what an earlier poll
stored; if the job states cannot be read, items are not updated either (their protection state
depends on the jobs). If repositories cannot be read, the run fails.

## Mapping onto the neutral model

| Veeam | Neutral model |
|---|---|
| Repository | storage location; `kind` = `bucket` for object storage (Amazon S3, S3-compatible, Azure Blob/Archive, Google Cloud, IBM Cloud, Wasabi), else `repository`; also an inventory device (`storage`), down when offline |
| Repository capacity / free / used GB | `capacity_bytes` / `free_bytes` / `storage_used_bytes` (GB × 1024³) |
| Public cloud object storage | `offsite` = true; any other type: not reported (where a NAS or disk stands is not in the API) |
| Hardened Linux repository | `immutable` = true; object storage immutability is not in the states API: not reported |
| Backup object (VM, server) | protected item; restore point count; last backup = newest restore point |
| Job of the object's backup | `policy_name`; job disabled → `protection_paused`; backup with no job → `protection_stopped`; else `protected` |
| Job (per repository) | policy (name, workload, object count; schedule and retention not read yet) |
| Session of a job | job history: Stopped + Success / Warning / Failed → completed / completed_with_warnings / failed; canceled → cancelled; any other state → in_progress; the result message is kept for failures and warnings |

Veeam reports a result per job run, not per object, so an item's `last_backup_status` stays
empty: a failed run shows in the job history, and an object it did not back up breaches the RPO
on its own. Sessions not tied to a job (infrastructure, rescans) are skipped.

## Alert rules

All Azure rules apply (RPO, protection). Added for fixed-size storage:

| Rule | Alert |
|---|---|
| Repository free space below `backup_repository_free_warning_pct` (15 %) | warning |
| Repository free space below `backup_repository_free_critical_pct` (5 %) | critical |

Migration `c3e8a1f5d7b9` adds `capacity_bytes` and `free_bytes` to `backup_vaults` (additive).

## Security

The Veeam user needs only the Backup Viewer role. The only POSTs are the token request and
logout; every data call is a GET (tested). The password is sent only in the token request's
form body, never in a URL, header or log (tested).

## Not yet

- Per-object result of each run (task sessions), so `last_backup_status` could be filled.
- Job schedule and retention (`GET /api/v1/jobs/{id}`), and object storage immutability
  (`GET /api/v1/backupInfrastructure/repositories`).
- Malware status of restore points (`malwareStatus`), backup copy and replica jobs as their own
  views, coverage of vSphere VMs against inventory.
- **Veeam Service Provider Console** (many customers' servers through one MSP console) is the
  next connector in the plan.
