# Sprint ECC-1 — Executive Command Center (one-screen `/dashboard`)

Status: **IMPLEMENTED 2026-10-29** (directly, not via Cline). Deviations from the
design below, decided during the build:
- Severity vocabulary is the backend's `critical | warning | info` (no High/Medium/Low).
- `ResponseKpis.previous` carries only window-derived metrics (incident volume,
  resolved count, MTTR, MTTA). There is no device/collector state history, so
  device and collector tiles show no delta rather than an invented one.
- MTTA = mean `acknowledged_at - opened_at` over incidents **opened** in the window
  (anchored on the indexed `opened_at`).
- `GET /api/dashboard/site-health` uses ≤ 3 statements and only counts incidents
  for the tenant's listed sites; `unassigned_incidents` was dropped.
- Migration `e8c1d4f7a2b9` adds the covering index
  `ix_incidents_tenant_status_site_active` — the aging aggregate fell from 45 s to
  ~3 s warm on the 2.7M-incident dev tenant (analytics read 46 s → 3.6 s).
- The brief now uses the 30 s heavy-KPI client timeout (it ran 6–39 s on that tenant).
- Drill-downs go only to filters the target pages actually read (`/incidents?severity=`,
  `/incidents/{id}`, `/explorer`, `/monitoring`, `/reports`). Selecting a site hexagon
  sets the dashboard's own site filter.
- Code: `frontend/src/components/command-center/`, `frontend/src/lib/commandCenter/format.ts`,
  `frontend/src/lib/severity.ts`, `backend/app/{schemas,repositories,services}/site_health*`. Repository facts below were read from the
working tree on 2026-10-29; much of it is **uncommitted** work.

Inspiration: Dynatrace (information hierarchy, single-value tiles, health-sorted
tables, drill-down). Not copied: no Dynatrace assets, copy, layout grid, or the
"Try our Playground examples" footer that appears in the reference mockups.

---

## A. Current state (verified against the repo)

### What `/dashboard` renders today
`frontend/src/app/(dashboard)/dashboard/page.tsx` stacks, top to bottom:

1. `CommandCenterBrief` — attention + health + KPIs (`GET /api/dashboard/command-center-brief`)
2. `IncidentAnalyticsPanel` — trend, severity, aging, heatmap, duration, top sites (`GET /api/dashboard/incident-analytics?days=`)
3. `UnifiedCommandCenter`, which itself stacks: `DashboardMasthead`, `DeploymentReadinessPanel`,
   `AttentionRequired`, `LiveInfrastructureIntelligence`, an in-page section nav,
   `OperationalHealth` + `AssetsSummary`, `AlertSummaryPanel` + `SocKpiCards`,
   `NetworkIntelligenceWidget` + `InfrastructureOverview`, `SecurityOperationsPanel`,
   `AiOperations` + `CommandCenterTopology`
4. `CloudOperationsPanel` (`GET /api/dashboard/cloud-operations`)

That is **~14 panels and 3 separate "counter" blocks** (Brief KPIs, AttentionRequired
counters, OperationalHealth KPI grid) answering overlapping questions. It is long,
not scannable, and not the one-screen executive view in the mockups.

### Backend that already exists for the mockup
| Mockup widget | Backed by | Status |
|---|---|---|
| Incident Trends (opened/resolved) | `IncidentAnalytics.trend` | Exists (uncommitted) |
| Severity Distribution donut | `IncidentAnalytics.severity`, `total_opened` | Exists |
| Incident Aging (stacked) | `IncidentAnalytics.aging[].by_severity` | Exists |
| Incident Heatmap (weekday×hour) | `IncidentAnalytics.heatmap` | Exists |
| Duration distribution + Median/P90/P95 | `IncidentAnalytics.duration` | Exists |
| Top Affected Sites | `IncidentAnalytics.top_sites`, `other_sites_count` | Exists |
| Infrastructure Health donut | `CommandCenterBrief.infrastructure_health` (DeviceHealthBuckets) | Exists |
| Collector Health & freshness | `CommandCenterBrief.collection_health`, `CollectorHealthBuckets` | Exists |
| Active / Critical incidents, MTTR | `CommandCenterBrief.kpis` (ResponseKpis) | Exists |
| Sparklines | `CommandCenterBrief.trends` (hourly: alerts, incidents, collection_failures, devices_down, stale_telemetry) | Exists |
| Recent incidents table | `GET /api/incidents/prioritized` | Exists |
| **MTTA** | `incidents.acknowledged_at` column exists; **not exposed** | Gap |
| **"vs last 24h" deltas** | No previous-window values in any payload | Gap |
| **Infrastructure Overview (Sites) honeycomb** | `GET /api/assets/sites` has `code`, `criticality`, no health | Gap |
| **Global filters** (time / site / severity) | `TopBar.tsx` is a 31-line status bar, no filters; `incident-analytics` takes only `days` | Gap |

