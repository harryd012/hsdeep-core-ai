"use strict";
/* Real-browser validation of the Network Intelligence surfaces.
 * node tests/network-intelligence-validation.cjs [happy|failure]
 *
 * Reuses the EXISTING login form + .env.local credentials, exactly like
 * tests/command-center-browser.cjs. No auth bypass; no backend changes.
 */
const L = require('./_ni_lib.cjs');
const { chromium, path, OUT, NI_KEY, NI_PATH, UI, report, note, dump, login, readTiles, readKv } = L;

const MODE = process.argv[2] === 'failure' ? 'failure' : 'happy';

async function runHappy(page) {
  // ------------------------------------------ PHASE 1: READY (no data yet)
  await page.evaluate((k) => window.localStorage.removeItem(k), NI_KEY);
  await page.goto(`${UI}/infrastructure`, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(7000);

  note('infrastructure_ready', 'state', {
    panelVisible: await page.locator('.ni-panel').isVisible().catch(() => false),
    title: await page.locator('#ni-title').innerText().catch(() => null),
    subtitle: await page.locator('.ni-header__sub').innerText().catch(() => null),
    scenarioSelect: await page.locator('#ni-scenario').count(),
    runButton: await page.locator('.ni-button').innerText().catch(() => null),
    flowSteps: await page.locator('.ni-flow__step').count(),
    emptyState: await page.locator('.ni-empty').innerText().catch(() => null),
    statusTiles: await page.locator('.ni-status__tile').count(),
    auditRows: await page.locator('.ni-audit__row').count(),
  });
  await page.screenshot({ path: path.join(OUT, 'ni-1-infra-ready.png'), fullPage: true });

  // ------------------------------------ PHASE 2: run the combined scenario
  await page.locator('#ni-scenario').selectOption('combined');
  await page.locator('.ni-button').click();
  const loading = {
    label: await page.locator('.ni-button').innerText().catch(() => null),
    disabled: await page.locator('.ni-button').isDisabled().catch(() => null),
  };
  await page.waitForTimeout(200);
  loading.labelAfter = await page.locator('.ni-button').innerText().catch(() => null);
  loading.disabledAfter = await page.locator('.ni-button').isDisabled().catch(() => null);
  note('evaluating', 'state', loading);

  await page.waitForSelector('.ni-status__tile', { timeout: 45000 });
  await page.waitForFunction(
    () => !document.querySelector('.ni-button')?.hasAttribute('disabled'),
    { timeout: 45000 },
  );
  await page.waitForTimeout(1500);

  note('combined', 'statusTiles', await readTiles(page, '.ni-status__tile'));
  note('combined', 'sdwanKv', await readKv(page, 0));
  note('combined', 'sasePipeline', await page.evaluate(() =>
    [...document.querySelectorAll('.ni-pipeline__row')].map((r) => ({
      stage: r.querySelector('.ni-pipeline__stage')?.textContent?.trim(),
      decision: r.querySelector('.ni-pipeline__decision')?.textContent?.trim(),
    }))));
  note('combined', 'finalDecision', await page.locator('.ni-final strong').innerText().catch(() => null));
  note('combined', 'whyBlocked', await page.locator('.ni-why p').first().innerText().catch(() => null));
  note('combined', 'networkDecision', await page.locator('.ni-decision__body').innerText().catch(() => null));
  note('combined', 'recommendationKv', await readKv(page, 2));
  note('combined', 'recommendationTitle', await page.locator('.ni-rec__title').innerText().catch(() => null));
  note('combined', 'safetyFooter', await page.locator('.ni-safety').innerText().catch(() => null));
  note('combined', 'auditCount', await page.locator('.ni-audit__row').count());
  note('combined', 'auditRows', await page.evaluate(() =>
    [...document.querySelectorAll('.ni-audit__row')].map((r) => [
      r.querySelector('.ni-audit__engine')?.textContent?.trim(),
      r.querySelector('.ni-audit__stage')?.textContent?.trim(),
      r.querySelector('.ni-audit__decision')?.textContent?.trim(),
    ])));
  note('combined', 'flowContext', await readKv(page, 3));

  await page.locator('.ni-toggle').click();
  await page.waitForTimeout(400);
  note('combined', 'auditFirstMeta', await page.locator('.ni-audit__meta').first().innerText().catch(() => null));
  await page.locator('.ni-toggle').click();

  await page.screenshot({ path: path.join(OUT, 'ni-2-infra-combined.png'), fullPage: true });
}

async function layout(page) {
  const widths = [];
  for (const w of [1920, 1600, 1440, 1366, 1024, 820]) {
    await page.setViewportSize({ width: w, height: 1080 });
    await page.waitForTimeout(700);
    widths.push(await page.evaluate(() => {
      const grid = document.querySelector('.ni-grid');
      return {
        viewport: window.innerWidth,
        docScrollWidth: document.documentElement.scrollWidth,
        gridWidth: grid ? Math.round(grid.getBoundingClientRect().width) : null,
        gridColumns: grid ? getComputedStyle(grid).gridTemplateColumns : null,
        cards: [...document.querySelectorAll('.ni-grid > .ni-card')].map((el) => {
          const r = el.getBoundingClientRect();
          return {
            x: Math.round(r.x),
            width: Math.round(r.width),
            wide: el.classList.contains('ni-card--wide'),
          };
        }),
      };
    }));
  }
  note('layout', 'breakpoints', widths);
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.waitForTimeout(600);
}


async function dashboardAndBack(page) {
  await page.goto(`${UI}/dashboard`, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(9000);

  note('dashboard_widget', 'state', {
    visible: await page.locator('.ni-widget').isVisible().catch(() => false),
    title: await page.locator('#ni-widget-title').innerText().catch(() => null),
    subtitle: await page.locator('.ni-widget__sub').innerText().catch(() => null),
    tiles: await readTiles(page, '.ni-widget__tile'),
    meta: await page.evaluate(() =>
      [...document.querySelectorAll('.ni-widget__meta')].map((m) => m.textContent.trim())),
    foot: await page.locator('.ni-widget__foot').innerText().catch(() => null),
    readyShown: await page.locator('.ni-widget__ready').count(),
    linkText: await page.locator('.ni-widget__link').innerText().catch(() => null),
    linkHref: await page.locator('.ni-widget__link').getAttribute('href').catch(() => null),
  });
  await page.screenshot({ path: path.join(OUT, 'ni-4-dashboard-widget.png'), fullPage: true });

  await page.locator('.ni-widget__link').click();
  await page.waitForURL('**/infrastructure', { timeout: 45000 });
  await page.waitForTimeout(6500);
  note('cross_page_back', 'state', {
    url: page.url(),
    tiles: await readTiles(page, '.ni-status__tile'),
    auditCount: await page.locator('.ni-audit__row').count(),
  });
}

/** Simulate NI endpoint conditions WITHOUT touching the backend. */
async function runFailure(page) {
  const cases = [
    { name: 'unavailable_503', status: 503, body: '{"detail":"down"}' },
    { name: 'service_error_500', status: 500, body: '{"detail":"boom"}' },
    { name: 'access_denied_403', status: 403, body: '{"detail":"forbidden"}' },
    { name: 'unauthorized_401', status: 401, body: '{"detail":"no auth"}' },
    { name: 'not_found_404', status: 404, body: '{"detail":"nope"}' },
    { name: 'malformed_200', status: 200, body: '{"unexpected":true}' },
  ];

  for (const c of cases) {
    const route = (r) =>
      r.fulfill({ status: c.status, contentType: 'application/json', body: c.body });
    await page.route(`**${NI_PATH}/scenarios/**`, route);
    await page.route(`**${NI_PATH}/scenarios`, route);
    await page.route(`**${NI_PATH}/evaluate`, route);

    await page.goto(`${UI}/infrastructure`, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForTimeout(6000);
    await page.locator('.ni-button').click();
    await page.waitForTimeout(6000);

    note('failure', c.name, {
      panelVisible: await page.locator('.ni-panel').isVisible().catch(() => false),
      semanticLabel: await page.locator('.ni-problem__label').innerText().catch(() => null),
      semanticDetail: await page.locator('.ni-problem__detail').innerText().catch(() => null),
      retryOffered: await page.getByRole('button', { name: 'RETRY' }).count(),
      statusTiles: await page.locator('.ni-status__tile').count(),
      auditRows: await page.locator('.ni-audit__row').count(),
      emptyStateShown: await page.locator('.ni-empty').count(),
      bodyTextLen: (await page.locator('body').innerText().catch(() => '')).length,
    });
    if (c.name === 'unavailable_503') {
      await page.screenshot({ path: path.join(OUT, 'ni-5-api-503.png'), fullPage: true });
    }
    await page.unroute(`**${NI_PATH}/scenarios/**`);
    await page.unroute(`**${NI_PATH}/scenarios`);
    await page.unroute(`**${NI_PATH}/evaluate`);
  }

  // ---- previous success, then API down: must show the LAST real result
  await page.goto(`${UI}/infrastructure`, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(6000);
  await page.evaluate((k) => window.localStorage.removeItem(k), NI_KEY);
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(6000);
  await page.locator('#ni-scenario').selectOption('combined');
  await page.locator('.ni-button').click();
  await page.waitForSelector('.ni-status__tile', { timeout: 45000 });
  await page.waitForTimeout(1500);
  const beforeDown = await readTiles(page, '.ni-status__tile');

  await page.route(`**${NI_PATH}/**`, (r) =>
    r.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"down"}' }));
  await page.goto(`${UI}/dashboard`, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(9000);
  const widgetText = await page.locator('.ni-widget').innerText().catch(() => '');
  note('previous_success_then_down', 'state', {
    beforeDown,
    widgetVisible: await page.locator('.ni-widget').isVisible().catch(() => false),
    widgetTiles: await readTiles(page, '.ni-widget__tile'),
    widgetFoot: await page.locator('.ni-widget__foot').innerText().catch(() => null),
    readyShown: await page.locator('.ni-widget__ready').count(),
    claimsLiveCurrentActive: /\bLIVE\b|\bCURRENT\b|\bACTIVE\b/.test(widgetText),
  });
  await page.screenshot({ path: path.join(OUT, 'ni-6-last-result-api-down.png'), fullPage: true });
  await page.unroute(`**${NI_PATH}/**`);
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  page.on('pageerror', (e) => report.pageErrors.push(e.message));
  page.on('console', (m) => { if (m.type() === 'error') report.consoleErrors.push(m.text()); });

  try {
    note('mode', 'value', MODE);
    await login(page);
    note('login', 'url', page.url());
    if (MODE === 'happy') {
      await runHappy(page);
      await layout(page);
      await dashboardAndBack(page);
    } else {
      await runFailure(page);
    }
    dump();
    console.log('REPORT WRITTEN');
    console.log(JSON.stringify(report, null, 2).slice(0, 14000));
  } catch (e) {
    report.fatal = e.message;
    dump();
    console.error('FAILED:', e.message);
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
})();
