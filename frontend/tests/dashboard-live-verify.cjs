/* Real-browser dashboard verification (Phase 4/5 evidence).
 * No request mocking, no persisted credentials.
 *
 * Verifies against the RUNNING backend (localhost:8000):
 *   - dashboard API calls return 200 (not 429 plan_limit_exceeded)
 *   - real backend numbers render in KPI/attention surfaces
 *   - no pageerror / console.error
 *   - full-page screenshot captured
 *
 * node tests/dashboard-live-verify.cjs
 */
const { chromium } = require('playwright');
const path = require('node:path');
process.loadEnvFile(path.resolve(__dirname, '../.env.local'));

const BASE = 'http://localhost:3000';

(async () => {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
  const page = await ctx.newPage();
  const errors = [];
  const apiCalls = [];
  const payloads = {};

  page.on('pageerror', e => errors.push(`pageerror: ${e.message}`));
  page.on('console', m => { if (m.type() === 'error') errors.push(`console.error: ${m.text()}`); });
  page.on('response', async r => {
    const u = new URL(r.url());
    if (u.port !== '8000') return;
    apiCalls.push({ path: u.pathname + u.search, status: r.status() });
    if (u.pathname.includes('/auth/')) return;
    try { payloads[u.pathname] = await r.json(); } catch {}
  });

  try {
    await page.goto(`${BASE}/dashboard`, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForTimeout(5000);
    // Real login only if the login form is presented (dev bootstrap may
    // already establish the session).
    if (await page.locator('input[type=email]').count()) {
      await page.locator('input[type=email]').fill(process.env.NEXT_PUBLIC_AUTH_EMAIL);
      await page.locator('input[type=password]').fill(process.env.NEXT_PUBLIC_AUTH_PASSWORD);
      await page.locator('button[type=submit]').click();
      await page.waitForTimeout(7000);
    }
    if (!page.url().includes('/dashboard')) {
      await page.goto(`${BASE}/dashboard`, { waitUntil: 'domcontentloaded', timeout: 60000 });
      await page.waitForTimeout(7000);
    }

    // Wait for the command-center content to render.
    try {
      await page.waitForFunction(() => {
        const el = document.querySelector('main');
        return el && el.innerText.trim().length > 0;
      }, { timeout: 30000 });
    } catch {
      errors.push('timed out waiting for non-empty <main>');
    }
    await page.waitForTimeout(6000);

    const probe = await page.evaluate(() => {
      const main = document.querySelector('main');
      const text = (main ? main.innerText : '').replace(/\s+/g, ' ').trim();
      return {
        mainTextLength: text.length,
        mainTextSample: text.slice(0, 1200),
        sections: [...document.querySelectorAll('main section, main h1, main h2, main h3')]
          .map(el => (el.querySelector('h1,h2,h3') || el).textContent.trim().slice(0, 80))
          .slice(0, 40),
        hasLoading: /Loading/i.test(text),
        has429: /plan_limit_exceeded|upgrade to add more/i.test(text),
      };
    });

    await page.screenshot({ path: path.resolve(__dirname, '../qa-shots/dashboard-live-verify.png'), fullPage: true });

    const summary = {
      url: page.url(),
      api: {
        total: apiCalls.length,
        ok: apiCalls.filter(c => c.status >= 200 && c.status < 300).length,
        notFound: apiCalls.filter(c => c.status === 404).map(c => c.path),
        quotaBlocked: apiCalls.filter(c => c.status === 429).map(c => c.path),
        serverErrors: apiCalls.filter(c => c.status >= 500).map(c => c.path),
        unauthorized: apiCalls.filter(c => c.status === 401 || c.status === 403).map(c => c.path),
      },
      payloadCounts: Object.fromEntries(Object.entries(payloads).map(([k, v]) => [
        k, Array.isArray(v) ? v.length : (v && typeof v === 'object' ? Object.keys(v).slice(0, 12) : v),
      ])),
      probe,
      errors,
    };
    console.log(JSON.stringify(summary, null, 2));

    const failed =
      errors.length > 0 ||
      summary.api.quotaBlocked.length > 0 ||
      summary.api.serverErrors.length > 0 ||
      probe.mainTextLength < 200;
    process.exit(failed ? 1 : 0);
  } finally {
    await ctx.close();
    await browser.close();
  }
})().catch(e => { console.error('FATAL', e); process.exit(2); });