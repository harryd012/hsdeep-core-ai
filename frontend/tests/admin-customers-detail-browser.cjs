/* PHASE 6.3C browser validation: the detail panel must load REAL data for the
 * selected tenant via GET /api/admin/tenants/{id}, switch correctly between TWO
 * tenants, and never show stale data or secrets.
 * node tests/admin-customers-detail-browser.cjs
 */
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');

process.loadEnvFile(path.resolve(__dirname, '../.env.local'));
const UI = 'http://localhost:3000';
const OUT = path.resolve(__dirname, '../layout-validation');

const SECTION = (body, name) => body.includes(name);

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1700, height: 1300 } });
  const errors = [];
  const detailCalls = [];
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(`console: ${m.text()}`);
  });
  page.on('request', (r) => {
    if (/\/api\/admin\/tenants\//.test(r.url())) detailCalls.push(r.url());
  });

  const readPanel = () =>
    page.evaluate(() => {
      const panel = Array.from(document.querySelectorAll('section.hud-panel')).find((s) =>
        /^TENANT ·/.test((s.querySelector('h2')?.textContent || '').trim()),
      );
      const body = panel ? panel.innerText : '';
      return {
        open: !!panel,
        body,
        num: (re) => {
          const m = body.match(re);
          return m ? Number(m[1].replace(/,/g, '')) : null;
        },
      };
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
    await page.waitForTimeout(6000);

    const rows = await page.locator('table tbody tr').count();
    if (rows < 1) throw new Error(`no tenant rows rendered (rows=${rows})`);

    // --- tenant 1 ---
    const firstRowText = await page.locator('table tbody tr').first().innerText();
    await page.locator('table tbody tr').first().click();
    await page.waitForTimeout(20000); // entitlements computation is slow
    const one = await readPanel();

    // --- tenant 2 (also proves no stale data) ---
    await page.locator('table tbody tr').nth(1).click();
    await page.waitForTimeout(20000);
    const two = await readPanel();

    fs.mkdirSync(OUT, { recursive: true });
    await page.screenshot({ path: path.join(OUT, 'admin-tenant-detail.png'), fullPage: true });

    const result = {
      rows,
      firstName: firstRowText.split('\n')[0],
      detailRequests: detailCalls.length,
      tenant1: {
        open: one.open,
        sections: [
          "TENANT PROFILE",
          "SUBSCRIPTION",
          "USAGE",
          "COUNTS",
          "USERS",
          "DISCOVERY SCOPES",
          "CUSTOM PROPERTIES",
          "ROLES & PERMISSIONS",
        ].filter((s) => SECTION(one.body, s)),
        hasNoData: /No data/.test(one.body),
        hasInvalidDate: /Invalid Date/.test(one.body),
        leaksSecret: /password|community|api[_-]?key|secret|token/i.test(one.body),
        emptyRoles: /No roles configured/.test(one.body),
        hasMutationControls:
          /Add role|Edit role|Delete role|Assign|Revoke|Save changes|New role/i.test(one.body),
        // Phase 6.3F: the rename control must render for the open tenant.
        hasRenameControl: /Rename tenant/.test(one.body),
        firstLine: one.body.split('\n')[0],
      },
      tenant2: {
        open: two.open,
        sections: [
          "TENANT PROFILE",
          "SUBSCRIPTION",
          "USAGE",
          "COUNTS",
          "USERS",
          "DISCOVERY SCOPES",
          "CUSTOM PROPERTIES",
          "ROLES & PERMISSIONS",
        ].filter((s) => SECTION(two.body, s)),
        firstLine: two.body.split('\n')[0],
      },
      switched: one.body.split('\n')[0] !== two.body.split('\n')[0],
      staleLeak: two.body.includes(one.body.split('\n')[0].replace('TENANT · ', '')),
      pageErrors: errors,
    };
    console.log(JSON.stringify(result, null, 2));

    const ok =
      result.tenant1.open &&
      result.tenant1.sections.length === 8 &&
      result.tenant1.hasInvalidDate === false &&
      result.tenant1.leaksSecret === false &&
      // Read-only RBAC: no mutation controls may be rendered.
      result.tenant1.hasMutationControls === false &&
      result.tenant1.hasRenameControl === true &&
      result.tenant2.open &&
      result.detailRequests >= 2 &&
      result.switched &&
      result.staleLeak === false &&
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
