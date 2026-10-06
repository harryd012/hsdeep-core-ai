/* P0 final browser verification: /monitoring, /infrastructure-topology,
 * /sop-engine. Real login, no mocking, no full test-suite run.
 *
 * Verifies ONLY the surfaces touched by the P0 hardening recovery:
 *   - monitoring: canonical sensor states, Freshness column, no stale-as-live,
 *     stat cards hold real numbers
 *   - topology:   QUICK SUMMARY lands BEFORE the bounded graph, node drawer
 *     opens with "Data freshness" derived from last-seen, no console errors
 *   - sop-engine: list loads, detail resolves current version, steps render or
 *     the empty state is honest
 *
 * Each route gets its OWN browser context (a 4th consecutive login in one run
 * intermittently stalls the auth guard — see topology-phase3-browser.cjs).
 *
 * node tests/p0-final-browser-verify.cjs
 */
const { chromium } = require('playwright');
const path = require('node:path');
process.loadEnvFile(path.resolve(__dirname, '../.env.local'));

const BASE = 'http://localhost:3000';

const MONITOR_STATES = [
  'Healthy', 'Warning', 'Critical', 'Down',
  'Stale', 'Unknown', 'Collection failed', 'Not configured',
];

async function open(browser, route, waitForText) {
  const ctx = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
  const page = await ctx.newPage();
  const errors = [];
  const apiCalls = [];
  page.on('pageerror', e => errors.push(`pageerror: ${e.message}`));
  page.on('console', m => { if (m.type() === 'error') errors.push(`console.error: ${m.text()}`); });
  page.on('response', r => {
    const u = r.url();
    if (u.includes('/api/')) apiCalls.push({ url: u.replace(BASE, ''), status: r.status() });
  });

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
  try {
    await page.waitForFunction(
      (needles) => {
        const el = document.querySelector('main');
        if (!el) return false;
        const t = el.innerText.trim();
        return t.length > 0 && needles.some(p => p === '' || t.includes(p));
      },
      waitForText.length ? waitForText : [''],
      { timeout: 30000 },
    );
  } catch {
    errors.push(`timed out waiting for content: ${waitForText.join(' | ') || '<non-empty main>'}`);
  }
  return { ctx, page, errors, apiCalls };
}

async function verifyMonitoring(browser) {
  const { ctx, page, errors, apiCalls } = await open(browser, '/monitoring', ['PRTG SENSORS']);
  let probe = {};
  try {
    await page.waitForFunction(
      () => Array.from(document.querySelectorAll('th')).some(th => th.innerText.trim() === 'Freshness'),
      { timeout: 20000 },
    );
    probe = await page.evaluate((states) => {
      const text = document.body.innerText;
      const headers = Array.from(document.querySelectorAll('th')).map(t => t.innerText.trim());
      const table = Array.from(document.querySelectorAll('table')).find(t =>
        Array.from(t.querySelectorAll('th')).some(th => th.innerText.trim() === 'Freshness'));
      const rows = table
        ? Array.from(table.querySelectorAll('tbody tr')).map(tr => {
            const cells = Array.from(tr.querySelectorAll('td'));
            const fresh = cells[cells.length - 1];
            return {
              status: (cells[1] ? cells[1].innerText : '').trim(),
              freshness: (fresh ? fresh.innerText : '').trim(),
              freshnessTitle: (fresh ? fresh.getAttribute('title') : '') || '',
            };
          })
        : [];
      const cardOf = (label) => {
        const m = text.match(new RegExp('(\\d+)\\s*\\n\\s*' + label));
        return m ? Number(m[1]) : null;
      };
      const sensors = cardOf('Sensors');
      const healthy = cardOf('Healthy');
      const alerting = cardOf('Alerting');
      return {
        hasFreshnessColumn: headers.includes('Freshness'),
        rowCount: rows.length,
        statusLabels: [...new Set(rows.map(r => r.status))],
        statusLabelsAreCanonical: rows.every(r => states.includes(r.status)),
        freshnessLabels: [...new Set(rows.map(r => r.freshness.replace(/\s*·.*$/, '')))],
        staleShownAsLive: rows.filter(r =>
          /^LIVE/.test(r.freshness) && /never collected/i.test(r.freshnessTitle)).length,
        statCards: { sensors, healthy, alerting },
        statCardsSane: [sensors, healthy, alerting].every(n => n !== null && n >= 0)
          && sensors !== null && healthy + alerting <= sensors,
        unexpectedHeaders: headers.filter(h => !['Sensor', 'Status', 'Value', 'Metric', 'Freshness'].includes(h)),
      };
    }, MONITOR_STATES);
  } catch (e) {
    errors.push(`monitoring probe: ${e.message}`);
  }
  await page.screenshot({ path: path.resolve(__dirname, '../layout-validation/p0-monitoring.png') });
  await ctx.close();
  return { route: '/monitoring', probe, errors, apiErrors: apiCalls.filter(c => c.status >= 500) };
}

