/* Real-browser verification of the LIVE INFRASTRUCTURE INTELLIGENCE zone.
 *
 *   node tests/live-infra-intel-browser.cjs
 *
 * Runs against a PRODUCTION build served on :3100 (the dev server on :3000 has a
 * pre-existing broken route manifest that 404s existing routes). The primary pass
 * uses the REAL backend on :8000, so the zone is verified against real contract
 * data. Two extra passes force the state matrix by intercepting only the
 * browser's own fetches (never the backend):
 *   A. live            - the real dashboard
 *   B. deps-aborted    - the dependency read fails  -> unavailable state
 *   C. empty-estate    - no devices/alerts/incidents -> empty state
 */
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
process.loadEnvFile(path.resolve(__dirname, '../.env.local'));

const BASE = process.env.LII_BASE || 'http://localhost:3000';
const OUT = path.resolve(__dirname, '..', 'layout-validation');
const ZONE = '#infrastructure-intelligence';

async function signIn(page) {
  await page.waitForTimeout(3000);
  if (await page.locator('input[type=email]').count()) {
    await page.locator('input[type=email]').fill(process.env.NEXT_PUBLIC_AUTH_EMAIL);
    await page.locator('input[type=password]').fill(process.env.NEXT_PUBLIC_AUTH_PASSWORD);
    await page.locator('button[type=submit]').click();
    // The app routes client-side, so no `load` event fires: wait for the URL
    // with `commit` and tolerate a slow first paint rather than failing here.
    try {
      await page.waitForURL('**/dashboard', { timeout: 20000, waitUntil: 'commit' });
    } catch {
      await page.waitForTimeout(5000);
    }
  }
  return page.url();
}

