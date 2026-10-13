# Backup / Restore Contract (Phase 9A)

## Actual current state: NO backup infrastructure exists

This is stated plainly because it must not be assumed otherwise:

* There is **no** `pg_dump`/`pg_restore` script, job, cron entry or documented
  procedure anywhere in this repository (a recursive search found only SOP
  *prose* mentioning backups, which is customer runbook content - not
  infrastructure).
* There is **no** managed database yet, so there is nothing to back up and no
  restore has ever been performed.
* **No backup is claimed to be configured.**

Everything below is a contract for Phase 9B to implement, not a description of
existing capability.

## Targets

| Item | Value | Status |
| --- | --- | --- |
| RPO (max acceptable data loss) | `TBD - deployment owner decision required` | **not approved** |
| RTO (max acceptable downtime) | `TBD - deployment owner decision required` | **not approved** |
| Retention (automated backups) | `TBD - deployment owner decision required` | **not approved** |
| Retention (PITR window) | `TBD - deployment owner decision required` | **not approved** |
| Encryption at rest | required - managed DB encryption **and** encrypted dumps if dumped outside the DB | contract |
| Restore verification cadence | at least once before go-live, then per release train | contract |
| Off-region copy | `TBD - deployment owner decision required` | **not approved** |

RPO/RTO/retention are deliberately **not invented here**. A number guessed by an
engineer in a document is worse than an explicit TBD: it silently becomes an SLA.

## Required Phase 9B implementation

1. **Managed PostgreSQL automated backups** enabled on the production database
   with the approved retention window, plus **PITR** (continuous WAL archiving).
2. **Encryption at rest** for the database and for any dump written to object
   storage, plus access control restricted to the deployment's service identity.
3. **Backup monitoring**: a failed or missed backup must raise an alert (the
   project already exposes Prometheus metrics at `/metrics` and structured logs
   to stdout, so either path can feed the alert).
4. **Documented restore runbook** naming the exact commands for the chosen
   provider, the person/role accountable, and the communication path during a
   restore.
5. **A completed restore-verification drill** (procedure below) recorded with its
   output before the first customer tenant is onboarded.

## Restore verification procedure (executable once the managed DB exists)

Requires a **new, empty, isolated** database. Never restore over, or into, a
database that serves traffic, and never point a verification at production.

1. Provision a scratch PostgreSQL instance (same major version as production).
2. Restore the most recent automated backup into it, then replay WAL to a chosen
   timestamp (PITR). Record the wall-clock duration: that is the measured RTO.
3. Point a **read-only** API process at the restored database:
   `DATABASE_URL=<restored> READINESS_REQUIRE_REDIS=false RUNTIME_MODE=api uvicorn app.main:app`.
4. Verify integrity with read-only checks (no writes):
   * `/health/live` returns 200 and `/health/ready` returns 200 (database up);
   * row counts for the core tables (`tenants`, `users`, `incidents`, `alerts`)
     match the source database as of the restore timestamp;
   * Alembic reports the expected revision:
     `docker run --rm --env-file <env> <image> alembic current` -> should equal
     the revision that was deployed before the incident/backup (`e7b2c9f4a1d3`
     at the time of writing);
   * a representative tenant-scoped API read returns the tenant's own rows and
     another tenant's row is still refused (tenant isolation must survive a
     restore).
5. Record, in the deployment log: backup id, restore timestamp, measured RTO,
   row-count deltas, any errors, and the operator's name.
6. Destroy the scratch instance (and its dump) once the record is captured.

## Explicit non-goals for Phase 9A

* No customer or production data is touched by this document.
* No backup infrastructure is created and no restore is claimed to have run.
* RPO/RTO numbers are **not** set here.