Other relevant facts:
- `GET /api/explorer/hosts` + `/explorer` page (1,433 lines) already implement the
  Dynatrace-Hosts-style table + facets (screenshot 3). Out of scope here; it is the
  drill-down target.
- `/dashboard-v2` already redirects to `/dashboard` — keep it that way; no second dashboard.
- `/incidents` reads only `?severity=` from the URL.
- Brief window is the constant `WINDOW_HOURS` in `operational_intelligence_service.py`.
- Working tree has **~266 uncommitted entries** including all of the analytics/explorer work.

## B. Recommended experience

### B1. One rule for the page
`/dashboard` answers, in this order and **within one 1440×900 viewport for rows 1–3**:
1. *Is anything on fire?* (KPI strip)
2. *Where and how bad?* (trends, severity, estate health, sites)
3. *Are we responding well?* (aging, recent incidents, collectors, durations)

Everything else moves to the page that already owns it. Nothing is deleted.

| Removed from `/dashboard` | Lives on (existing route) |
|---|---|
| DeploymentReadinessPanel | `/settings/general` (admin only) — or keep on `/admin/customers`; **Needs Verification** of best home |
| LiveInfrastructureIntelligence, InfrastructureOverview, NetworkIntelligenceWidget, AssetsSummary | `/infrastructure` |
| SocKpiCards, SecurityOperationsPanel | `/soc-analysis` |
| AiOperations, CommandCenterTopology | `/portal` (Command Center) and `/ai-copilot` |
| CloudOperationsPanel | `/infrastructure` (cloud tab) |
| AttentionRequired, OperationalHealth, AlertSummaryPanel, CommandCenterBrief (as a block) | Superseded by the grid; their data is the brief payload they already read |

Components are **not deleted** in ECC-1 — they are unmounted from `/dashboard` only.
Removal of genuinely dead components is a later cleanup with its own review.

### B2. Layout (desktop ≥1440, 12-column grid, 16px gap)

```
┌ Page header ────────────────────────────────────────────────────────────────┐
│ Executive Command Center      [Last 24h ▾][All sites ▾][All severities ▾][⟳]│
│ subtitle                                        Updated 14:32:18 · ● Live    │
├ Row 1: KPI strip — 8 tiles, each span 12/8 (wrap 4×2 below 1440) ───────────┤
│ Monitored │ Healthy │ Critical/Unreach │ Active inc │ Critical inc │         │
│ Collector health │ MTTA │ MTTR                                              │
├ Row 2 ──────────────────────────────────────────────────────────────────────┤
│ Incident Trends (span 5) │ Severity Distribution (span 3) │ Infra Health (4) │
├ Row 3 ──────────────────────────────────────────────────────────────────────┤
│ Top Affected Sites (3) │ Sites honeycomb (3) │ Collector Health (3) │ Aging(3)│
├ Row 4 ──────────────────────────────────────────────────────────────────────┤
│ Recent Incidents (5)   │ Incident Heatmap (4) │ Duration Distribution (3)    │
└─────────────────────────────────────────────────────────────────────────────┘
```

Breakpoints: ≥1440 as above · 1024–1439 → rows reflow to 2 cards per row (KPI 4×2) ·
768–1023 → 2 columns, KPI 2×4 · <768 → 1 column, KPI 2×4, tables become card lists.
No horizontal page scroll at any width. Side gutter 16px on mobile.

### B3. KPI tile contract
Each tile: label, value, unit, optional delta, optional sparkline, info tooltip
(definition + source + window), whole tile is a link to its drill-down.

| Tile | Value source | Delta | Sparkline | Drill-down |
|---|---|---|---|---|
| Monitored Devices | `infrastructure_health.devices.total` | prev window | — | `/explorer` |
| Healthy Devices | `devices.healthy` (show `% of total` under value) | prev window | — | `/explorer?health=healthy` |
| Critical / Unreachable | `devices.critical + devices.unreachable` (exact field names: Needs Verification) | prev window | `trends.devices_down` | `/explorer?health=critical` |
| Active Incidents | `kpis.open_incidents` | prev window | `trends.incidents` | `/incidents` |
| Critical Incidents | `kpis.critical_open_incidents` | prev window | — | `/incidents?severity=critical` |
| Collector Health | `% collectors online` from `CollectorHealthBuckets` | prev window | `trends.collection_failures` (inverted tone) | `/monitoring` |
| MTTA | **new** `kpis.mtta_minutes` | prev window | — | `/reports/alert-analysis` |
| MTTR | `kpis.mttr_minutes` | prev window | — | `/reports` |

