/* Phase 9.9 - SNMP monitoring-source onboarding, verified in a real browser.
 *
 *   node tests/snmp-source-onboarding.cjs
 *
 * Real browser, real login, real API, real Postgres. No mocks, no route
 * interception, no auth bypass. Verified:
 *   1. the SNMP onboarding surface renders on Infrastructure
 *   2. Add opens a form with Name / Host / Port / Version / credential
 *   3. the credential input is masked and a missing credential blocks the save
 *   4. saving a real (unreachable) target stores the source and probes it
 *   5. the readiness result is shown verbatim and is NEVER "unsupported" for a
 *      transport failure
 *   6. the typed credential is cleared after saving and never appears in the
 *      DOM, localStorage or sessionStorage
 *   7. the source is listed with "credential configured" and can be edited
 *   8. editing shows a MASKED stored credential (never the value) plus an
 *      explicit Replace-credential action
 *   9. the existing Collect action is present and accepts a click
 *  10. no page/console errors
 *
 * Exits non-zero when any check fails.
 */
const { chromium } = require('playwright');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

process.loadEnvFile(path.resolve(__dirname, '../.env.local'));

const UI = 'http://localhost:3000';
const CAPTURES = path.join(os.tmpdir(), 'hsdeep-topology-captures');
const SOURCE_NAME = 'p99-onboarding-sw';
const HOST = '127.0.0.1';
const PORT = '1161';
const COMMUNITY = 'p99-browser-community-2f91';
const ROTATED = 'p99-browser-community-rotated-77c4';

const results = [];
const pageErrors = [];
const consoleErrors = [];

