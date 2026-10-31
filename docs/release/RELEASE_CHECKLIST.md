# Release checklist (gates G1-G8)

A gate passes only with evidence recorded in `TEST_EVIDENCE.md`.

| Gate | Criteria | Status | Evidence / missing |
| --- | --- | --- | --- |
| G1 Repository and CI | Release branch; clean checkout builds; mandatory CI green on the release commit | PASSED (pending owner acceptance) | `release/cloud-saas-stabilization`; CI run 41092830411 on `a1011d47b` green (tests, lint ratchet, type-check, unit tests, build, migrations single head + round trip, secret scan, dependency check, container builds). Open item: BLK-R0-03 branch integration |
| G2 Security | Tenant isolation suite, RBAC, auth lifecycle, session revocation, audit, secure config | IN_PROGRESS | Fixed: SEC-01 (R1.1), SEC-02 (R1.2), SEC-04 (R1.4), SEC-07 (R1.7). Open: SEC-03 isolation suite, SEC-05 per-role RBAC, SEC-06 audit, SEC-08 API keys, SEC-09 secrets at rest, SEC-12 config review |
| G3 Runtime | API/worker separation, shared event transport, retry/idempotency, scheduler coordination, health | NOT_STARTED | BLK-R2-01 |
| G4 Core monitoring | PRTG ingestion, discovery, alert and incident lifecycle, dashboard on real data | NOT_STARTED | BLK-R3-01 |
| G5 Operational intelligence | Priority, correlation, RCA evidence, SOP, approved automation | NOT_STARTED | |
| G6 Commercial | MSP and enterprise hierarchy, entitlements, onboarding, reporting | NOT_STARTED | BLK-R4-01 |
| G7 Cloud | Secure deployment, WAF/API protection, central monitoring, backup/restore, performance and resilience | NOT_STARTED | BLK-R5-01 |
| G8 Pilot | MSP + 2 customers, 1 enterprise, support procedures, measured results | NOT_STARTED | |

Readiness labels (none reached yet): Engineering complete → Pilot ready →
Commercial pilot validated → GA ready.
