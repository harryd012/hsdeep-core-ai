# P0 Hardening Plan — HSDEEP CORE AI
> Written by planning agent after reading all referenced files.
> Implementer: follow items IN ORDER. Each item leaves the codebase in a buildable state.

---

## Legend
- **File paths** are absolute or relative to the project root `I:\AI-LAB\hsdeep-core-ai\`.
- **Verify** commands run from the project root unless noted.
- Python: `backend\.venv\Scripts\python.exe`
- Backend start (already running): `http://127.0.0.1:8000`
- Frontend start (already running): `http://localhost:3000`

---

## What was found (per-task current state)

### TASK 1 — Fast topology summary
- `TopologyGraphService.build()` fetches **full rows** for devices, sites, sources, sensors, and relationships (up to `_FETCH_LIMIT = 100_000` each) just to count them. On a large tenant this is the 2-second bottleneck.
- `DeviceRepository` already has `count(tenant_id, ...)` ✅
- `SiteRepository` already has `count(tenant_id)` ✅
- `MonitoringSourceRepository` already has `count(tenant_id, ...)` ✅
- `SensorRepository` has `count_for_tenant(tenant_id, ...)` ✅ (different name — note it)
- `InterfaceRepository` has `count_for_tenant(tenant_id)` ✅
- `ResourceRelationshipRepository` already has `count(tenant_id, ...)` ✅
- `DeviceRepository` has `counts_by_status(tenant_id)` returning `dict[str, int]` — usable for `by_health` ✅
- No `GET /api/infrastructure/topology/summary` endpoint exists yet — must be created.
- Schema file `backend/app/schemas/topology_graph.py` exists; new schema goes there.

### TASK 2 — Pagination for topology graph
- `TopologyGraphService.build(tenant_id)` currently takes **only `tenant_id`** — no pagination params.
- `DeviceRepository.list()` already accepts `limit`, `offset`, `site_id`, `device_type` ✅
- The route `GET /api/infrastructure/topology/graph` passes no pagination to `build()` — must be added.
- `_FETCH_LIMIT = 100_000` class constant — must NOT be changed. The new API `limit` param defaults to 100.
- Response schema `TopologyGraphOut` already has a `summary.devices_total` field (the count); the pagination envelope `{limit, offset, total_devices}` will be added as a top-level `pagination` key, or embedded in `summary`. Decision: embed in `summary` to avoid a breaking schema change — add `limit`, `offset`, `total_devices` to `TopologyGraphSummaryOut`.

### TASK 3 — Frontend topology summary-first
- `frontend/src/lib/topologyApi.ts` has `getTopologyGraph()` and `getLiveTopology()` — no summary function.
- `infrastructure-topology/page.tsx` calls `getTopologyGraph()` in a `useEffect`; graph renders when `graph !== null`.
- The page shows summary stats from `graph.summary` — today these only appear after the full graph loads.
- No `30_000ms` timeout override is wired — `apiFetch` uses `REQUEST_TIMEOUT_MS = 10_000` globally.
- `apiFetch<T>(path, init, timeoutMs?)` accepts an optional third `timeoutMs` arg ✅ — use this.

### TASK 4 — Billing portal link
- `PaymentMethod.tsx` has **two** `<a href="/api/billing/portal">` anchors — one in the `hasPaymentMethod` branch, one in the else branch.
- A plain `<a href>` fires a GET that returns a redirect or JSON; the browser follows the redirect to Stripe but on error it renders raw JSON in the tab.
- The fix: replace both anchors with `<button>` that calls `apiFetch('/api/billing/portal')` (POST or GET — need to verify backend endpoint method) and `window.open(url)` on the returned URL.

### TASK 5 — SOP steps display
- Backend: `GET /api/sops` → `SOPOut[]` — **does NOT include `versions`** (see `SOPOut` schema).
- Backend: `GET /api/sops/{id}` → `SOPDetailOut` — **includes `versions: list[SOPVersionOut]`**, each with `steps: list[dict]`.
- Frontend `SOP` interface in `api.ts` inherits `versions: SOPVersion[]` — this is **wrong for the list response** but harmless (the list API returns `SOPOut` without versions, so `versions` is undefined at runtime).
- In `sop-engine/page.tsx`, the card renders `(sop.versions?.[0]?.steps.length ?? 0)` for step count — this is always 0/undefined from the list response. **Not a blocking bug** (shows 0 steps), but misleading.
- The `toggleView` function correctly calls `getSop(sop.id)` and accesses `detail.versions?.[detail.versions.length - 1]` — **this is correct** already.
- The actual broken case: the steps empty state shows `"Step details unavailable{(steps !== undefined) ? " for this SOP." : " right now."}"` — the condition is backwards (steps is `[]` after load, not `undefined`, so it always shows "for this SOP." even when the real issue is no steps configured).
- Fix needed: (1) step-count on cards should be removed or show "—" (no versions in list response); (2) empty steps message should say "No procedure steps configured"; (3) `detailSteps` stores `string[]` empty array — code correctly accesses `v?.steps.map(st => st.action ?? ...)` using optional chaining on `v`.

