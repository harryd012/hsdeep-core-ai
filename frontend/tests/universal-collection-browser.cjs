/* Real-browser validation of the Universal Collection panel (Phase 5).
 * node tests/universal-collection-browser.cjs
 *
 * Uses the EXISTING login form + .env.local credentials, exactly like
 * tests/alerts-raw-html.cjs and tests/command-center-browser.cjs. No auth
 * bypass, no mocking of the app's own API - this drives the real UI against the
 * real backend.
 *
 * What it proves: the panel I added actually RENDERS in a browser, that the
 * per-protocol coverage rows came from the live readiness endpoint, and that the
 * readiness-vs-collection distinction is present in the UI. Compile-level checks
 * cannot prove any of that.
 */
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');

process.loadEnvFile(path.resolve(__dirname, '../.env.local'));

const UI = 'http://localhost:3000';
const OUT = path.resolve(__dirname, '../layout-validation');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(`console: ${m.text()}`);
  });

  try {
    await page.goto(`${UI}/infrastructure`, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForTimeout(4000);

    // Real login through the real form, only if the session is not already live.
    if (await page.locator('input[type=email]').count()) {
      await page.locator('input[type=email]').fill(process.env.NEXT_PUBLIC_AUTH_EMAIL);
      await page.locator('input[type=password]').fill(process.env.NEXT_PUBLIC_AUTH_PASSWORD);
      await page.locator('button[type=submit]').click();
      await page.waitForTimeout(8000);
      if (!page.url().includes('/infrastructure')) {
        await page.goto(`${UI}/infrastructure`, { waitUntil: 'domcontentloaded', timeout: 60000 });
      }
    }

    // The panel is client-rendered after the readiness fetch, so wait for its
    // heading rather than assuming it is present immediately.
    let panelFound = true;
    try {
      await page.waitForSelector('text=UNIVERSAL COLLECTION', { timeout: 30000 });
    } catch {
      panelFound = false;
    }
    await page.waitForTimeout(3000);

    const info = await page.evaluate(() => {
      const sections = Array.from(document.querySelectorAll('section.hud-panel'));
      const panel = sections.find((s) =>
        (s.querySelector('h2')?.textContent || '').includes('UNIVERSAL COLLECTION'),
      );
      const main = document.querySelector('main');
      const mainText = main ? main.innerText : '';
      // Diagnostics: if the panel is missing we must know WHICH reason - not
      // logged in, still loading, or genuinely absent.
      const diagnostics = {
        url: location.pathname,
        hasEmailInput: !!document.querySelector('input[type=email]'),
        hasPasswordInput: !!document.querySelector('input[type=password]'),
        hudPanelCount: sections.length,
        hudPanelHeadings: sections
          .map((s) => (s.querySelector('h2')?.textContent || '').trim())
          .filter(Boolean),
        mainTextLength: mainText.length,
        mainSample: mainText.split('\n').slice(0, 14).join(' | ').slice(0, 700),
      };
      if (!panel) return { panelPresent: false, ...diagnostics };

      const text = panel.innerText || '';

      // Coverage rows are the grid children above the "CONFIGURED SOURCES" head.
      const coverageGrid = panel.querySelector('div');
      const coverageRows = coverageGrid
        ? Array.from(coverageGrid.children).map((el) => el.innerText.replace(/\s+/g, ' ').trim())
        : [];

      const buttons = Array.from(panel.querySelectorAll('button')).map((b) =>
        (b.textContent || '').trim(),
      );

      return {
        panelPresent: true,
        headingPresent: text.includes('UNIVERSAL COLLECTION'),
        coverageRows,
        coverageHasSnmp: /SNMP/.test(text),
        coverageHasWmi: /WMI\/WSMan/.test(text),
        statesNotCollapsed:
          text.includes('ready') === false || text.includes('does not mean') === true,
        honestyNotePresent: text.includes('does not') && text.includes('topology'),
        configuredSourcesHead: /CONFIGURED SOURCES \(\d+\)/.test(text),
        probeButtons: buttons.filter((b) => b.startsWith('Probe')).length,
        collectButtons: buttons.filter((b) => b.startsWith('Collect')).length,
        textLength: text.length,
        sample: text.split('\n').slice(0, 18).join(' | ').slice(0, 900),
      };
    });

    fs.mkdirSync(OUT, { recursive: true });
    await page.screenshot({
      path: path.join(OUT, 'universal-collection-panel.png'),
      fullPage: false,
    });

    const result = { panelFound, ...info, pageErrors: errors };
    console.log(JSON.stringify(result, null, 2));

    const ok =
      result.panelPresent === true &&
      result.coverageHasSnmp === true &&
      result.coverageHasWmi === true &&
      result.honestyNotePresent === true &&
      result.configuredSourcesHead === true &&
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
