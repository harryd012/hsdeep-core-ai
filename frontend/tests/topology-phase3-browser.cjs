/* Phase 3 browser validation (Step 17).
 *
 * Verifies the topology page renders the real empty state for a tenant with no
 * discovered relationships, that counts are real, that no fake edges / sites /
 * coordinates are drawn, and that there are no console, page or hydration
 * errors. Real login, no mocking.
 *
 * Each route is captured in its OWN browser context. Capturing them
 * sequentially in one context degrades the session (repeated hard navigations
 * trip the auth guard's re-validation and land on /login), which would report
 * a blank page for a page that is actually fine.
 *
 * node tests/topology-phase3-browser.cjs
 */
const { chromium } = require('playwright');
const path = require('node:path');
process.loadEnvFile(path.resolve(__dirname, '../.env.local'));

const BASE = 'http://localhost:3000';
// The canonical topology route in this app is `/infrastructure-topology`
// (src/components/Sidebar.tsx line 55 is the nav entry, and
// src/app/(dashboard)/infrastructure-topology/page.tsx is the only topology
// page on disk). The spec's `/infrastructure/topology` path does not exist.
const TOPOLOGY_ROUTE = '/infrastructure-topology';
const ROUTES = ['/infrastructure', TOPOLOGY_ROUTE, '/infrastructure/discovery'];

