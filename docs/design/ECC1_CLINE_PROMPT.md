# Cline prompt — Sprint ECC-1: Executive Command Center

> Paste everything below the line into Cline. Full design rationale:
> `docs/design/EXECUTIVE_COMMAND_CENTER_ECC1.md` (read it first).

---

You are implementing Sprint ECC-1 in `I:\AI-LAB\hsdeep-core-ai`. Read
`docs/design/EXECUTIVE_COMMAND_CENTER_ECC1.md` in full before writing code. It is the
spec; where this prompt and the spec differ, the spec wins and you tell me.

## 0. Pre-flight (do this first, report results, then continue)
1. Run `git status`. If there are uncommitted changes, STOP and ask me to commit or
   branch first. Do not commit for me. Work on branch `feat/ecc1-command-center`.
2. Run `pytest backend/tests -q` and the frontend type-check/build
   (`npm run build` or `npx tsc --noEmit` in `frontend/`). Record the baseline
   pass/fail counts. Pre-existing failures are not yours to fix — list them.
3. Verify and report (file:line) each item marked **Needs Verification** in the spec:
   - exact field names in `DeviceHealthBuckets` and `CollectorHealthBuckets`
     (`backend/app/schemas/dashboard.py`)
   - whether any device-state history / snapshot table exists (for previous-window deltas)
   - where the brief computes device health (the function you must reuse in site-health)
   - whether `/infrastructure`, `/soc-analysis`, `/portal`, `/ai-copilot` already render
     each panel being unmounted from `/dashboard` (list panel → route → yes/no)
   - the query budget described in `P1.1_COMMAND_CENTER_BRIEF_LATENCY_FIX.md`

## 1. Hard constraints
- Extend, don't rebuild. Reuse `useCommandCenterBrief`, `useIncidentAnalytics`,
  `components/viz/Chart`, `IncidentAnalyticsPanel` chart code, existing API client in
  `frontend/src/lib/api.ts`.
- Additive API changes only. Every new response field is optional/nullable with a
  default; every new query param is optional; absent params = identical behaviour.
- Tenant always from `Depends(get_tenant_id)`. Never accept tenant from the client.
- No mock, demo, placeholder, or hardcoded metric values anywhere. `null` → "—" with
  "Not measured"; never coerce null to 0. The string "API data" must not appear.
- Do not modify `TopBar.tsx`, `Sidebar.tsx`, `layout.tsx`, auth, polling config of
  other pages, or any unrelated module.
- Do not delete existing components. Unmount them from `/dashboard` only.
- Keep `/dashboard-v2` redirecting to `/dashboard`.
- Aggregations in SQL (GROUP BY / FILTER), not by loading rows into Python.
- Match surrounding code style and comment density.

## 2. Backend tasks
**B1 — MTTA + previous window** (`schemas/dashboard.py`, `operational_intelligence_service.py`)
- Add `mtta_minutes: float | None = None` to `ResponseKpis`: average of
  `incidents.acknowledged_at - incidents.created_at` for incidents acknowledged inside
  the brief window; `None` when there are none.
- Add `ResponseKpisPrevious` and `ResponseKpis.previous` per spec §C1. Incident-derived
  values: compute for `[now-2w, now-w)`. Point-in-time device/collector values: if no
  history table exists, return `None` — do not reconstruct.
- Stay within the brief's existing query budget; if you must add queries, fold them
  into existing aggregates and report the new count.

**B2 — filters on incident analytics** (`routes/dashboard.py`,
`incident_analytics_service.py`, `incident_analytics_repository.py`)
- Optional `site_id: uuid.UUID | None` and `severity` (validated against the existing
  incident severity enum; invalid → 422). Applied as SQL WHERE in every aggregate.

**B3 — `GET /api/dashboard/site-health`** (new `schemas/site_health.py`,
`repositories/site_health_repository.py`, `services/site_health_service.py`, route on
the dashboard router)
- Contract exactly as spec §C3. ≤ 2 SQL statements. Reuse the brief's device-health
  derivation; do not write a second health rule. Sorted worst first.