async function runScenario(browser, name, setup, expect) {
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  const checks = {};
  const check = (key, ok, detail) => {
    checks[key] = { ok: Boolean(ok), detail: detail === undefined ? '' : String(detail) };
  };
  const errors = [];
  const api = [];
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(`console: ${m.text()}`); });
  page.on('response', (r) => {
    try {
      const u = new URL(r.url());
      if (u.port === '8000') api.push({ path: u.pathname, status: r.status() });
    } catch { /* not a URL */ }
  });

  if (setup) await setup(page);

  await page.goto(`${BASE}/dashboard`, { waitUntil: 'domcontentloaded', timeout: 90000 });
  const afterSignInUrl = await signIn(page);

  // Loading state: briefly poll for the zone's own skeleton copy.
  let sawLoading = false;
  for (let i = 0; i < 20; i += 1) {
    const early = await page.locator(ZONE).innerText().catch(() => '');
    if (/Resolving infrastructure context/.test(early)) { sawLoading = true; break; }
    await page.waitForTimeout(150);
  }

  await page.waitForTimeout(11000);

  const zone = page.locator(ZONE);
  check('signed-in-on-dashboard', !/\/login/.test(afterSignInUrl), afterSignInUrl);
  check('zone-present', (await zone.count()) === 1);
  const text = await zone.innerText().catch(() => '');

  check('left-panel-title', /Infrastructure Context/i.test(text));
  check('right-panel-title', /Impact & Correlation/i.test(text));
  check('strip-telemetry', /Telemetry/i.test(text));
  check('strip-graph-evidence', /Graph evidence/i.test(text));
  check('strip-collection', /Collection/i.test(text));
  check('strip-activity', /Recent activity/i.test(text));
  check('correlation-block-present', /Correlation/i.test(text));

  // Scenario-specific state expectations (proves each state actually renders).
  for (const [key, re] of Object.entries(expect ?? {})) {
    check(key, re.test(text), text.slice(0, 300));
  }

  // Ordering: attention < zone < operational health.
  const y = await page.evaluate(() => ({
    attention: document.getElementById('attention')?.getBoundingClientRect().y ?? null,
    zone: document.getElementById('infrastructure-intelligence')?.getBoundingClientRect().y ?? null,
    health: document.getElementById('operational-health')?.getBoundingClientRect().y ?? null,
  }));
  check(
    'order-attention-zone-health',
    y.attention !== null && y.zone !== null && y.health !== null && y.attention < y.zone && y.zone < y.health,
    JSON.stringify(y),
  );

  // No horizontal overflow at any tested width.
  const widths = [1920, 1440, 1366, 1024, 820, 390];
  const overflow = [];
  for (const width of widths) {
    await page.setViewportSize({ width, height: 1080 });
    await page.waitForTimeout(350);
    const m = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      innerWidth: window.innerWidth,
    }));
    overflow.push({ width, ...m });
  }
  check('no-horizontal-overflow', overflow.every((o) => o.scrollWidth <= o.innerWidth + 1), JSON.stringify(overflow));

  // Links are real anchors to existing routes, with accessible names.
  const links = await page.evaluate(() => {
    const z = document.getElementById('infrastructure-intelligence');
    if (!z) return [];
    return [...z.querySelectorAll('a')].map((a) => ({
      href: a.getAttribute('href'),
      label: (a.textContent || '').trim(),
    }));
  });
  check('links-resolve-to-existing-routes', links.length > 0 && links.every((l) => typeof l.href === 'string' && l.href.startsWith('/')), JSON.stringify(links));
  check('links-have-labels', links.length > 0 && links.every((l) => l.label.length > 0), JSON.stringify(links.map((l) => l.label)));
  check('no-root-only-links', links.every((l) => l.href !== '/'), JSON.stringify(links.map((l) => l.href)));

  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.waitForTimeout(400);
  await zone.scrollIntoViewIfNeeded();
  const shot = path.join(OUT, `live-infra-intel-${name}.png`);
  await page.screenshot({ path: shot, fullPage: true });
  const zoneShot = path.join(OUT, `live-infra-intel-${name}-zone.png`);
  await zone.screenshot({ path: zoneShot }).catch(() => {});

  const duplicates = {};
  for (const call of api) duplicates[call.path] = (duplicates[call.path] ?? 0) + 1;

  await page.close();
  return { name, checks, errors, apiCalls: duplicates, apiTotal: api.length, text, links, screenshot: shot, zoneShot, sawLoading };
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  fs.mkdirSync(OUT, { recursive: true });
  const results = [];
  try {
    results.push(
      await runScenario(browser, 'live', null, {
        'state-live': /CORRELATED|NO DATA|UNAVAILABLE|ANALYZING/,
      }),
    );

    results.push(
      await runScenario(
        browser,
        'deps-aborted',
        async (page) => {
          await page.route('**/api/infrastructure/dependencies/**', (route) => route.abort());
        },
        { 'state-deps-unavailable': /unavailable right now|Impact data unavailable/i },
      ),
    );

    results.push(
      await runScenario(
        browser,
        'empty-estate',
        async (page) => {
          const graph = {
            tenant_id: 't', generated_at: new Date().toISOString(), status: 'not_discovered',
            nodes: [], edges: [],
            summary: { nodes_total: 0, edges_total: 0, devices_total: 0, sites_total: 0, monitoring_sources_total: 0, interfaces_total: 0, sensors_total: 0 },
            limitations: ['no relationship evidence recorded'],
          };
          // Precise per-path interception: only the endpoints that decide whether a
          // focus exists are emptied, so nothing else in the dashboard is broken.
          await page.route('**/api/**', (route) => {
            const p = new URL(route.request().url()).pathname;
            if (p === '/api/devices') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
            if (p === '/api/incidents') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
            if (p === '/api/infrastructure/topology/graph') {
              return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(graph) });
            }
            return route.continue();
          });
        },
        { 'state-empty': /No active infrastructure context available/i },
      ),
    );
  } finally {
    await browser.close();
  }

  const summary = {
    base: BASE,
    results: results.map((r) => ({
      name: r.name,
      failures: Object.entries(r.checks).filter(([, v]) => !v.ok).map(([k, v]) => `${k}: ${v.detail}`),
      checks: r.checks,
      sawLoading: r.sawLoading,
      apiTotal: r.apiTotal,
      apiCalls: r.apiCalls,
      errors: r.errors,
      text: r.text,
      links: r.links,
    })),
  };
  fs.writeFileSync(path.join(OUT, 'live-infra-intel.json'), JSON.stringify({ summary, results }, null, 2));
  console.log(JSON.stringify(summary, null, 2));

  const failed = summary.results.flatMap((r) => r.failures);
  const errs = summary.results.flatMap((r) => r.errors);
  if (failed.length || errs.length) process.exitCode = 1;
})().catch((e) => { console.error(e); process.exitCode = 1; });
