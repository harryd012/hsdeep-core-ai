# PHASE 2 — Command Center / Dashboard Hierarchy

**Status:** implemented and validated; committed in two steps (see §9.1)
**Milestone:** PHASE 2
**Predecessors:** PHASE 1 `5a7be7729`, PHASE 3 `aab1a06b5`/`9f8bb8b7e`, PHASE 3.1 `dccc5d81b`
**Branch:** `main`

Nothing below describes behaviour that has not been verified against the cited
source. Where a value could not be verified it says so (§13).

---

## 1. The dashboard problem this fixes

The `/dashboard` page carried the right *information* but not the right
*priority*. Concretely, before this milestone:

1. **AI competed with attention.** The `AI Operations / AI CTO` zone sat third on
   the page — above Infrastructure and Security Operations — so an AI suggestion
   panel occupied prime screen space ahead of measured estate state. Nothing in
   the ordering told an operator what to look at first.
2. **The two most actionable object lists were inert.** `WHY - OPEN INCIDENTS`
   and `WHY - DEGRADED SYSTEMS` rendered the incidents and devices behind the
   counters as **plain `<li>` text**. An operator could see that
   `192.168.159.100` was degraded but could not click it, and could not open an
   incident to investigate it. Both destinations already existed
   (`/incidents/{id}`, `/infrastructure/assets/{deviceId}`) — they were simply not
   linked.
3. **The evidence panel described the wrong semantics.** The collection-pipeline
   hints still read *"partial success, last 24h"* and *"no successful run, last
   24h"*. After PHASE 3.1 the authoritative `CollectorHealthService` counts
   `status IN ('success','partial')` as a **success** and defines `failing` as a
   **failure rate of 50% or more**. The old hints therefore mis-described two of
   the four numbers an operator reads.
4. **No level structure was visible.** The hierarchy existed only in code
   comments (`LEVEL 1`, `LEVEL 2b`, `ROW 3`), so a reader of the page could not
   tell which band they were looking at.
5. **Filter context was dropped on navigation.** `Critical Incidents` linked to
   bare `/incidents` even though that route already reads `?severity=` — the
   operator's intent was discarded and had to be re-applied by hand.

## 2. The new information hierarchy

Seven levels, highest-urgency first, with a caption rendered for each band:

| Level | Band | What the operator sees | Source |
|-------|------|------------------------|--------|
| L1–3 | Attention, impact and health | The normalized KPI strip (10 cards) | `commandKpis.ts` (PHASE 1) |
| L4 | Why | The incidents and devices **behind** those counters, each row carrying its measured evidence (member alerts, occurrences, recorded RCA, asset, status, age) | `AttentionRequiredData` |
| L5 | Evidence | Aggregates **the backend computes**, with the backend's own definition attached to every number | `/incidents/intelligence`, `/incidents/collector-health` |
| L6 | Operations | The queues where the work happens: incidents, alerts, changes, automation | existing routes |
| L7 | AI | AI Operations / AI CTO + live topology — moved to the **last** section | `AiOperations`, `CommandCenterTopology` |

The page order is now:

```
AttentionRequired (L1-7)
  -> Operational Health (NOC)          [health]
  -> Alert Summary + Security Overview [health]
  -> Infrastructure Operations         [detail]
  -> Security Operations               [detail]
  -> AI Operations / AI CTO            [L7 - AI, deliberately last]
```

`AI Operations` moved from position 3 to position 6. The section nav
(`#attention`, `#operational-health`, `#security-overview`, `#infrastructure`,
`#security-operations`, `#ai-operations`) was reordered to match, so the nav and
the document order cannot disagree.

## 3. KPI semantics

Unchanged from PHASE 1 and re-verified here:

* `value === null` → the card renders **`N/A`** with the reason the source gave.
* `value === 0` → a **measured zero**, and it stays `0`.
* `unknown` != `down`. `DEGRADED_DEVICE_STATUS` deliberately excludes `""` and
  `"unknown"`, so a device with no reported status is never counted as degraded;
  the strip reports those separately (`"N device(s) monitored · N with no reported
  status"`).
