/* PHASE 6.3B browser validation: /admin/customers must render the REAL tenant
 * list for a platform admin instead of the permanent "access required" state.
 * node tests/admin-customers-browser.cjs
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
    await page.goto(`${UI}/admin/customers`, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForTimeout(4000);
    if (await page.locator('input[type=email]').count()) {
      await page.locator('input[type=email]').fill(process.env.NEXT_PUBLIC_AUTH_EMAIL);
      await page.locator('input[type=password]').fill(process.env.NEXT_PUBLIC_AUTH_PASSWORD);
      await page.locator('button[type=submit]').click();
      await page.waitForTimeout(8000);
      await page.goto(`${UI}/admin/customers`, { waitUntil: 'domcontentloaded', timeout: 60000 });
    }
    await page.waitForTimeout(9000);

    const info = await page.evaluate(() => {
      const body = document.body.innerText;
      const rows = document.querySelectorAll('table tbody tr').length;
      const headers = Array.from(document.querySelectorAll('table thead th')).map((t) =>
        t.textContent.trim(),
      );
      return {
        url: location.pathname,
        accessRequired: /Platform-admin access required/i.test(body),
        tenantCountLine: (body.match(/\d+ of \d+ tenants/) || [null])[0],
        rows,
        headers,
        hasTenantIdColumn: /Tenant ID/i.test(body),
        // No fabricated values: unsupported columns must read "No data".
        noDataCount: (body.match(/No data/g) || []).length,
        leaksSecret: /password|community|api[_-]?key|secret/i.test(body),
      };
    });

    // Open the first tenant row to exercise the detail panel.
    if (info.rows > 0) {
      await page.locator('table tbody tr').first().click();
      await page.waitForTimeout(1500);
    }
    const detail = await page.evaluate(() => {
      const body = document.body.innerText;
      return {
        detailOpen: /TENANT ·/.test(body),
        detailFields: (body.match(/Tenant name|Tenant ID|Slug|Plan tier|Subscription status/g) || [])
          .length,
      };
    });

    fs.mkdirSync(OUT, { recursive: true });
    await page.screenshot({ path: path.join(OUT, 'admin-customers.png'), fullPage: true });

    const result = { ...info, ...detail, pageErrors: errors };
    console.log(JSON.stringify(result, null, 2));

    const ok =
      result.accessRequired === false &&
      result.rows > 0 &&
      result.hasTenantIdColumn &&
      result.detailOpen &&
      result.leaksSecret === false &&
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
