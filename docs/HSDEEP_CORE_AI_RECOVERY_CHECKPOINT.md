# HSDEEP CORE AI Recovery Checkpoint

## Checkpoint Metadata

| Field | Value |
|-------|-------|
| **Commit** | `e0fe8b9` |
| **Branch** | `backup/hsdeep-core-ai-pre-enterprise-hardening` |
| **Tag** | `hsdeep-core-ai-checkpoint-pre-enterprise` |
| **Date** | 2026-09-01 |
| **Previous HEAD** | `3042838` (roo) |

---

## Application State

### Summary
HSDEEP CORE AI is an enterprise NOC/SOC/IT Operations platform with significant implementation across:

- **Dashboard** — Command center, alert summary, AI operations summary, infrastructure snapshot, automation activity, recent incidents
- **Infrastructure** — Monitoring collectors (PRTG, FortiGate, Meraki, Zabbix, Azure, VMware, SNMP, SSH, WinRM)
- **Monitoring** — Metrics infrastructure, collection pipeline, Prometheus integration
- **Alerts** — Alert management with priority engine (risk scoring, business impact, confidence scoring)
- **Incidents** — Incident management system
- **Tickets** — ITSM/ticketing system
- **SOP Engine** — Standard Operating Procedures with event matcher intelligence
- **AI Copilot** — AI-assisted investigation capabilities
- **Automation** — Workflow automation engine, ServiceNow integration
- **Reports** — Reporting infrastructure
- **Subscription** — Billing service, subscription plans, payment gateways, FX rates
- **Settings** — OIDC identity provider support, user management, role-based access

### What This Checkpoint Includes
- OIDC identity provider (models, routes, schemas, services, Alembic migration)
- SOP engine enhancements (event matcher, route updates)
- Subscription/billing service updates with payment gateway support
- Frontend dashboard components (AutomationActivity, InfrastructureSnapshot, RecentIncidents, ConfidenceBar)
- FX rates module with tests
- Surprisal analysis module
- StatCard and CommandCenter UI improvements
- Entitlements system updates
- Test and requirement updates
---

## Database

| Field | Value |
|-------|-------|
| **Type** | PostgreSQL (asyncpg driver) |
| **Migration Tool** | Alembic |
| **Migration Location** | `backend/alembic/versions/` |
| **Connection** | Configured via `DATABASE_URL` env var |
| **Backup Status** | NOT APPLICABLE — No local database file; uses external PostgreSQL instance |
| **New Migration in Checkpoint** | `d4e5f6a7b8c9_add_identity_provider.py` |

---

## Frontend

| Field | Value |
|-------|-------|
| **Framework** | Next.js 16.2.9 with React 19.2.4 |
| **Language** | TypeScript 5 |
| **Styling** | Tailwind CSS 4 |
| **3D/Visualization** | Three.js, React Three Fiber, Drei |
| **Animation** | Framer Motion |
| **State** | Zustand |
| **Validation** | Zod |
| **Testing** | Vitest 4.1.11 |
| **Package Manager** | npm (package-lock.json present) |

---

## Backend

| Field | Value |
|-------|-------|
---

## Dependencies

### Python (`backend/requirements.txt`)
```
fastapi==0.115.0, uvicorn[standard]==0.30.6, sqlalchemy==2.0.35, alembic==1.13.2,
asyncpg==0.29.0, redis==5.0.8, pydantic==2.9.2, pydantic-settings==2.5.2,
python-dotenv==1.0.1, passlib[argon2]==1.7.4, python-jose[cryptography]==3.3.0,
httpx==0.27.2, apscheduler==3.10.4, cryptography==43.0.1, pysnmp==6.2.5,
asyncssh==2.14.2, pywinrm==0.4.3, azure-identity==1.17.1, stripe==15.5.1
Dev: hypothesis==6.112.0, pytest==8.3.3, pytest-asyncio==0.24.0, respx==0.21.1
```

### Node.js (`frontend/package.json`)
- next 16.2.9, react 19.2.4, three, framer-motion, lucide-react
- Dev: tailwindcss 4, typescript 5, vitest 4.1.11, eslint 9

---

## Environment Files

| File | Status | Notes |
|------|--------|-------|
| `backend/.env` | EXISTS (not backed up) | Contains secrets; see required variables below |
| `backend/.env.example` | Committed | Template with variable names |
| `frontend/.env.local` | EXISTS (not backed up) | Contains secrets |
| `frontend/.env.local.example` | Committed | Template with variable names |

### Required Backend Environment Variables
- `APP_NAME`, `APP_ENV`, `DEBUG`, `LOG_LEVEL`, `HOST`, `PORT`
- `DATABASE_URL` (PostgreSQL), `REDIS_URL` (Redis)
- `SECRET_ENCRYPTION_KEY` (Fernet key — currently missing from `.env`)
- `MAX_CONCURRENT_COLLECTIONS`, `COLLECTOR_RUN_STALE_MINUTES`
- `PRTG_DEV_*` (optional, for PRTG integration)

---

## Files Modified by This Backup Operation

1. `docs/HSDEEP_CORE_AI_RECOVERY_CHECKPOINT.md` — Created (this document)

No source code files were modified. The checkpoint commit (`e0fe8b9`) was created on the `backup/hsdeep-core-ai-pre-enterprise-hardening` branch and includes all previously uncommitted work.

| **Framework** | FastAPI 0.115.0 |
| **Server** | Uvicorn |
| **ORM** | SQLAlchemy 2.0.35 (async) |
| **Migrations** | Alembic 1.13.2 |
| **Database Driver** | asyncpg 0.29.0 |
| **Cache** | Redis 5.0.8 |
| **Auth** | python-jose (JWT), passlib (argon2) |
| **Validation** | Pydantic 2.9.2, pydantic-settings |
| **Scheduling** | APScheduler |
| **Testing** | pytest, pytest-asyncio, hypothesis, respx |
| **Billing** | Stripe 15.5.1 |
| **Monitoring** | pysnmp, asyncssh, pywinrm, azure-mgmt-monitor, pyvmomi, prometheus-client |


---

## Baseline Tests

### Command Used
```
backend/.venv/Scripts/pytest.exe backend/tests --tb=short -q --no-header
```

### Result
**FAILED** — Pre-existing environment configuration issue (not caused by this backup)

### Error
```
pydantic_core._pydantic_core.ValidationError: 1 validation error for Settings
  secret_encryption_key
    Field required [type=missing, input_value={}, input_type=dict]
```

### Root Cause
The `backend/.env` file is missing the `SECRET_ENCRYPTION_KEY` variable. This is a Fernet encryption key required by the application settings.

### Fix (for future reference)
Generate a Fernet key and add to `backend/.env`:
```python
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
```
Then set `SECRET_ENCRYPTION_KEY=<generated-key>` in `backend/.env`.

### Note
This is a **known pre-existing issue** and does not represent a regression introduced by this checkpoint.

---

## Known Issues

1. **Missing `SECRET_ENCRYPTION_KEY`** — Tests fail because the environment variable is not set in `.env`. The `.env.example` documents this variable but it has not been generated locally.
2. **`.venv` timestamp drift** — The virtual environment shows many modified files (timestamp/permission changes only, no functional changes). This is normal Windows behavior and does not affect source code.
3. **Untracked temp files** — Several log files, screenshots, and debug dumps exist in the working directory (not committed, not part of source).