### TASK 6 — Command center data consistency
- Backend `CCMetrics` fields: `total_agents, active_agents, assigned_agents, working_agents, total_tasks, queued, working, verifying, completed, failed, critical`
- Frontend `CCMetrics` interface (api.ts): **exact same fields** ✅ — no mismatch.
- `buildAiOperations()` reads `metrics?.working` → `activeTasks`, `metrics?.queued` → `queuedTasks`, `metrics?.failed` → `failedTasks` ✅
- `CommandCenterOverview` frontend interface matches backend schema exactly ✅
- **No field-name mismatches found.** The only risk area: `system_online` bool — present in both ✅.
- Action: add a comment noting verified, add a runtime guard `?? null` on `metrics` fields in `buildAiOperations` (already done in the adapter). No code change needed beyond adding `| null` to numeric fields that default to 0 in backend but might be absent.

### TASK 7 — Sensor state differentiation
- `monitoring/page.tsx` currently uses a three-way color map: `up`→green, `warning`→amber, `down/anything-else`→red.
- Backend sensor `status` can be: `up`, `down`, `warning`, `unknown` (from `SensorRepository.counts_by_status`).
- No HEALTHY/WARNING/CRITICAL/DOWN/STALE/UNKNOWN/COLLECTION_FAILED/NOT_CONFIGURED differentiation exists — all non-up states render the same red.
- `formatSensorValue` exists but has no freshness-based state logic.
- Fix: add `classifySensorState()` helper in a new shared util file, use it in the monitoring page table.

### TASK 8 — FreshnessIndicator
- No `FreshnessIndicator` component exists anywhere in `frontend/src/`.
- `topologyApi.ts` already defines `LiveFreshness` with `state: string` values matching the spec: `fresh | aging | stale | expired | never_collected | unavailable` — TASK 8 needs to extend this to the full set.
- The topology page's `NodeDrawer` shows `last_seen_at` as a locale string — no freshness indicator there yet.
- The monitoring page shows sensor rows — no freshness indicator there yet.

---

## Implementation Plan

---

- [ ] **1. Add `TopologyQuickSummary` schema to `topology_graph.py`**

  Add `TopologyQuickSummary` Pydantic model with all required fields. Also add `pagination` fields to `TopologyGraphSummaryOut` (for Task 2). Both additions go in the same file to keep schema coupling minimal.

  **Fields for `TopologyQuickSummary`:**
  ```python
  class TopologyQuickSummary(BaseModel):
      tenant_id: str
      generated_at: str
      devices_total: int
      sites_total: int
      monitoring_sources_total: int
      relationships_total: int
      interfaces_total: int
      sensors_total: int
      by_health: dict[str, int]   # from DeviceRepository.counts_by_status
      status: str                  # "ready" | "not_discovered" | "partial"
  ```

  **Files:** `backend/app/schemas/topology_graph.py`

  **Verify:** `cd backend && .venv\Scripts\python.exe -c "from app.schemas.topology_graph import TopologyQuickSummary; print('OK')"`

---

