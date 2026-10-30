/* Browser validation: the portal's Usage page must show REAL API-request usage
 * (the meter had no callers, so it always read 0 before this change).
 * node tests/api-quota-browser.cjs
 */
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');

process.loadEnvFile(path.resolve(__dirname, '../.env.local'));
const UI = 'http://localhost:3000';
const OUT = path.resolve(__dirname, '../layout-validation');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1600, height: 1200 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(`console: ${m.text()}`);
  });

  try {
    await page.goto(`${UI}/usage`, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForTimeout(4000);
    if (await page.locator('input[type=email]').count()) {
      await page.locator('input[type=email]').fill(process.env.NEXT_PUBLIC_AUTH_EMAIL);
      await page.locator('input[type=password]').fill(process.env.NEXT_PUBLIC_AUTH_PASSWORD);
      await page.locator('button[type=submit]').click();
      await page.waitForTimeout(8000);
      await page.goto(`${UI}/usage`, { waitUntil: 'domcontentloaded', timeout: 60000 });
    }
    await page.waitForTimeout(12000);

    const info = await page.evaluate(() => {
      const body = document.body.innerText;
      // Target the ConsumptionTracker tile ("API Calls"), NOT the static plan
      // comparison table (which lists marketing allowances, not tenant usage).
      const idx = body.search(/API Calls/i);
      const tile = idx === -1 ? null : body.slice(idx, idx + 80).replace(/\s+/g, ' ').trim();
      const nums = tile ? (tile.match(/(\d[\d,]*)/g) || []) : [];
      return {
        url: location.pathname,
        apiCallsTile: tile,
        apiCallsCurrent: nums.length ? Number(nums[0].replace(/,/g, '')) : null,
        loadsError: /Failed to load usage data/i.test(body),
        mentionsUpgrade: /upgrade/i.test(body),
        leaksConnectionString: /redis:\/\/|postgresql:\/\//i.test(body),
      };
    });

    fs.mkdirSync(OUT, { recursive: true });
    await page.screenshot({ path: path.join(OUT, 'api-quota-usage.png'), fullPage: true });

    const result = { ...info, pageErrors: errors };
    console.log(JSON.stringify(result, null, 2));

    const ok =
      // Real metered usage must be visible in the portal (it read 0 before).
      Number.isFinite(result.apiCallsCurrent) &&
      result.apiCallsCurrent > 0 &&
      result.loadsError === false &&
      result.leaksConnectionString === false &&
      errors.length === 0;
    console.log(ok ? 'BROWSER VALIDATION: PASS' : 'BROWSER VALIDATION: FAIL');
    if (!ok) process.exitCode = 1;
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error('ERROR:', e.message);
  process.exitCode = 1;
});