* Every card carries state as **text** (`OK`, `Watch`, `Needs attention`,
  `State unknown`, `Source unavailable`) — never colour alone.
* No trend indicator is displayed: no authoritative trend exists, so none is
  invented.
* No overall "health score" exists and none was added.

## 4. Navigation

Every destination is an existing route; no duplicate page or API was created.

| Object | Destination | Filter carried |
|--------|-------------|----------------|
| Critical incidents | `/incidents?severity=critical` | **yes** (`?severity=`) |
| High priority | `/incidents?severity=warning` | **yes** ("high" is the warning band of the incident API) |
| Unacknowledged alerts | `/alerts` | n/a — `?severity=` is not supported there, so none is invented |
| Degraded systems (card) | `/infrastructure` | n/a |
| Devices online/offline, sensors, collector success | from `operationalHealth.*.href` | unchanged |
| AI tasks | `/ai-copilot` | n/a |
| **An incident row** | `/incidents/{id}` | the PHASE 3 investigation workspace |
| **A degraded device row** | `/infrastructure/assets/{device id}` | resolved by authoritative device id |
| Collection-pipeline evidence | `/monitoring` | none invented (`/monitoring` does not read a `view` param) |
| Operations | `/incidents`, `/alerts`, `/changes`, `/automation` | none |
| AI | `/ai-copilot` | none |

### 4.1 Why the device link uses the id, not the name

