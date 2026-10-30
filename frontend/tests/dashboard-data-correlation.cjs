/* Deep probe: prove rendered dashboard numbers match live API payloads.
 * node tests/dashboard-data-correlation.cjs
 */
const { chromium } = require('playwright');
const path = require('node:path');
process.loadEnvFile(path.resolve(__dirname, '../.env.local'));

const BASE = 'http://localhost:3000';

(async () => {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
  const page = await ctx.newPage();
  const payloads = {};
  const bad = [];

  page.on('response', async r => {
    const u = new URL(r.url());
    if (u.port !== '8000') return;
    if (r.status() >= 400) bad.push({ path: u.pathname, status: r.status() });
    if (u.pathname.includes('/auth/')) return;
    try { payloads[u.pathname] = await r.json(); } catch {}
  });

  try {
    await page.goto(`${BASE}/dashboard`, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForTimeout(5000);
    if (await page.locator('input[type=email]').count()) {
      await page.locator('input[type=email]').fill(process.env.NEXT_PUBLIC_AUTH_EMAIL);
      await page.locator('input[type=password]').fill(process.env.NEXT_PUBLIC_AUTH_PASSWORD);
      await page.locator('button[type=submit]').click();
      await page.waitForTimeout(7000);
    }
    await page.waitForFunction(() => {
      const el = document.querySelector('main');
      return el && el.innerText.trim().length > 1000;
    }, { timeout: 30000 }).catch(() => {});
    await page.waitForTimeout(8000);

    const result = await page.evaluate(() => {
      const main = document.querySelector('main');
      const text = (main ? main.innerText : '').replace(/\s+/g, ' ');
      // Locate every occurrence of "Loading" with surrounding context.
      const loadingContexts = [];
      const re = /loading/ig;
      let m;
      while ((m = re.exec(text)) !== null && loadingContexts.length < 15) {
        loadingContexts.push(text.slice(Math.max(0, m.index - 60), m.index + 60));
      }
      return { fullText: text, loadingContexts };
    });

    // Correlate live API payloads with rendered text.
    const p = payloads;
    const expectations = {
      openAlerts: { api: p['/api/alerts'] ? p['/api/alerts'].length : null, needle: null },
      incidents: { api: p['/api/incidents'] ? p['/api/incidents'].length : null, needle: null },
      sensors: { api: p['/api/monitoring/sensors'] ? p['/api/monitoring/sensors'].length : null, needle: null },
      devices: { api: p['/api/devices'] ? p['/api/devices'].length : null, needle: null },
      alertTotal: { api: p['/api/alerts/summary'] ? p['/api/alerts/summary'].total : null, needle: null },
    };
    const text = result.fullText;
    const correlation = {};
    for (const [k, v] of Object.entries(expectations)) {
      if (v.api === null || v.api === undefined) { correlation[k] = { api: null, rendered: 'no payload' }; continue; }
      correlation[k] = { api: v.api, rendered: text.includes(String(v.api)) };
    }

    console.log(JSON.stringify({
      correlation,
      alertSummary: p['/api/alerts/summary'],
      networkStatus: p['/api/network/status'],
      automation: p['/api/automation/status'] ? {
        sources_total: p['/api/automation/status'].sources_total,
        success_rate_pct: p['/api/automation/status'].success_rate_pct,
      } : null,
      badApiCalls: bad,
      loadingContexts: result.loadingContexts,
      textLength: text.length,
      textTail: text.slice(-1500),
    }, null, 2));
  } finally {
    await ctx.close();
    await browser.close();
  }
})().catch(e => { console.error('FATAL', e); process.exit(2); });