function check(name, ok, detail) {
  results.push({ name, ok: Boolean(ok), detail: detail === undefined ? null : detail });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail === undefined ? '' : `  (${detail})`}`);
}

async function login(page) {
  await page.goto(`${UI}/dashboard`, { waitUntil: 'domcontentloaded', timeout: 90000 });
  await page.waitForTimeout(4000);
  if (await page.locator('input[type=email]').count()) {
    await page.locator('input[type=email]').fill(process.env.NEXT_PUBLIC_AUTH_EMAIL);
    await page.locator('input[type=password]').fill(process.env.NEXT_PUBLIC_AUTH_PASSWORD);
    await page.locator('button[type=submit]').click();
    await page.waitForURL('**/dashboard', { timeout: 60000 });
  }
  await page.waitForTimeout(3000);
}

/** Search the rendered page plus all client-side storage for a secret. */
async function secretAppearsAnywhere(page, secret) {
  return page.evaluate((needle) => {
    const haystacks = [document.documentElement.outerHTML];
    for (let i = 0; i < localStorage.length; i += 1) {
      haystacks.push(String(localStorage.getItem(localStorage.key(i)) ?? ''));
    }
    for (let i = 0; i < sessionStorage.length; i += 1) {
      haystacks.push(String(sessionStorage.getItem(sessionStorage.key(i)) ?? ''));
    }
    return haystacks.some((text) => text.includes(needle));
  }, secret);
}

async function main() {
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
  const page = await context.newPage();
  page.on('pageerror', (err) => pageErrors.push(String(err)));
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });

  try {
    await login(page);
    await page.goto(`${UI}/infrastructure`, { waitUntil: 'domcontentloaded', timeout: 90000 });
    await page.waitForTimeout(4000);

    // The first request to a freshly started dev server also compiles the route,
    // so this waits on attachment rather than visibility.
    await page.waitForSelector('[data-testid="snmp-onboarding"]', {
      timeout: 180000,
      state: 'attached',
    });
    const surface = page.locator('[data-testid="snmp-onboarding"]');
    await surface.scrollIntoViewIfNeeded().catch(() => {});
    check('SNMP onboarding surface renders', (await surface.count()) === 1);
    fs.mkdirSync(CAPTURES, { recursive: true });
    await page.screenshot({ path: path.join(CAPTURES, 'p99-01-onboarding-panel.png') });

    // --- 2/3. Open the form, masked credential, missing-credential guard ---
    // Decide add-vs-edit only once the panel's async source list has really
    // rendered: the placeholder also shows while the fetch is still in flight,
    // and creating a duplicate would hit the tenant's collector plan limit.
    const existing = page
      .locator('[data-testid^="snmp-source-row-"]')
      .filter({ hasText: SOURCE_NAME });
    let sourceExists = false;
    for (let attempt = 0; attempt < 30 && !sourceExists; attempt += 1) {
      sourceExists = (await existing.count()) > 0;
      if (!sourceExists) await page.waitForTimeout(5000);
    }
    check('existing harness source is detected in the panel list', sourceExists, `exists=${sourceExists}`);
    if (await existing.count()) {
      // A previous run left the harness source behind: edit it instead of
      // creating a duplicate (the operator surface has no delete action).
      await existing.first().locator('[data-testid^="snmp-edit-"]').click();
    } else {
      await page.locator('[data-testid="snmp-add"]').click();
    }
    await page.waitForSelector('[data-testid="snmp-source-form"]', { timeout: 15000 });

    const credentialInput = page.locator('[data-testid="snmp-credential"]');
    const credentialType = await credentialInput.getAttribute('type');
    check('credential field is masked', credentialType === 'password', `type=${credentialType}`);
    check(
      'SNMP fields are present',
      (await page.locator('[data-testid="snmp-name"]').count()) === 1 &&
        (await page.locator('[data-testid="snmp-host"]').count()) === 1 &&
        (await page.locator('[data-testid="snmp-port"]').count()) === 1 &&
        (await page.locator('[data-testid="snmp-version"]').count()) === 1,
    );

    await page.locator('[data-testid="snmp-name"]').fill(SOURCE_NAME);
    await page.locator('[data-testid="snmp-host"]').fill(HOST);
    await page.locator('[data-testid="snmp-port"]').fill(PORT);
    await page.locator('[data-testid="snmp-version"]').selectOption('v2c');

    if (await credentialInput.count()) {
      await page.locator('[data-testid="snmp-save"]').click();
      await page.waitForSelector('[data-testid="snmp-error"]', { timeout: 15000 });
      const guard = await page.locator('[data-testid="snmp-error"]').innerText();
      check('saving without a credential is blocked', /required/i.test(guard), guard.slice(0, 60));
    }


    // --- 4/5. Save an unreachable target; readiness must not lie -----------
    if (await credentialInput.count()) {
      await credentialInput.fill(COMMUNITY);
    }
    await page.locator('[data-testid="snmp-save"]').click();
    // On a slow collector host the create + probe can take a couple of minutes;
    // the PRODUCT's own budget is bounded, so either a result or an error will
    // have been rendered by then.
    await page.waitForSelector('[data-testid="snmp-readiness"], [data-testid="snmp-error"]', {
      timeout: 300000,
    });
    const outcome = await page.evaluate(() => ({
      readiness: document.querySelector('[data-testid="snmp-readiness"]')?.textContent ?? null,
      error: document.querySelector('[data-testid="snmp-error"]')?.textContent ?? null,
    }));
    check(
      'a readiness result is displayed after saving',
      Boolean(outcome.readiness),
      outcome.readiness ? outcome.readiness.slice(0, 80) : (outcome.error ?? 'nothing rendered'),
    );
    const readiness = outcome.readiness ?? '';
    check(
      'readiness is reported verbatim',
      /Readiness: \S+ \(stage \S+\)/.test(readiness),
      readiness.slice(0, 90),
    );
    const statusPart = readiness.split('—')[0];
    check(
      'a transport failure is never reported as unsupported',
      !/unsupported/i.test(statusPart) && !/ready/i.test(statusPart),
      statusPart.trim().slice(0, 60),
    );
    await page.screenshot({ path: path.join(CAPTURES, 'p99-02-readiness-result.png') });

    // --- 6. Secret hygiene ------------------------------------------------
    check(
      'typed credential cleared from the form after save',
      (await page.locator('[data-testid="snmp-credential"]').count()) === 0,
    );
    check(
      'typed credential never reaches the DOM or browser storage',
      !(await secretAppearsAnywhere(page, COMMUNITY)),
    );

    // --- 7. Listed with a configured credential ---------------------------
    const row = page
      .locator('[data-testid^="snmp-source-row-"]')
      .filter({ hasText: SOURCE_NAME })
      .first();
    // The list refreshes after the save, and each API call can take tens of
    // seconds on a slow collector host.
    await row.waitFor({ state: 'attached', timeout: 300000 });
    await row.scrollIntoViewIfNeeded().catch(() => {});
    const rowText = await row.innerText();
    check('source appears in the SNMP source list', rowText.includes(SOURCE_NAME), rowText.slice(0, 70));
    check('credential is reported as configured', /credential configured/i.test(rowText), rowText.slice(0, 70));

    // --- 8. Edit shows a MASKED credential + explicit replace -------------
    await row.locator('[data-testid^="snmp-edit-"]').click();
    await page.waitForSelector('[data-testid="snmp-source-form"]', { timeout: 15000 });
    const masked = page.locator('[data-testid="snmp-credential-stored"]');
    check(
      'stored credential is displayed as masked text only',
      (await masked.count()) === 1,
      (await masked.innerText().catch(() => '')).slice(0, 40),
    );
    check(
      'replace-credential action is offered',
      (await page.locator('[data-testid="snmp-replace-credential"]').count()) === 1,
    );
    check('stored credential value is not in the DOM', !(await secretAppearsAnywhere(page, COMMUNITY)));
    await page.screenshot({ path: path.join(CAPTURES, 'p99-03-edit-masked-credential.png') });

    await page.locator('[data-testid="snmp-replace-credential"]').click();
    await page.locator('[data-testid="snmp-credential"]').fill(ROTATED);
    await page.locator('[data-testid="snmp-save"]').click();
    await page.waitForSelector('[data-testid="snmp-readiness"], [data-testid="snmp-error"]', {
      timeout: 300000,
    });
    check(
      'rotated credential never reaches the DOM or storage',
      !(await secretAppearsAnywhere(page, ROTATED)),
    );
    check(
      'replaced credential is cleared from the form',
      (await page.locator('[data-testid="snmp-credential"]').count()) === 0,
    );

    // --- 9. The existing Collect action is still available ----------------
    const collectButton = page.locator('button[title^="Run a real collection cycle"]').first();
    await collectButton.scrollIntoViewIfNeeded().catch(() => {});
    const collectVisible = (await collectButton.count()) === 1;
    check('collect (sync) action is available for the source', collectVisible);
    if (collectVisible) {
      await collectButton.click();
      await page.waitForTimeout(3000);
      check('collect action accepted the click without a page error', pageErrors.length === 0);
    }

    // --- 10. No page/console errors ---------------------------------------
    const realConsoleErrors = consoleErrors.filter(
      (text) => !/favicon|ResizeObserver|React DevTools/i.test(text),
    );
    check('no page errors', pageErrors.length === 0, pageErrors.slice(0, 1).join(' | '));
    check('no console errors', realConsoleErrors.length === 0, realConsoleErrors.slice(0, 1).join(' | '));
    await page.screenshot({ path: path.join(CAPTURES, 'p99-04-source-managed.png') });
  } finally {
    await browser.close();
  }

  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
  console.log(`screenshots: ${CAPTURES}`);
  if (failed.length) {
    console.log('FAILURES:');
    failed.forEach((f) => console.log(`  - ${f.name}${f.detail ? `: ${f.detail}` : ''}`));
    process.exit(1);
  }
}

main().catch((err) => {
  console.error('HARNESS ERROR:', err);
  process.exit(1);
});