The degraded rows are built from `Device` records
(`dashboardDataAdapter.buildAttentionRequired` →
`{ id: d.id, name: d.name, type: d.device_type, status }`). The displayed `name`
is frequently an **IP address** (`10.10.13.0`, `192.168.159.10`, ...), so the link
uses `row.id` — the authoritative record id — and never treats an IP as the
identity of a device. The target is the existing
`app/(dashboard)/infrastructure/assets/[deviceId]/page.tsx`, which fetches the
device by that id. `row.type` (the backend's own `device_type`) is shown as the
resource type, and when the backend reports none the row reads
`type not reported` rather than inventing a class.

## 5. Loading, empty and unavailable states

These three states stay distinct, per section, and each section degrades
independently — a dead collector API cannot blank the incident or device panels.

| State | Trigger | What renders |
|-------|---------|--------------|
| loading | `states.attention === "loading"` | `Loading attention data…` — never a `0` |
| error | incident + alert + device sources all failed | `Attention data unavailable - incidents, alerts and devices could not be read.` |
| partial | some (not all) sources failed | `Some sources did not respond - those counters show N/A rather than 0.` |
| empty (measured) | source answered, count is 0 | `Nothing critical or high-priority is open.` / `No device is reporting a degraded or down state.` |
| empty (unavailable) | `data.metrics[0].value === null` | `Not available - the incident API did not respond.` |
| evidence unavailable | every row in the block is `N/A` | `Not available — the aggregation endpoint did not respond.` |

The empty-vs-unavailable distinction is decided on the **adapter's** recorded
availability (`data.metrics[n].value === null`), not on `length === 0`, so an
empty estate and a failed fetch cannot be confused.

## 6. API dependencies (unchanged)

PHASE 2 added **no API call, no hook and no query key**. The dashboard still makes
exactly the requests `useDashboardData()` / `useSocData()` already made:

* `GET /api/incidents/intelligence` — correlation evidence (PHASE 3.1 contract)
* `GET /api/incidents/collector-health` — collection-pipeline evidence
  (`window_hours=24`, authoritative `current_state` counts)
* `listIncidents`, `listAlerts`, `getDevices` — the attention counters and the L4
  rows behind them
* the existing `operationalHealth.*` derivations — the estate/health cards

No per-card fetch and no N+1 was introduced: the L4 rows come from the same
`listIncidents` / `getDevices` payloads the counters are computed from.

## 7. Performance decisions

* **No new requests.** The reorder and the captions are pure render changes.
* **No polling change.** Refresh remains whatever `useDashboardData` already did
  (60s React Query intervals, `refetchInterval`); nothing new was added and
  nothing was removed, so no new cleanup/unmount requirement was created.
* **No new client maths for the L5 evidence.** The numbers are the backend's
  aggregates, rendered verbatim; only the hint text (the backend's definition)
  changed.
* Server render cost is unchanged: one extra `<h3>` per level band.

## 8. Accessibility decisions

* Every newly-interactive row is a real `next/link` `<a>` — keyboard reachable and
  in the tab order — not a `div` with an `onClick`.
* Focus is visible through the **existing** global rule
  `dashboard.css:39` — `.hs-app a:focus-visible { outline: 2px solid var(--cyan);
  outline-offset: 2px }` — so no new CSS was needed and the focus ring matches the
  rest of the shell.
* Each row carries an `aria-label` that states the object and its state, e.g.
  `Open device 192.168.159.100, type firewall, current status degraded` and
  `Open incident WAN link down, severity critical, status open`. The KPI cards
  already exposed label + value + state + context in one accessible label.
* Level bands are real `<h3>` elements inside the section's `<h2>`, giving the
  page a correct heading outline instead of decorative divisions.
* Status is always also text (`degraded`, `failing`, `unknown`), so nothing relies
  on colour alone.

## 9. Files changed

| File | Change |
|------|--------|
| `frontend/src/lib/dashboard/commandKpis.ts` | Critical/high-priority cards now carry the severity filter (`?severity=critical`, `?severity=warning`) and say so in their context line; navigation rationale documented in the header. |
| `frontend/src/components/dashboard/AttentionRequired.tsx` | Level captions (L1-3, L4, L5, L6, L7); incident rows and degraded-device rows are now links (`/incidents/{id}`, `/infrastructure/assets/{id}`) with aria-labels and the backend's resource type; collection-pipeline hints corrected to the PHASE 3.1 definitions; `NEXT_ACTIONS` split into `OPERATIONS_ACTIONS` (incidents, alerts, changes, automation) and `AI_ACTIONS`; `EvidenceBlock` gained an optional destination link; docstring rewritten to the level map. |
| `frontend/src/components/dashboard/UnifiedCommandCenter.tsx` | `AI Operations / AI CTO` moved from row 3 to the end (L7); section nav reordered to match; zone comments relabelled to levels; docstring rewritten. |
| `docs/architecture/phase-2-dashboard-hierarchy.md` | this document (new). |

### 9.1 Provenance and the two-step commit

`AttentionRequired.tsx` and `UnifiedCommandCenter.tsx` were **not tracked in HEAD**
when this milestone ran. The dashboard described in §1 exists only as an
*uncommitted* restructure from another session:

```
working tree:  dashboard/page.tsx -> UnifiedCommandCenter (untracked) -> AttentionRequired (untracked)
HEAD:          dashboard/page.tsx -> CommandCenter      (the older composition)
```

PHASE 2 therefore improved those files **in place**, which means the work was
committed in two steps so the attribution is explicit and reviewable:

| Commit | Contents | Attribution |
|--------|----------|-------------|
| 1 | `commandKpis.ts` (tracked; a PHASE 1 file) + this document | entirely PHASE 2 |
| 2 | `AttentionRequired.tsx` + `UnifiedCommandCenter.tsx` | PHASE 2 changes layered on previously-untracked files from the in-flight dashboard restructure |

Because those two files were untracked, **no pre-edit copy existed**: their first
commit necessarily records the other session's baseline content as well as the
PHASE 2 edits. That is stated plainly in the commit message rather than presented
as PHASE 2 authorship.

**Deliberately not changed:** every protected WIP file (see §11), the
`hud-btn` class issue, `CoreHUD`, the SOC adapter's demo series, and any backend
file (PHASE 2 needed no backend change — every value it displays already had an
authoritative contract after PHASE 3.1).

## 10. Tests and validation

| Check | Result |
|-------|--------|
| `tsc --noEmit` (whole frontend) | **0 errors** |
| `eslint` on the three changed files | **0 findings** |
| `npm run build` (Next 16 production) | **Compiled successfully in 14.4s**, `Finished TypeScript in 24.4s`, 45/45 static pages generated, no errors |
| Built route table | `/incidents` static, `/incidents/[id]` **dynamic**, `/infrastructure/assets/[deviceId]` **dynamic**, plus `/changes`, `/automation`, `/monitoring`, `/alerts`, `/ai-copilot` — every link target exists |
| Route matrix against the production server (`:3100`) | **13/13 return 200**, including both dynamic detail routes with a synthetic id |
| PHASE 2 strings present in the production bundle | **8/8 found** (`Level 5 - Evidence`, `Level 7 - AI`, `Open monitoring sources`, `Review the change queue`, `no failed run in the window`, `not a failure`, `severity=critical`, `infrastructure/assets/`) |
| Backend tests | **not re-run for PHASE 2** — no backend file was touched; the relevant contract tests are PHASE 3.1's (`tests/test_incident_lifecycle.py`, 13 passed baseline / 22 of 26 passed with 0 failures after the contract change). |

### 10.1 Browser smoke-test method and one environment caveat

The dev server that was already listening on `:3000` **could not be used as a
validation target**, and this is pre-existing, not caused by PHASE 2: it returned
Next's built-in 404 for routes that exist and that this milestone never touched —
including `/reports/alert-analysis` (a committed static route) and
`/infrastructure/assets/[deviceId]/dependencies`. Its route manifest was
incomplete, so any 404 from it would have been a false signal.

The trustworthy target was therefore produced from scratch: `npm run build`
followed by `next start -p 3100`, then the route matrix above. Every URL the
dashboard links to answered **200**. (`next start` prints a warning that
`output: "standalone"` prefers `node .next/standalone/server.js`; it still served
the build correctly, and the route matrix is the evidence.)

The dashboard's server HTML is an auth/loading shell for an unauthenticated
request, so a marker-by-marker check of the *rendered* DOM needs a signed-in
session. Instead the hierarchy was verified two ways that do not depend on auth:
all link targets answer 200, and every new string is present in the production
bundle (`src_0p0go_v._.js`), i.e. the code that renders the hierarchy shipped.

## 11. Known limitations and deferred work

1. **Level 2 "Affected sites" / "critical services affected", Level 3 "Sites
   online", and Level 5 "Recent changes" / "Recent automation" were deliberately
   NOT added.** Each needs a new field on `AttentionRequiredData` /
   `AttentionEvidence` or `OperationalHealthData`, i.e. edits to
   `dashboardModels.ts` and `dashboardDataAdapter.ts`. Both files are inside the
   protected 22-file WIP set, so touching them would risk another session's
   in-flight work. The sites/estate information is already visible in the
   Operational Health zone (`OperationalHealth`, `AssetsSummary`), so nothing is
   hidden by deferring it.
2. **Per-source collector detail is not surfaced in the evidence panel.** The
   block shows the four authoritative `current_state` counts (healthy / degraded /
   failing / unknown) with the correct definitions, but not the per-source
   `failure_rate_pct`, `failed_runs`, `last_failure_at` that PHASE 3.1 now serves.
   Showing them would again require new model fields in the protected files. The
   *panel-level* link to `/monitoring` is provided instead; there is no per-source
   link because the payload carries counts, not rows, and inventing a row list
   would mean fetching data this page does not have.
3. **`duplicate_alerts_removed` is not shown** in the correlation evidence block
   for the same reason (no field on `AttentionEvidence`).
4. **`hud-btn` is still undefined in CSS.** Out of scope here and explicitly not
   required by this milestone; the incident page was not restyled.
5. **`CoreHUD` remains an orphan component with hardcoded metrics.** Untouched, as
   instructed.
6. **The SOC adapter's demo series / MITRE / geo data remain in
   `socAdapter.ts`.** They are labelled `source: "fallback"` and `SocDashboard` is
   still unmounted, so nothing displays them; they were neither expanded nor
   promoted into the dashboard hierarchy. `SocKpiCards` continues to render the
   blocked/identity/malware KPIs in their honest typed `null` state (PHASE 3.1
   established that no authoritative source exists for those three).
7. **No overall health score was introduced** — no authoritative calculation
   exists, and inventing one (92%, 87%, …) is forbidden.


