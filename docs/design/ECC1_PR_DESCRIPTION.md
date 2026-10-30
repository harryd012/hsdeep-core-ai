## Summary

**Executive Command Center (ECC-1).** `/dashboard` is now one screen instead of ~14 stacked panels:
- 8 KPI tiles: devices, healthy, critical/down, active incidents, critical incidents, collector health, MTTA, MTTR.
- Three rows of cards: incident trends, severity, estate health, top sites, site honeycomb, collector health, aging, recent incidents, weekday×hour heatmap, resolution times.
- Time / site / severity filters live in the URL.
- Honest states: `null` shows "—" + "Not measured", a real zero shows `0`, a "vs previous 24h" delta appears only when both windows were measured, and each card has its own loading / error / empty state.
- The NOC / SOC / cloud / AI panels are not deleted; they stay on their own pages.

**Backend (additive only):**
- `ResponseKpis` gains `mtta_minutes` and a `previous` window block (volume, resolved, MTTR, MTTA).
- `GET /api/dashboard/incident-analytics` accepts optional `site_id` and `severity`.
- New `GET /api/dashboard/site-health`, reusing the brief's device-health rule.
- Migration `e8c1d4f7a2b9` adds a covering index for active-incident aggregates. On a 2.7M-incident tenant, the aging query went from 45 s to ~3 s warm and the analytics read from 46 s to 3.6 s.

**Clean-checkout build.** `main` referenced files that were never committed, so a fresh clone could not install, import or build. This adds:
- the frontend `package.json`, `next.config.js`, `tsconfig.json` (with the `@/*` alias), PostCSS/ESLint configs, `.gitignore`, `pnpm-workspace.yaml`, Dockerfile;
- the `datadog` and `paloalto` collectors;
- SOC / network-intelligence components that tracked code imports.

`next.config.js` gains `output: "standalone"`, which the Dockerfile needs.

## Verification (in a fresh clean checkout of this branch)
- `pnpm install --frozen-lockfile` ✅
- `next build` ✅: all routes, `.next/standalone/server.js` produced
- `import app.main` ✅; `pytest backend/tests`: 2357 passed, 1 skipped
- `node --test frontend/tests/command-center-format.test.mjs`: 9 passed
- Checked manually in the browser against the dev tenant at 1440 / 1024 / 375 wide: filters round-trip through the URL, and a failing read degrades only its own cards.

## Known gaps
- `tsc` still reports errors in `UnifiedCommandCenter.tsx` / `useDashboardData.ts` (the two are out of step on `main`) and in the unused `ExecutiveMetrics.tsx`. `ignoreBuildErrors: true` keeps the build green, but it also hides new type errors.
- The app shell sidebar does not collapse on phones, so 375 px scrolls sideways. The dashboard grid itself fits from 1024 px up.
- The site honeycomb is untested with real sites (the dev tenant has none).
- Deploying requires `alembic upgrade head` (the migration uses `CREATE INDEX CONCURRENTLY`).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