async function verifyTopology(browser) {
  const { ctx, page, errors, apiCalls } = await open(browser, '/infrastructure-topology', ['QUICK SUMMARY']);
  const probe = {};
  try {
    probe.summaryRendered = await page.evaluate(() =>
      document.body.innerText.includes('QUICK SUMMARY'));

    const graphStart = Date.now();
    try {
      await page.waitForFunction(
        () => document.querySelectorAll('[data-topology-node]').length > 0
          || /No topology relationships discovered|TOPOLOGY LIMITATIONS/.test(document.body.innerText),
        { timeout: 30000 },
      );
      probe.graphLoaded = true;
      probe.graphMs = Date.now() - graphStart;
    } catch {
      probe.graphLoaded = false;
      probe.graphTimeout = true;
    }

    probe.summaryStillVisibleAfterGraph = await page.evaluate(() =>
      document.body.innerText.includes('QUICK SUMMARY'));
    probe.nodesRendered = await page.locator('[data-topology-node]').count();
    probe.boundedNodes = probe.nodesRendered <= 100; // API default limit=100
    probe.honestStatus = await page.evaluate(() =>
      /TOPOLOGY LIMITATIONS|No topology relationships discovered|READY|NOT DISCOVERED|PARTIAL/.test(document.body.innerText));
    probe.noHealthFabrication = await page.evaluate(() =>
      !/network is healthy|no issues/i.test(document.body.innerText));
    probe.summaryNumbers = await page.evaluate(() => {
      const t = document.body.innerText;
      const grab = (label) => {
        const m = t.match(new RegExp(label + '\\D*(\\d+)'));
        return m ? Number(m[1]) : null;
      };
      return { devices: grab('Devices'), sites: grab('Sites'), relationships: grab('Relationships') };
    });

    // Node drawer: opens, shows Data freshness from the SAME last-seen value.
    probe.drawer = { opened: false };
    if (probe.nodesRendered > 0) {
      await page.locator('[data-topology-node]').first().click();
      await page.waitForSelector('[data-topology-drawer="node"]', { timeout: 10000 });
      const drawerText = (await page.locator('[data-topology-drawer="node"]')
        .innerText()).replace(/\s+/g, ' ');
      probe.drawer = {
        opened: true,
        hasDataFreshness: /Data freshness/.test(drawerText),
        hasLastSeen: /Last seen/.test(drawerText),
        freshnessRendered: /(LIVE|RECENT|AGING|STALE|EXPIRED|NEVER COLLECTED|UNKNOWN)/.test(drawerText),
        freshnessConsistent: !(/NEVER COLLECTED/.test(drawerText) && /\bLIVE\b/.test(drawerText)),
        text: drawerText.slice(0, 400),
      };
      await page.locator('[data-topology-drawer="node"] button', { hasText: 'Close' }).click();
      await page.waitForTimeout(400);
      probe.drawer.closed = (await page.locator('[data-topology-drawer="node"]').count()) === 0;
    }

    const topoApi = apiCalls
      .filter(c => c.url.includes('/api/infrastructure/topology/'))
      .map(c => ({ url: c.url, status: c.status, at: apiCalls.indexOf(c) }));
    probe.topologyApi = topoApi;
    probe.summaryCalled = topoApi.some(c => c.url.includes('/topology/summary') && c.status === 200);
    probe.graphApiOk = topoApi.filter(c => c.url.includes('/topology/graph'))
      .every(c => c.status === 200);
  } catch (e) {
    errors.push(`topology probe: ${e.message}`);
  }
  await page.screenshot({ path: path.resolve(__dirname, '../layout-validation/p0-topology.png') });
  await ctx.close();
  return { route: '/infrastructure-topology', probe, errors, apiErrors: apiCalls.filter(c => c.status >= 500) };
}

async function verifySop(browser) {
  const { ctx, page, errors, apiCalls } = await open(browser, '/sop-engine', ['SOP']);
  const probe = {};
  try {
    await page.waitForFunction(
      () => Array.from(document.querySelectorAll('button')).some(b => b.innerText.trim() === 'View'),
      { timeout: 20000 },
    );
    probe.listLoaded = true;
    probe.sopCards = await page.locator('button', { hasText: 'View' }).count();

    await page.locator('button', { hasText: 'View' }).first().click();
    await page.waitForTimeout(3000);
    probe.detail = await page.evaluate(() => {
      const text = document.body.innerText;
      return {
        rendersSteps: document.querySelectorAll('ol li').length > 0,
        honestEmpty: /No procedure steps configured\./.test(text),
        loading: /Loading steps…/.test(text),
        staleMessageGone: !/Step details unavailable/.test(text),
        misleadingZeroGone: !/\b0 steps\b/.test(text),
      };
    });
  } catch (e) {
    errors.push(`sop probe: ${e.message}`);
  }
  await page.screenshot({ path: path.resolve(__dirname, '../layout-validation/p0-sop.png') });
  await ctx.close();
  return { route: '/sop-engine', probe, errors, apiErrors: apiCalls.filter(c => c.status >= 500) };
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const results = [];
  try {
    results.push(await verifyMonitoring(browser));
    results.push(await verifyTopology(browser));
    results.push(await verifySop(browser));
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(results, null, 2));
  const failed = results.some(r => r.errors.length > 0 || r.apiErrors.length > 0);
  process.exit(failed ? 1 : 0);
})().catch(e => { console.error('FATAL', e); process.exit(2); });