**Delta semantics** (the mockup's "↑ 33% vs last 24h"): colour encodes *good/bad*, not
direction. Incidents ↑ = red; Healthy ↑ = green; MTTR ↓ = green. If the previous value
is `null` or `0`, show no percentage (show absolute change, e.g. "+3", or nothing).
Never invent a baseline.

### B4. Value states (applies to every tile and card)
| State | Render |
|---|---|
| loading (first load) | skeleton matching final shape; no spinners over charts |
| refreshing | keep data, subtle "Updating…" in header; "Updated hh:mm:ss" timestamp |
| value = 0 | `0` (real zero, normal tone) |
| value = null / unavailable | `—` + muted caption "Not measured" + tooltip with backend `reason` |
| empty series | card-level empty state: one line explaining why (e.g. "No incidents resolved in the last 7 days") |
| error | card-level: "Couldn't load incident analytics" + Retry; other cards unaffected |
| stale (data older than 2× poll interval) | amber "Stale · last updated 6 min ago" chip in header; "Live" dot turns amber |

The mockup's "API data" placeholder text is **not** an acceptable production state.

### B5. Cards
- **Incident Trends** — two-series line/area (opened, resolved), daily buckets from
  `trend`; crosshair tooltip with both values; window follows the global time filter
  (24h → hourly from `brief.trends.incidents` opened-only and resolved hidden with
  note; 7/14/30d → `incident-analytics.trend`).
- **Severity Distribution** — donut + legend table (count, %), centre = `total_opened`.
  Legend rows are links to `/incidents?severity=`. Fixed severity order and colours
  (reuse `SEVERITY_COLOR`/`SEVERITY_ORDER` in `IncidentAnalyticsPanel.tsx`; move to
  a shared `lib/severity.ts`).
- **Infrastructure Health** — donut of device health buckets; centre = total devices.
- **Top Affected Sites** — horizontal bars, top 7 + "Other (n)". Bars coloured by rank
  severity is decorative — use a single accent colour; count label right-aligned.
  Row click → `/explorer?site_id=`.
- **Sites honeycomb (new endpoint)** — one hex per site labelled with `site.code` and
  open incident count; fill = worst device health at that site; state text in
  `aria-label` and tooltip (colour is never the only signal). >11 sites → 11 worst +
  "+N" hex linking to `/infrastructure`. Click → `/explorer?site_id=`.
- **Collector Health & Data Freshness** — gauge (% online, `online/total`), status
  list (online/degraded/offline counts), and a bar sparkline of
  `trends.collection_failures`. Print `collection_health.distinction` verbatim as a
  caption (collection failure ≠ infrastructure failure — existing product rule).
- **Incident Aging** — stacked bars per bucket (`aging[].by_severity`), totals under
  each bar.
- **Recent Incidents** — from `/api/incidents/prioritized?limit=8`: ID, severity pill,
  title, site, device, status, age, last update; row → `/incidents/{id}`. "View all →".
- **Incident Heatmap** — 7×24 grid from `heatmap`, 5-step sequential scale with legend;
  cell tooltip "Wed 14:00 — 6 incidents". Days = `heatmap_days`.
- **Duration Distribution** — histogram of `duration.buckets` + Median / P90 / P95
  footer (null → "No data", existing `formatDuration`).

### B6. Global filters
A `CommandCenterFilters` bar in the page header (not in `TopBar.tsx`, so the shell
is untouched). State lives in the URL: `?range=24h|7d|14d|30d&site=<uuid>&severity=<s>`
so views are shareable. Defaults: `7d`, all, all. Filters apply to
`incident-analytics` (range, site, severity) and the recent-incidents table
(site, severity). The brief is a fixed 24h snapshot — its tiles show a "24h" badge
so it is obvious they ignore the range filter. Site list from `GET /api/assets/sites`.

### B7. Theme
The repo is dark-first. ECC-1 builds all new components on CSS custom properties
(`--surface`, `--surface-raised`, `--border`, `--text`, `--text-muted`, severity and
health tokens) declared in `globals.css` so the light theme (mockup 6) is a token
swap later. **Light-theme toggle itself is out of scope** for ECC-1.

### B8. Accessibility & performance
- Every chart has `role="img"` + an `aria-label` summary and a visually hidden data
  table; heatmap/honeycomb cells keyboard-focusable with tooltip on focus.
- Contrast ≥ 4.5:1 for text on all surfaces; severity pills have text, not colour only.
- Page issues **at most 4 requests** on load: brief, incident-analytics, site-health,
  prioritized incidents (+ sites list for the filter, cached). Poll 60s, pause when tab
  hidden (React Query `refetchIntervalInBackground: false`).
- p95 API latency targets: each read < 400 ms at 10k incidents / 2k devices per tenant.

## C. Backend contracts (additive, backward-compatible)

### C1. `ResponseKpis` — add fields (all nullable, defaults keep old clients working)
```python
mtta_minutes: float | None = None          # avg(acknowledged_at - created_at) over incidents acknowledged in window
previous: "ResponseKpisPrevious | None" = None

class ResponseKpisPrevious(BaseModel):     # same metrics, window shifted back by window_hours
    open_incidents: int | None
    critical_open_incidents: int | None
    mttr_minutes: float | None
    mtta_minutes: float | None
    devices_total: int | None
    devices_healthy: int | None
    devices_critical_unreachable: int | None
    collectors_online_pct: float | None
```
"Previous" for point-in-time counts (open incidents, device health) requires history.
If there is no snapshot/history table, those fields are **`None` with no delta shown**
— do not reconstruct past device state from current rows. Incident-derived counts can
be computed from `created_at`/`resolved_at` at `now - window`. **Needs Verification:**
whether a device-state history table exists; Cline must check and report.

### C2. `GET /api/dashboard/incident-analytics` — add optional params
`site_id: uuid | None`, `severity: str | None` (validated against the incident severity
enum). Absent params = today's behaviour exactly. Applied as SQL `WHERE` clauses in
`incident_analytics_repository.py`, not in Python.

### C3. New `GET /api/dashboard/site-health`
```python
class SiteHealthItem(BaseModel):
    site_id: uuid.UUID
    code: str
    name: str
    criticality: str | None
    devices_total: int
    devices_by_health: dict[str, int]   # healthy/warning/critical/unreachable/unknown — same buckets as DeviceHealthBuckets
    open_incidents: int
    worst_health: str                   # derived: worst non-empty bucket, "unknown" if no devices

class SiteHealth(BaseModel):
    generated_at: datetime
    sites: list[SiteHealthItem]         # sorted worst_health desc, then open_incidents desc
    unassigned_devices: int             # devices with no site
```
Tenant from `get_tenant_id` (principal-derived), read so not behind `_active_sub_guard`,
≤ 2 SQL aggregates (GROUP BY site), no ORM row loading. Lives on the dashboard router
(same pattern as `explorer/hosts`), so no `main.py` change. Reuse the device-health
derivation the brief already uses — **do not write a second health rule.**

## D. Risks and dependencies
1. **Uncommitted baseline (high).** ~266 uncommitted entries include the very endpoints
   this sprint builds on. Commit (or branch) the current state *before* Cline starts,
   so ECC-1 is a reviewable diff and reversible.
2. **Previous-window history (medium).** Device-health deltas may be impossible without
   a snapshot table; acceptable outcome is "no delta", not a fake one. A
   `device_health_snapshots` hourly table would be a separate, approved item.
3. **P1 Auth/RBAC (dependency).** All reads use `get_tenant_id`; if P1 lands mid-sprint
   the dependency swaps but contracts do not change.
4. **Brief latency.** There is a `P1.1_COMMAND_CENTER_BRIEF_LATENCY_FIX.md`; adding MTTA
   and `previous` adds queries to that path — must stay inside its query budget.
5. **Lost discoverability.** Unmounting ~10 panels: confirm each target page actually
   renders them (Cline verifies; if a panel has no other home, keep it reachable via a
   link rather than silently losing it).

## E. Acceptance criteria (measurable)
1. At 1440×900, KPI strip + rows 2 and 3 are fully visible without scrolling.
2. No horizontal scroll at 375, 768, 1024, 1440, 1920 widths.
3. `/dashboard` fires ≤ 5 API requests on first load (network panel) and no request to
   SOC, cloud-operations, deployment-readiness, topology or AI endpoints.
4. Every null metric renders `—` + "Not measured"; zero renders `0`; no "API data"
   string or hardcoded number exists in any new component (grep test).
5. Changing site/severity/range updates the URL and refetches incident-analytics with
   matching params; reload preserves filters.
6. Each KPI tile and site/legend/table row navigates to the specified drill-down.
7. One failing endpoint (simulate 500) shows an error card with Retry while all other
   cards render.
8. Existing API responses are byte-compatible for old clients (new fields only added).
9. Cross-tenant test: tenant A's site-health never includes tenant B's sites/devices.
10. Lighthouse accessibility ≥ 90 on `/dashboard`; axe: zero serious/critical issues.
11. All backend tests pass (`pytest backend/tests`), and new tests below pass.

## F. Post-implementation evidence to send back for review
Screenshots at 1440 and 375 (dark), network panel of first load, `pytest` output,
the diff stat, sample JSON from the three changed/new endpoints, and the list of
unmounted panels with their verified home route.