async function capture(browser, route, collect, waitForText = [], afterCollect = null) {
  const ctx = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(`pageerror: ${e.message}`));
  page.on('console', m => { if (m.type() === 'error') errors.push(`console.error: ${m.text()}`); });

  await page.goto(BASE + route, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(4000);

  if (await page.locator('input[type=email]').count()) {
    await page.locator('input[type=email]').fill(process.env.NEXT_PUBLIC_AUTH_EMAIL);
    await page.locator('input[type=password]').fill(process.env.NEXT_PUBLIC_AUTH_PASSWORD);
    await page.locator('button[type=submit]').click();
    await page.waitForTimeout(7000);
  }
  if (!page.url().includes(route)) {
    await page.goto(BASE + route, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForTimeout(7000);
  }

  // Wait for the content under test to actually appear rather than guessing at
  // a delay: every page needs a non-empty <main>, and the topology page
  // additionally needs its graph section. A timeout is not swallowed silently -
  // the probe below still runs and reports the real state as a failure.
  const phrases = waitForText.length
    ? waitForText
    : ['']; // '' matches any non-empty text
  try {
    await page.waitForFunction(
      (needles) => {
        const el = document.querySelector('main');
        if (!el) return false;
        const t = el.innerText.trim();
        if (t.length === 0) return false;
        return needles.some((p) => p === '' || t.includes(p));
      },
      phrases,
      { timeout: 30000 },
    );
  } catch {
    errors.push(`timed out waiting for content: ${phrases.join(' | ') || '<non-empty main>'}`);
  }
  await page.waitForTimeout(800);

  const info = await page.evaluate(collect);
  // Optional post-probe interaction (e.g. exercising the node drawer) that runs
  // while this context is still authenticated.
  let extra = {};
  if (typeof afterCollect === "function") {
    extra = await afterCollect(page, ctx);
    if (Array.isArray(extra.errors)) errors.push(...extra.errors);
  }
  await page.screenshot({
    path: path.resolve(__dirname, `../layout-validation/topology-phase3-${route.replace(/\//g, '_')}.png`),
    fullPage: false,
  });
  const url = page.url();
  await ctx.close();
  return { url, info: { ...info, ...extra }, errors };
}

const ROUTE_PROBE = () => {
  const main = document.querySelector('main');
  const text = main ? main.innerText : '';
  return {
    route: location.pathname,
    mainPresent: Boolean(main),
    textLength: text.length,
    overflow: document.documentElement.scrollWidth > window.innerWidth,
    saysNetworkHealthy: /network is healthy/i.test(text),
    saysNoIssues: /no issues/i.test(text),
    hasNotDiscoveredBadge: /NOT DISCOVERED|READY|PARTIAL/.test(text),
    hasLimitations: text.includes('TOPOLOGY LIMITATIONS'),
  };
};

const TOPOLOGY_PROBE = () => {
  const main = document.querySelector('main');
  const text = main ? main.innerText : '';
  const grab = (label) => {
    const m = text.match(new RegExp('(\\d+)\\s+' + label));
    return m ? Number(m[1]) : null;
  };
  return {
    route: location.pathname,
    mainPresent: Boolean(main),
    textLength: text.length,
    overflow: document.documentElement.scrollWidth > window.innerWidth,
    saysNetworkHealthy: /network is healthy/i.test(text),
    saysNoIssues: /no issues/i.test(text),
    hasNotDiscoveredBadge: /NOT DISCOVERED|READY|PARTIAL/.test(text),
    hasLimitations: text.includes('TOPOLOGY LIMITATIONS'),
    devices: grab('Devices'),
    sensors: grab('Sensors'),
    monitoringSources: grab('Monitoring sources'),
    interfaces: grab('Interfaces'),
    relationships: grab('Relationships'),
    sectionButtons: document.querySelectorAll('section button').length,
    nodeCards: document.querySelectorAll('[data-topology-node]').length,
    nodeHealthBuckets: Array.from(document.querySelectorAll('[data-topology-health]'))
      .map(el => el.getAttribute('data-topology-health'))
      .reduce((acc, h) => { acc[h] = (acc[h] ?? 0) + 1; return acc; }, {}),
    emptyStateHeading: text.includes('No topology relationships discovered'),
    edgeRowsRendered: (text.match(/discovered relationship/g) || []).length,
    hasRunDiscoveryAction: text.includes('Run Discovery'),
    hasViewDevicesAction: text.includes('View Devices'),
    hasViewMonitoringAction: text.includes('View Monitoring'),
    hasUpdatedTimestamp: /Updated \d/.test(text),
    // Provider markup specifically (the page's own React divs are not a leak).
    htmlHasLogmessage: /logmessage|moreicon/i.test(main ? main.innerHTML : ''),
  };
};

/* Exercises the Step 9 node drawer on an ALREADY-AUTHENTICATED topology page:
 * opens it from a real node card, asserts real values, and HTTP-checks every
 * action link so a dead link cannot pass.
 *
 * It deliberately reuses the caller's page/context instead of logging in again:
 * a 4th consecutive login+navigation in one browser run intermittently stalls
 * the auth guard, which would fail this check for reasons unrelated to the
 * drawer. */
async function verifyNodeDrawer(page, ctx) {
  const out = { opened: false, closed: false, text: '', actions: [], deadLinks: [], edgeDrawerAbsentInitially: false };
  try {
    await page.waitForSelector('[data-topology-node]', { timeout: 30000 });

    // With zero relationships no edge may be selectable, so the edge drawer
    // must not be present.
    out.edgeDrawerAbsentInitially = (await page.locator('[data-topology-drawer="edge"]').count()) === 0;

    await page.locator('[data-topology-node]').first().click();
    await page.waitForSelector('[data-topology-drawer="node"]', { timeout: 10000 });
    out.opened = true;
    out.text = (await page.locator('[data-topology-drawer="node"]').innerText()).replace(/\s+/g, ' ').trim();

    out.actions = await page.locator('[data-topology-drawer="node"] a').evaluateAll(
      els => els.map(e => e.getAttribute('href')),
    );
    // A link that 404s is a dead end, which the spec forbids.
    for (const href of out.actions) {
      const res = await ctx.request.get(BASE + href, { maxRedirects: 0, failOnStatusCode: false });
      if (res.status() >= 400) out.deadLinks.push({ href, status: res.status() });
    }

    await page.locator('[data-topology-drawer="node"] button', { hasText: 'Close' }).click();
    await page.waitForTimeout(500);
    out.closed = (await page.locator('[data-topology-drawer="node"]').count()) === 0;
  } catch (e) {
    out.errors = [`drawer: ${e.message}`];
  }
  return out;
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const results = [];
  const errors = [];
  let topo = null;
  let drawer = null;
  try {
    for (const route of ROUTES) {
      // The topology route gets the deep probe in the SAME capture, so there is
      // no redundant extra login+navigation (which intermittently stalls).
      const isTopo = route === TOPOLOGY_ROUTE;
      const r = await capture(
        browser, route,
        isTopo ? TOPOLOGY_PROBE : ROUTE_PROBE,
        isTopo ? ['TOPOLOGY LIMITATIONS', 'No topology relationships discovered'] : [],
        // Step 9: exercise the node drawer on this already-authenticated page.
        isTopo ? verifyNodeDrawer : null,
      );
      results.push({ requested: route, url: r.url, ...r.info });
      errors.push(...r.errors);
      if (isTopo) {
        topo = r.info;
        drawer = {
          opened: r.info.opened, closed: r.info.closed, text: r.info.text,
          actions: r.info.actions, deadLinks: r.info.deadLinks,
          edgeDrawerAbsentInitially: r.info.edgeDrawerAbsentInitially,
        };
      }
    }

    const failed =
      errors.length > 0 ||
      results.some(r => !r.mainPresent || r.textLength === 0 || r.overflow ||
        r.saysNetworkHealthy || r.saysNoIssues) ||
      !topo.emptyStateHeading ||
      !topo.hasRunDiscoveryAction ||
      !topo.hasViewDevicesAction ||
      !topo.hasViewMonitoringAction ||
      !topo.hasUpdatedTimestamp ||
      // Real counts, matching the verified API values for this tenant.
      topo.devices !== 9 ||
      topo.sensors !== 34 ||
      topo.monitoringSources !== 2 ||
      topo.interfaces !== 0 ||
      // 9 devices + 2 monitoring sources = 11 real nodes rendered, and every
      // device node carries a canonical Phase 2 health bucket.
      topo.nodeCards !== 11 ||
      !topo.nodeHealthBuckets.healthy ||
      // With zero relationships, no edge may be drawn.
      topo.relationships !== 0 ||
      topo.edgeRowsRendered !== 0 ||
      topo.htmlHasLogmessage ||
      // Step 9/10: the drawer must open, show real values, close again, and
      // offer no link that 404s. No edge drawer may exist while edges are 0.
      !drawer.opened ||
      !drawer.closed ||
      !drawer.edgeDrawerAbsentInitially ||
      drawer.deadLinks.length > 0 ||
      !drawer.text.includes('NODE DETAIL') ||
      !drawer.text.includes('METRICS') ||
      !drawer.text.includes('ACTIONS') ||
      drawer.actions.length === 0;

    console.log(JSON.stringify({ results, topology: topo, drawer, errors }, null, 2));
    if (failed) process.exitCode = 1;
  } finally {
    await browser.close();
  }
})().catch(e => { console.error('ERROR:', e.message); process.exitCode = 1; });
