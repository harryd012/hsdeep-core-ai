# Stabilization roadmap

Dependency order R0 → R1 → R2 → R3 → R4 → R5. Broad feature work waits until
the P0 phases (R0-R2) pass. Earlier batches: `docs/PLATFORM_LEDGER.md`;
audit phase order: `docs/REPOSITORY_AUDIT.md`.

| Phase | Priority | Scope (first tasks) | Exit gate |
| --- | --- | --- | --- |
| R0 Repository and release | P0 | Reconcile branches, release branch, CI on Node 24 / Python 3.12, secret scanner, inventories, green remote CI | G1 |
| R1 Security and tenancy | P0 | SEC-04 access-token revocation (jti + revocation list checked on every request); SEC-02 scoped, time-bound, reasoned support grants with awaited audit; SEC-03 cross-tenant suite over every tenant-scoped resource; SEC-07 rate limiting fails closed for signup; SEC-06 security audit events; SEC-01 remove the nil-UUID fallback | G2 |
| R2 Runtime and events | P0 | Redis Streams transport behind the existing EventBus interface (versioned envelope, consumer groups, ack, retry, dead letter, idempotency keys); worker/scheduler coordination; structured logs with correlation ids; backup/restore runbook | G3 |
| R3 Operations end to end | P1 | PRTG live validation (needs an instance); alert/incident lifecycle gaps; RCA evidence fields; SOP matching; ServiceNow tests; the 16-step E2E test against a PRTG fake, then live | G4, G5 |
| R4 MSP and enterprise | P1 | Organization hierarchy design (provider → customer, enterprise → business unit) on the existing tenant model; role mapping onto the existing RBAC; entitlement enforcement in jobs; SLA/report definitions; onboarding | G6 |
| R5 Cloud and pilot | P1 | Render staging + production, secrets, TLS, health checks, central logs, restore drill, performance and resilience tests, runbooks, pilot | G7, G8 |

Owner inputs needed along the way: push approval (R0), support-access policy
(R1), PRTG and ServiceNow test instances (R3), hierarchy design approval (R4),
Render account, domain, Stripe test keys and secrets (R5).