- [ ] **2. Add `GET /api/infrastructure/topology/summary` route (backend)**

  In `backend/app/api/routes/infrastructure.py`:
  - Import `TopologyQuickSummary` from `app.schemas.topology_graph`.
  - Import the repos already available as dependencies: `DeviceRepository`, `SiteRepository`, `MonitoringSourceRepository`, `SensorRepository`, `InterfaceRepository`, `ResourceRelationshipRepository`.
  - Add a new dependency factory `get_resource_relationship_repository`.
  - Add the endpoint **above** the existing `@router.get("/topology/graph")` route:

  ```python
  @router.get("/topology/summary", response_model=TopologyQuickSummary)
  async def get_topology_summary(
      tenant_id: uuid.UUID = Depends(get_tenant_id),
      device_repo: DeviceRepository = Depends(get_device_repository),
      site_repo: SiteRepository = Depends(get_site_repository),
      source_repo: MonitoringSourceRepository = Depends(...),
      sensor_repo: SensorRepository = Depends(...),
      interface_repo: InterfaceRepository = Depends(...),
      rel_repo: ResourceRelationshipRepository = Depends(...),
  ):
  ```

  - Call: `device_repo.count(tenant_id)`, `site_repo.count(tenant_id)`, `source_repo.count(tenant_id)`, `sensor_repo.count_for_tenant(tenant_id)` (**note different name**), `interface_repo.count_for_tenant(tenant_id)`, `rel_repo.count(tenant_id)`.
  - Call `device_repo.counts_by_status(tenant_id)` for `by_health`.
  - Derive `status`: `"ready"` if `devices_total > 0`, else `"not_discovered"`.
  - All 6 count queries run via `asyncio.gather()` so they execute concurrently — target < 500ms.
  - `generated_at` = `datetime.now(timezone.utc).isoformat()`.

  **Files:** `backend/app/api/routes/infrastructure.py`

  Also add missing imports:
  - `from app.repositories.interface_repository import InterfaceRepository`
  - `from app.repositories.sensor_repository import SensorRepository`
  - `from app.repositories.resource_relationship_repository import ResourceRelationshipRepository`
  - `from app.schemas.topology_graph import TopologyQuickSummary`
  - `import asyncio`

  **Verify:**
  ```
  cd backend
  .venv\Scripts\python.exe -c "from app.api.routes.infrastructure import router; print('OK')"
  # Then hit the live API:
  curl "http://127.0.0.1:8000/api/infrastructure/topology/summary?tenant_id=<your-tenant-id>"
  ```
  Expected: JSON with all count fields, response time < 500ms.

---

- [ ] **3. Add pagination params to `TopologyGraphService.build()` and wire to route (backend)**

  **3a. Modify `TopologyGraphService.build()`** in `backend/app/services/topology_graph_service.py`:

  Change signature to:
  ```python
  async def build(
      self,
      tenant_id: uuid.UUID,
      *,
      limit: int = 100,
      offset: int = 0,
      node_type: str | None = None,
      site_id: uuid.UUID | None = None,
  ) -> dict[str, Any]:
  ```

  Inside `build()`:
  - Change `await self.devices.list(tenant_id, limit=self._FETCH_LIMIT)` to `await self.devices.list(tenant_id, limit=limit, offset=offset, site_id=site_id)`.
  - After the list call, add a separate total count: `total_devices = await self.devices.count(tenant_id, site_id=site_id)`.
  - If `node_type` is provided, filter the `nodes` list after building it (client-side filter on the already-fetched data — sites and sources are small and not paginated separately; only devices are paginated).
  - In the returned dict, add a `"pagination"` key: `{"limit": limit, "offset": offset, "total_devices": total_devices}`.
  - Do NOT touch `_FETCH_LIMIT` — it remains as an upper guard if the constant is ever used elsewhere.

  **3b. Update `TopologyGraphSummaryOut`** in `backend/app/schemas/topology_graph.py` — add `pagination` as a top-level sibling field to `TopologyGraphOut`:
  ```python
  class TopologyPaginationOut(BaseModel):
      limit: int
      offset: int
      total_devices: int
  ```
  Add `pagination: TopologyPaginationOut` to `TopologyGraphOut`.

  **3c. Update the route** in `backend/app/api/routes/infrastructure.py`:
  ```python
  @router.get("/topology/graph", response_model=TopologyGraphOut)
  async def get_topology_graph(
      tenant_id: uuid.UUID = Depends(get_tenant_id),
      limit: int = Query(default=100, ge=1, le=500),
      offset: int = Query(default=0, ge=0),
      node_type: str | None = Query(default=None),
      site_id: uuid.UUID | None = Query(default=None),
      service: TopologyGraphService = Depends(get_topology_graph_service),
  ):
      return await service.build(tenant_id, limit=limit, offset=offset, node_type=node_type, site_id=site_id)
  ```

  **Gotcha:** `TopologyLiveService.build()` also calls the graph internally — check whether it calls `TopologyGraphService.build()` or does its own thing. If it delegates to the graph service, ensure the call still compiles. (From reading: `TopologyLiveService` is its own class, not a subclass — safe.)

  **Files:**
  - `backend/app/services/topology_graph_service.py`
  - `backend/app/schemas/topology_graph.py`
  - `backend/app/api/routes/infrastructure.py`

  **Verify:**
  ```
  cd backend
  .venv\Scripts\python.exe -c "from app.services.topology_graph_service import TopologyGraphService; print('OK')"
  curl "http://127.0.0.1:8000/api/infrastructure/topology/graph?tenant_id=<id>&limit=10&offset=0"
  ```
  Response must include top-level `"pagination": {"limit": 10, "offset": 0, "total_devices": <n>}`.

---

