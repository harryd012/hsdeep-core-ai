# Release blockers

Open items that stop a pilot. Each has an owner-facing reason and the phase
that resolves it. Security items are tracked in `SECURITY_GAP_REGISTER.md`;
only pilot-blocking ones are repeated here.

| ID | Blocker | Why it blocks | Phase | Needs |
| --- | --- | --- | --- | --- |
| BLK-R0-01 | Release branch not pushed, so remote CI has not run on it | G1 needs a green remote run on the release commit | R0 | Owner approval to push |
| BLK-R0-02 | ~245 uncommitted paths in the main checkout (dashboard-v2, explorer, SOP import pipeline, edge agent) | Not on any branch, so not releasable; the edge agent is needed for customer-site collection | R0 | Owner to commit or discard |
| BLK-R1-01 | ~~No server-side access-token revocation~~ | Resolved in `431b85c85` (R1.4) | R1 | - |
| BLK-R1-02 | Support override not scoped or time-bound (SEC-02) | Cross-customer access without a recorded grant. Needs the owner to choose the grant model (self-granted break-glass with reason and expiry, or customer-approved) before code changes the superuser workflow | R1 | Owner decision |
| BLK-R2-01 | Event bus is in-process (`backend/app/services/events/event_bus.py`) | Events raised in the API process never reach a separate worker | R2 | Engineering |
| BLK-R3-01 | No collector has a successful live run in the dev database (78 PRTG sources, 0 successes) | The PRTG end-to-end workflow cannot be demonstrated | R3 | Reachable PRTG instance + credentials |
| BLK-R3-02 | ServiceNow integration has no tests and no instance | Ticketing claims unverified | R3 | ServiceNow developer instance |
| BLK-R4-01 | No provider → customer or business-unit hierarchy | MSP and enterprise pilots need it | R4 | Engineering (design approval) |
| BLK-R5-01 | Not deployed; no backup/restore drill | G7 | R5 | Render account, domain, secrets (owner) |
| BLK-R5-02 | Stripe not tested with a real account | Billing claims unverified | R4/R5 | Stripe test-mode keys (owner) |
