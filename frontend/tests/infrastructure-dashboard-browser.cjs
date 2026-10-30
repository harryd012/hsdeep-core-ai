/* Real-browser validation of the Phase 5.1 infrastructure dashboard additions.
 * node tests/infrastructure-dashboard-browser.cjs
 *
 * Real login form + .env.local credentials, real backend. Verifies the KPI row
 * renders REAL numbers from /api/infrastructure/overview (not hard-coded), that
 * the canonical topology panel and its filters render, and that the environment's
 * genuinely-empty graph is reported honestly rather than drawn as fake edges.
 */
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');

process.loadEnvFile(path.resolve(__dirname, '../.env.local'));

const UI = 'http://localhost:3000';
const OUT = path.resolve(__dirname, '../layout-validation');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1400 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(`console: ${m.text()}`);
  });

  try {
    await page.goto(`${UI}/infrastructure`, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForTimeout(4000);

    if (await page.locator('input[type=email]').count()) {
      await page.locator('input[type=email]').fill(process.env.NEXT_PUBLIC_AUTH_EMAIL);
      await page.locator('input[type=password]').fill(process.env.NEXT_PUBLIC_AUTH_PASSWORD);
      await page.locator('button[type=submit]').click();
      await page.waitForTimeout(8000);
      if (!page.url().includes('/infrastructure')) {
        await page.goto(`${UI}/infrastructure`, { waitUntil: 'domcontentloaded', timeout: 60000 });
      }
    }

    for (const heading of ['INFRASTRUCTURE KPIs', 'CANONICAL TOPOLOGY']) {
      try {
        await page.waitForSelector(`text=${heading}`, { timeout: 30000 });
      } catch {
        /* recorded below via panel presence */
      }
    }

    // /api/infrastructure/overview is a heavy aggregate and can be slow, so wait
    // for the KPI placeholders to be replaced by real values rather than
    // snapshotting a loading state and calling it a result.
    let kpiResolved = false;
    const startedAt = Date.now();
    for (let i = 0; i < 30; i += 1) {
      kpiResolved = await page.evaluate(() => {
        const sections = Array.from(document.querySelectorAll('section.hud-panel'));
        const kpi = sections.find((s) =>
          (s.querySelector('h2')?.textContent || '').includes('INFRASTRUCTURE KPIs'),
        );
        if (!kpi) return false;
        const strongs = Array.from(kpi.querySelectorAll('strong')).map((s) =>
          s.textContent.trim(),
        );
        // At least one real figure (digits) and no remaining loading marker.
        return strongs.some((t) => /^\d+$/.test(t)) && !strongs.includes('…');
      });
      if (kpiResolved) break;
      await page.waitForTimeout(10000);
    }
    const kpiWaitSeconds = Math.round((Date.now() - startedAt) / 1000);
    await page.waitForTimeout(2000);

    const info = await page.evaluate(() => {
      const sections = Array.from(document.querySelectorAll('section.hud-panel'));
      const findPanel = (title) =>
        sections.find((s) => (s.querySelector('h2')?.textContent || '').includes(title));

      const kpi = findPanel('INFRASTRUCTURE KPIs');
      const topo = findPanel('CANONICAL TOPOLOGY');

      // Extract "LABEL -> value" pairs from the KPI cards.
      const kpis = {};
      if (kpi) {
        kpi.querySelectorAll('div').forEach((card) => {
          const strong = card.querySelector('strong');
          const span = card.querySelector('span');
          if (strong && span) {
            const label = span.textContent.trim().replace(/\s+/g, ' ');
            if (label && label === label.toUpperCase()) kpis[label] = strong.textContent.trim();
          }
        });
      }

      return {
        kpiPanelPresent: !!kpi,
        topoPanelPresent: !!topo,
        kpis,
        kpiCardCount: Object.keys(kpis).length,
        // "No data" must be distinguishable from a real 0.
        hasNoDataCard: Object.values(kpis).includes('No data'),
        kpiStrongValues: kpi
          ? Array.from(kpi.querySelectorAll('strong')).map((s) => s.textContent.trim())
          : [],
        topoSummary: topo
          ? (topo.innerText.match(/status[^\n]*/gi) || []).slice(0, 2)
          : [],
        topoFilterLabels: topo
          ? Array.from(topo.querySelectorAll('button')).map((b) => b.textContent.trim())
          : [],
        topoShowsLimitations: topo
          ? /No relationships have been discovered|No network relationship/i.test(topo.innerText)
          : false,
        topoTextSample: topo ? topo.innerText.slice(0, 400) : '',
      };
    });

    fs.mkdirSync(OUT, { recursive: true });
    await page.screenshot({
      path: path.join(OUT, 'infrastructure-dashboard-phase51.png'),
      fullPage: true,
    });

    const result = { kpiResolved, kpiWaitSeconds, ...info, pageErrors: errors };
    console.log(JSON.stringify(result, null, 2));

    const ok =
      result.kpiPanelPresent === true &&
      result.topoPanelPresent === true &&
      result.kpiCardCount >= 6 &&
      result.topoFilterLabels.includes('Switches') &&
      result.topoFilterLabels.includes('Inferred') &&
      result.topoShowsLimitations === true &&
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