- [ ] **4. Add `getTopologySummary()` to `topologyApi.ts` and update topology page (frontend)**

  **4a. Add to `frontend/src/lib/topologyApi.ts`:**
  ```typescript
  export interface TopologyQuickSummary {
    tenant_id: string;
    generated_at: string;
    devices_total: number;
    sites_total: number;
    monitoring_sources_total: number;
    relationships_total: number;
    interfaces_total: number;
    sensors_total: number;
    by_health: Record<string, number>;
    status: string;
  }

  export function getTopologySummary(
    tenantId: string = DEFAULT_TENANT_ID
  ): Promise<TopologyQuickSummary> {
    return apiFetch<TopologyQuickSummary>(
      `/api/infrastructure/topology/summary?${withTenant(tenantId).toString()}`
    );
  }
  ```

  **4b. Update `frontend/src/app/(dashboard)/infrastructure-topology/page.tsx`:**

  - Add state: `const [summary, setSummary] = useState<TopologyQuickSummary | null>(null)`.
  - In the `useEffect`, call `getTopologySummary()` first (fast path), set `summary` when it resolves.
  - Call `getTopologyGraph()` separately with the `30_000ms` timeout override:
    ```typescript
    import { apiFetch, DIAGNOSTIC_TIMEOUT_MS } from "@/lib/api";
    // ... use apiFetch directly with 30_000ms, or pass timeoutMs to getTopologyGraph
    ```
    The cleanest approach: add an optional `timeoutMs` parameter to `getTopologyGraph()` in `topologyApi.ts`:
    ```typescript
    export function getTopologyGraph(
      tenantId: string = DEFAULT_TENANT_ID,
      timeoutMs?: number
    ): Promise<TopologyGraph> {
      return apiFetch<TopologyGraph>(
        `/api/infrastructure/topology/graph?${withTenant(tenantId).toString()}`,
        undefined,
        timeoutMs
      );
    }
    ```
    Then call `getTopologyGraph(DEFAULT_TENANT_ID, 30_000)`.
  - Show summary cards immediately (even when graph is still loading or `graphError` is set):
    ```tsx
    {summary && (
      <section style={{ ... }}>
        <strong>QUICK SUMMARY</strong>
        <Stat label="Devices" value={summary.devices_total} />
        <Stat label="Sites" value={summary.sites_total} />
        {/* ... other counts */}
        {graph === null && !graphError && (
          <span style={{ color: "var(--muted)", fontSize: 11 }}>
            Full graph loading…
          </span>
        )}
      </section>
    )}
    ```
  - Keep **all existing graph rendering code** exactly as-is — the graph section renders only when `graph !== null`, as it does today.
  - Do NOT change `REQUEST_TIMEOUT_MS` — only this single call gets 30s via the optional argument.

  **Files:**
  - `frontend/src/lib/topologyApi.ts`
  - `frontend/src/app/(dashboard)/infrastructure-topology/page.tsx`

  **Verify:** Open browser, navigate to `/infrastructure-topology`. Summary cards (device/site counts) appear immediately before the full graph loads. Network tab: `/topology/summary` resolves < 500ms; `/topology/graph` may take longer but does not block the summary display.

---

- [ ] **5. Fix billing portal link in `PaymentMethod.tsx`**

  **Current state:** Two `<a href="/api/billing/portal">` elements — one inside the `hasPaymentMethod` branch and one in the else branch.

  **Fix:** Replace both `<a>` anchors with `<button>` elements that:
  1. Import `apiFetch` from `@/lib/api`.
  2. Add state variables: `const [portalLoading, setPortalLoading] = useState(false)` and `const [portalError, setPortalError] = useState<string | null>(null)`.
  3. Implement handler:
     ```typescript
     async function handlePortalClick() {
       setPortalLoading(true);
       setPortalError(null);
       try {
         const result = await apiFetch<{ url?: string; detail?: string }>(
           '/api/billing/portal'
         );
         if (result.url) {
           window.open(result.url, '_blank', 'noopener,noreferrer');
         } else {
           setPortalError('Billing portal is not configured. Contact your administrator.');
         }
       } catch (err) {
         const msg = err instanceof Error ? err.message : String(err);
         if (msg.includes('404') || msg.includes('not configured') || msg.includes('Stripe')) {
           setPortalError('Billing portal is not configured. Contact your administrator.');
         } else {
           setPortalError('Could not open billing portal. Please try again.');
         }
       } finally {
         setPortalLoading(false);
       }
     }
     ```
  4. Replace both `<a>` tags with:
     ```tsx
     <button
       onClick={handlePortalClick}
       disabled={portalLoading}
       style={{ fontSize: 12, color: "var(--cyan)", background: "transparent", border: "1px solid var(--cyan)", borderRadius: 6, padding: "6px 12px", cursor: portalLoading ? "wait" : "pointer", display: "inline-flex", alignItems: "center", gap: 4 }}
     >
       {portalLoading ? 'Opening…' : 'Update'} <ExternalLink size={11} />
     </button>
     ```
     (Use `'Add payment method'` label for the else-branch button.)
  5. Show `portalError` below the card:
     ```tsx
     {portalError && (
       <p style={{ marginTop: 8, fontSize: 11, color: "var(--red)" }}>{portalError}</p>
     )}
     ```

  **Gotcha:** `apiFetch` requires auth — `PaymentMethod` is already inside the dashboard, so a token is present. The billing portal endpoint may return a redirect or a JSON body depending on backend implementation. Handle both: check for `result.url`, otherwise show an honest error.

  **Files:** `frontend/src/components/subscription/PaymentMethod.tsx`

  **Verify:** Build compiles (`cd frontend && npm run build` — or `npx tsc --noEmit`). In browser, clicking "Update" / "Add payment method" no longer navigates the tab; it either opens the portal URL or shows an error message.