## 3. Frontend tasks
**F1 — page** `frontend/src/app/(dashboard)/dashboard/page.tsx`
- Replace the stacked layout with the grid in spec §B2. Remove mounts of
  `UnifiedCommandCenter` and `CloudOperationsPanel` from this page.
- New hooks: `useSiteHealth`, `useCommandCenterFilters` (URL query state: `range`,
  `site`, `severity`; defaults 7d/all/all). Extend `useIncidentAnalytics` to accept
  `{days, siteId, severity}` in its query key.

**F2 — components** under `frontend/src/components/command-center/`
- `CommandCenterHeader` (title, subtitle, filters, "Updated hh:mm:ss", Live/Stale chip)
- `CommandCenterFilters`
- `KpiTile` + `KpiStrip` (spec §B3 table; delta colour = good/bad; no % when previous
  is null/0)
- `CardShell` (title, info tooltip, action slot, and the five states in spec §B4)
- `IncidentTrendCard`, `SeverityDonutCard`, `InfraHealthDonutCard`, `TopSitesCard`,
  `SiteHoneycombCard`, `CollectorHealthCard`, `IncidentAgingCard`,
  `RecentIncidentsCard`, `IncidentHeatmapCard`, `DurationDistributionCard`
- Extract chart logic from `IncidentAnalyticsPanel.tsx` into these cards rather than
  duplicating it; move `SEVERITY_COLOR` / `SEVERITY_ORDER` / `formatDuration` to
  `frontend/src/lib/severity.ts` and import from both places. Leave
  `IncidentAnalyticsPanel` working (it may be reused elsewhere).
- Styles: CSS custom properties added to `globals.css` (`--cc-surface`,
  `--cc-surface-raised`, `--cc-border`, `--cc-text`, `--cc-text-muted`, severity and
  health tokens). No hex colours inside components. Dark theme only for now.
- Drill-down links exactly per spec §B3/§B5. Only link to filters the target page
  actually reads (verified: `/incidents?severity=`; check `/explorer` params).
- Accessibility per spec §B8.

## 4. Tests (required)
Backend (`backend/tests/`):
- `test_incident_analytics.py`: site_id filter, severity filter, invalid severity 422,
  no params = unchanged output (snapshot of existing fixture).
- `test_command_center_brief.py`: mtta computed; mtta None when nothing acknowledged;
  `previous` populated for incident metrics; device fields None without history;
  response still validates for an old-client schema (no required new fields).
- `test_site_health.py` (new): bucket counts, worst_health ordering, site with zero
  devices → "unknown", unassigned_devices, **cross-tenant isolation**, query count ≤ 2.
Frontend (`frontend/tests/`, follow existing `*.test.mjs` / `*-browser.cjs` patterns):
- delta formatter: good/bad tone per metric, null/0 previous → no percentage.
- value formatter: null → "—", 0 → "0".
- grep test: no `"API data"` and no numeric literals rendered as metrics in
  `components/command-center/`.
- browser test `command-center-ecc1-browser.cjs`: no horizontal scroll at
  375/768/1024/1440/1920; filters write URL and survive reload; one endpoint forced to
  500 shows that card's error + Retry while others render; ≤ 5 requests on first load.

## 5. Acceptance criteria
All 11 criteria in spec §E. Do not report done until each is checked; report each as
PASS / FAIL / NOT VERIFIED with evidence.

## 6. Report back (required format)
1. Pre-flight findings (section 0) with file:line citations.
2. Files changed / added (diff stat).
3. API changes with sample JSON for brief (kpis), incident-analytics with filters,
   site-health.
4. DB/migration changes (expected: none — say so).
5. Test output: backend and frontend, before vs after counts.
6. Screenshots: `/dashboard` at 1440×900 and 375 wide.
7. Acceptance criteria table.
8. Unmounted panels → verified home route.
9. Performance (query counts, measured latency), security impact, technical debt.
Do not claim anything you did not run.