---

- [ ] **6. Fix SOP steps display**

  **Current state:**
  - `listSops()` returns `SOPOut[]` — backend does NOT include `versions` in the list response.
  - The `SOP` TypeScript interface incorrectly declares `versions: SOPVersion[]` (it will always be `undefined` at runtime for list items).
  - Card step-count: `(sop.versions?.[0]?.steps.length ?? 0)` — always renders `0`.
  - `toggleView` correctly calls `getSop(sop.id)` and accesses `detail.versions?.[detail.versions.length - 1]` ✅.
  - Steps empty message: uses backwards condition — renders "for this SOP." even when `steps` is `[]` (loaded, zero steps), not "right now." (load error).

  **Fixes:**

  **Fix A — Update `SOP` interface in `frontend/src/lib/api.ts`:**
  Change `versions: SOPVersion[]` to `versions?: SOPVersion[]` (make optional, since the list endpoint doesn't return it).

  **Fix B — Fix step-count on the card in `sop-engine/page.tsx`:**
  Change `(sop.versions?.[0]?.steps.length ?? 0)` to show `—` instead of a misleading `0`:
  ```tsx
  <span className="inline-flex items-center gap-1">
    <FileText size={12} />
    {sop.versions ? `${sop.versions[0]?.steps.length ?? 0} steps` : '—'}
  </span>
  ```

  **Fix C — Fix the steps empty state message in `sop-engine/page.tsx`:**

  Current code (line ~160):
  ```tsx
  ) : steps === undefined || steps.length === 0 ? (
    <p>Step details unavailable{(steps !== undefined) ? " for this SOP." : " right now."}</p>
  ```

  Replace with:
  ```tsx
  ) : steps === undefined ? (
    <p className="text-xs text-gray-400">Could not load steps right now.</p>
  ) : steps.length === 0 ? (
    <p className="text-xs text-gray-400">No procedure steps configured.</p>
  ```

  **Fix D — Access `current_version` correctly:**
  The `toggleView` function currently accesses `detail.versions?.[detail.versions.length - 1]` — the LAST version. This is a proxy for "most recent" but the backend exposes `current_version_id`. For correctness, change to find the version matching `current_version_id` first, falling back to last:
  ```typescript
  const v: SOPVersion | undefined = detail.current_version_id
    ? detail.versions?.find(v => v.id === detail.current_version_id) ?? detail.versions?.[detail.versions.length - 1]
    : detail.versions?.[detail.versions.length - 1];
  ```

  **Files:**
  - `frontend/src/lib/api.ts` (make `versions` optional on `SOP`)
  - `frontend/src/app/(dashboard)/sop-engine/page.tsx` (fixes B, C, D)

  **Verify:** `cd frontend && npx tsc --noEmit`. In browser, expanding a SOP card: if the SOP has a current version with steps, they render; if no steps, shows "No procedure steps configured." instead of "Step details unavailable for this SOP."

---

- [ ] **7. Command center data consistency verification and guard**

  **Current state after investigation:** Backend `CCMetrics` and frontend `CCMetrics` interface are **in sync** — all 11 fields match exactly. `buildAiOperations()` already uses `?.` optional chaining on the metrics object. `CommandCenterOverview` top-level fields match. No critical mismatch found.

  **What to do:**

  **Fix A — Add `| null` types to numeric `CCMetrics` fields in `frontend/src/lib/api.ts`:**
  Backend defaults all numeric fields to `0` via Pydantic `= 0`. The frontend interface types them as `number`. This is fine — no change needed.

  **Fix B — Defensive guard in `dashboardDataAdapter.ts`** — in `buildAiOperations()`, the adapter already uses `metrics?.working ?? null` etc. Add a guard for the `activity` feed too:
  Check whether `overview.activity` (which is `CCActivityEvent[]`) is accessed anywhere without a null-guard. In `buildActivity()` and `buildAiOperations()`, ensure all array accesses use `?? []`.

  **Fix C — Verify the `ai-copilot` page field access.** In `frontend/src/app/(dashboard)/ai-copilot/page.tsx`, the page destructures `o.metrics` as `const m: CCMetrics = o.metrics` — this will throw if `o` is defined but `o.metrics` is undefined. Add defensive: `const m = o.metrics ?? {} as CCMetrics`.

  **Files:**
  - `frontend/src/app/(dashboard)/ai-copilot/page.tsx`
  - `frontend/src/lib/dashboard/dashboardDataAdapter.ts`

  **Verify:** `cd frontend && npx tsc --noEmit` — no new type errors. In browser, `/ai-copilot` page loads without console errors when the backend returns an empty `agents`/`tasks` array.

---

- [ ] **8. Add `classifySensorState()` and update monitoring page sensor table**

  **Current state:** `monitoring/page.tsx` uses inline ternary color logic:
  ```tsx
  color: sensor.status === "up" ? "var(--green)"
       : sensor.status === "warning" ? "var(--amber)"
       : "var(--red)"
  ```
  All states outside `up`/`warning` are red — `unknown`, `collection_failed`, `not_configured`, and `stale` are all indistinguishable from `down`.

  **Create `frontend/src/lib/sensorState.ts`:**
  ```typescript
  export type SensorState =
    | "HEALTHY"
    | "WARNING"
    | "CRITICAL"
    | "DOWN"
    | "STALE"
    | "UNKNOWN"
    | "COLLECTION_FAILED"
    | "NOT_CONFIGURED";

  export interface SensorStateInfo {
    state: SensorState;
    color: string;
    label: string;
    dot: string; // CSS variable
  }

  const STATE_MAP: Record<SensorState, SensorStateInfo> = {
    HEALTHY:          { state: "HEALTHY",          color: "var(--green)",  dot: "var(--green)",  label: "Healthy" },
    WARNING:          { state: "WARNING",          color: "var(--amber)",  dot: "var(--amber)",  label: "Warning" },
    CRITICAL:         { state: "CRITICAL",         color: "var(--red)",    dot: "var(--red)",    label: "Critical" },
    DOWN:             { state: "DOWN",             color: "var(--red)",    dot: "var(--red)",    label: "Down" },
    STALE:            { state: "STALE",            color: "var(--amber)",  dot: "var(--amber)",  label: "Stale" },
    UNKNOWN:          { state: "UNKNOWN",          color: "var(--muted)",  dot: "var(--muted)",  label: "Unknown" },
    COLLECTION_FAILED:{ state: "COLLECTION_FAILED",color: "#ff6b35",       dot: "#ff6b35",       label: "Collection failed" },
    NOT_CONFIGURED:   { state: "NOT_CONFIGURED",   color: "var(--muted)",  dot: "var(--muted)",  label: "Not configured" },
  };

  /**
   * Maps a raw backend sensor status string to a canonical SensorStateInfo.
   * Backend emits: "up", "down", "warning", "unknown", and potentially
   * "collection_failed", "not_configured".
   * Freshness-based stale detection: pass lastValueAt to detect STALE.
   */
  export function classifySensorState(
    status: string | null | undefined,
    lastValueAt?: string | null
  ): SensorStateInfo {
    // Stale: status is "up" but data is older than 2 hours
    if (status === "up" && lastValueAt) {
      const ageMs = Date.now() - new Date(lastValueAt).getTime();
      if (ageMs > 2 * 60 * 60 * 1000) return STATE_MAP.STALE;
    }

    switch ((status ?? "").toLowerCase()) {
      case "up":                return STATE_MAP.HEALTHY;
      case "warning":           return STATE_MAP.WARNING;
      case "critical":          return STATE_MAP.CRITICAL;
      case "down":              return STATE_MAP.DOWN;
      case "unknown":           return STATE_MAP.UNKNOWN;
      case "collection_failed": return STATE_MAP.COLLECTION_FAILED;
      case "not_configured":    return STATE_MAP.NOT_CONFIGURED;
      default:                  return STATE_MAP.UNKNOWN;
    }
  }
  ```

  **Update `frontend/src/app/(dashboard)/monitoring/page.tsx`:**
  - Import `classifySensorState` from `@/lib/sensorState`.
  - Replace the inline ternary color logic in the `<td>` status cell with:
    ```tsx
    const si = classifySensorState(sensor.status, sensor.last_value_at ?? null);
    // In the JSX:
    <td style={{ padding: "10px 12px", color: si.color }}>
      <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
        <i style={{ width: 6, height: 6, borderRadius: "50%", background: si.dot, display: "inline-block" }} />
        {si.label}
      </span>
    </td>
    ```
  - Also update the top-level stat cards: `upCount` and `downCount` now use:
    ```typescript
    const upCount = sensors.filter((s) => classifySensorState(s.status).state === "HEALTHY").length;
    const alertingCount = sensors.filter((s) => {
      const st = classifySensorState(s.status, (s as any).last_value_at).state;
      return ["WARNING","CRITICAL","DOWN","STALE","COLLECTION_FAILED"].includes(st);
    }).length;
    ```

  **Gotcha:** Check whether `SensorOut` has `last_value_at` — if not, the stale detection falls back to no staleness check. Do not add a fake field; use only what the backend returns.

  **Files:**
  - `frontend/src/lib/sensorState.ts` (new file)
  - `frontend/src/app/(dashboard)/monitoring/page.tsx`

  **Verify:** `cd frontend && npx tsc --noEmit`. In browser, monitoring page shows distinct visual states per sensor status. `COLLECTION_FAILED` renders in orange-red (#ff6b35), `UNKNOWN`/`NOT_CONFIGURED` in muted, rather than all-red.

---

- [ ] **9. Create `FreshnessIndicator` component**

  **Create `frontend/src/components/shared/FreshnessIndicator.tsx`:**

  ```tsx
  "use client";

  import React from "react";

  export type FreshnessBucket =
    | "LIVE"            // < 5 min
    | "RECENT"          // 5–30 min
    | "AGING"           // 30 min – 2 hr
    | "STALE"           // 2 hr – 24 hr
    | "EXPIRED"         // > 24 hr
    | "NEVER_COLLECTED" // last_collected_at is null
    | "UNKNOWN";        // cannot determine

  interface BucketConfig {
    label: string;
    color: string;
    bg: string;
  }

  const BUCKET_CONFIG: Record<FreshnessBucket, BucketConfig> = {
    LIVE:            { label: "LIVE",            color: "var(--green)",  bg: "rgba(0,217,100,.12)" },
    RECENT:          { label: "RECENT",          color: "var(--cyan)",   bg: "rgba(0,217,255,.10)" },
    AGING:           { label: "AGING",           color: "var(--amber)",  bg: "rgba(255,180,0,.10)" },
    STALE:           { label: "STALE",           color: "var(--amber)",  bg: "rgba(255,140,0,.12)" },
    EXPIRED:         { label: "EXPIRED",         color: "var(--red)",    bg: "rgba(255,50,50,.10)" },
    NEVER_COLLECTED: { label: "NEVER COLLECTED", color: "var(--muted)",  bg: "transparent" },
    UNKNOWN:         { label: "UNKNOWN",         color: "var(--muted)",  bg: "transparent" },
  };

  /**
   * Classify a UTC ISO timestamp (or null) into a FreshnessBucket.
   * Pass null when the data has never been collected.
   */
  export function classifyFreshness(lastCollectedAt: string | null | undefined): FreshnessBucket {
    if (!lastCollectedAt) return "NEVER_COLLECTED";
    const ageMs = Date.now() - new Date(lastCollectedAt).getTime();
    if (isNaN(ageMs)) return "UNKNOWN";
    const min = ageMs / 60_000;
    if (min < 5)    return "LIVE";
    if (min < 30)   return "RECENT";
    if (min < 120)  return "AGING";
    if (min < 1440) return "STALE";
    return "EXPIRED";
  }

  interface FreshnessIndicatorProps {
    /** UTC ISO timestamp of last collection, or null if never collected. */
    lastCollectedAt: string | null | undefined;
    /** Override the computed bucket (e.g. from a backend-computed freshness state). */
    bucket?: FreshnessBucket;
    /** Show the human-relative age alongside the label, e.g. "LIVE · 2m ago" */
    showAge?: boolean;
    /** Compact mode: dot only, no text label. */
    compact?: boolean;
  }

  function relativeAge(lastCollectedAt: string): string {
    const ageMs = Date.now() - new Date(lastCollectedAt).getTime();
    const s = Math.floor(ageMs / 1000);
    if (s < 60)      return `${s}s ago`;
    const m = Math.floor(s / 60);
    if (m < 60)      return `${m}m ago`;
    const h = Math.floor(m / 60);
    if (h < 24)      return `${h}h ago`;
    return `${Math.floor(h / 24)}d ago`;
  }

  export default function FreshnessIndicator({
    lastCollectedAt,
    bucket: bucketOverride,
    showAge = false,
    compact = false,
  }: FreshnessIndicatorProps) {
    const bucket = bucketOverride ?? classifyFreshness(lastCollectedAt);
    const cfg = BUCKET_CONFIG[bucket];

    if (compact) {
      return (
        <i
          title={cfg.label}
          style={{
            display: "inline-block",
            width: 7,
            height: 7,
            borderRadius: "50%",
            background: cfg.color,
            flexShrink: 0,
          }}
        />
      );
    }

    const age = showAge && lastCollectedAt ? ` · ${relativeAge(lastCollectedAt)}` : "";

    return (
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 4,
          padding: "2px 6px",
          borderRadius: 4,
          background: cfg.bg,
          fontSize: 9,
          fontWeight: 700,
          letterSpacing: "0.4px",
          color: cfg.color,
          whiteSpace: "nowrap",
        }}
        title={lastCollectedAt ? new Date(lastCollectedAt).toLocaleString() : "Never collected"}
      >
        {cfg.label}{age}
      </span>
    );
  }
  ```

  **File:** `frontend/src/components/shared/FreshnessIndicator.tsx`

  **Verify:** `cd frontend && npx tsc --noEmit` — new component type-checks cleanly.

---

- [ ] **10. Wire `FreshnessIndicator` into monitoring page and topology node drawer**

  **10a. Monitoring page** (`frontend/src/app/(dashboard)/monitoring/page.tsx`):
  - Import `FreshnessIndicator` from `@/components/shared/FreshnessIndicator`.
  - Add a "Freshness" column header to the PRTG sensor table.
  - In each sensor row, render:
    ```tsx
    <td style={{ padding: "10px 12px" }}>
      <FreshnessIndicator
        lastCollectedAt={(sensor as any).last_value_at ?? null}
        showAge
      />
    </td>
    ```
  - If `SensorOut` in `api.ts` does not have `last_value_at`, do NOT cast — instead check `api.ts` and add the field if it exists on the backend model. If it does not exist, render `<FreshnessIndicator lastCollectedAt={null} />` and note this as a future gap.

  **10b. Topology node drawer** (`frontend/src/app/(dashboard)/infrastructure-topology/page.tsx`):
  - Import `FreshnessIndicator` from `@/components/shared/FreshnessIndicator`.
  - In `NodeDrawer`, after the `<Row label="Last seen" ...>` row, add:
    ```tsx
    <div style={{ marginTop: 8 }}>
      <span style={{ fontSize: 10, color: "var(--muted)", minWidth: 110, display: "inline-block" }}>
        Data freshness
      </span>
      <FreshnessIndicator
        lastCollectedAt={(meta.last_seen_at as string) ?? null}
        showAge
      />
    </div>
    ```

  **Gotcha:** `meta.last_seen_at` is already used in the existing `Row label="Last seen"` — reuse the same value. Do not invent a new field.

  **Files:**
  - `frontend/src/app/(dashboard)/monitoring/page.tsx`
  - `frontend/src/app/(dashboard)/infrastructure-topology/page.tsx`

  **Verify:** `cd frontend && npx tsc --noEmit`. In browser:
  - Monitoring page PRTG sensor table shows a "Freshness" column with color-coded badges.
  - Topology node drawer shows a "Data freshness" indicator after "Last seen".

---

## Cross-cutting notes

### Do not touch
- `REQUEST_TIMEOUT_MS = 10_000` in `frontend/src/lib/api.ts` — leave unchanged.
- `_FETCH_LIMIT = 100_000` in `TopologyGraphService` — leave unchanged.
- `TopologyLiveService` — none of these tasks touch it.
- The existing `SiteRepository.list()`, `DeviceRepository.list()` signatures — Tasks 2 and 3 only ADD parameters, all with defaults.

### Build / test commands
```
# Backend — from backend/
.venv\Scripts\python.exe -m pytest tests/ -x -q          # full test suite
.venv\Scripts\python.exe -m pytest tests/test_topology_graph.py -x -q   # topology tests

# Frontend — from frontend/
npx tsc --noEmit       # type-check only (fast)
npm run build          # full production build
npm run lint           # ESLint
```

### Dependency order
```
Task 1 (schema) → Task 2 (route) → Task 3 (pagination in service+schema+route)
Task 3 (backend) → Task 4 (frontend getTopologySummary + page changes)
Task 5 (SOP fix) — independent
Task 6 (CC verify) — independent
Task 7 (sensor state) → Task 10a (FreshnessIndicator wired in monitoring)
Task 8 (FreshnessIndicator component) → Task 10 (wire into pages)
```
Tasks 5, 6, 7, 8, 9 are independent of Tasks 1–4.
