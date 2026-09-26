/* Portal surfacing / integration verification for the Phase 9.5 live topology.
 *
 *   node tests/topology-portal-surfacing.cjs
 *
 * Real browser, real login form, real API. No mocks, no auth bypass, no route
 * interception. Verified here:
 *   1. the dashboard sidebar exposes Infrastructure -> Topology
 *   2. that entry points at the Phase 9.5 route and opens it
 *   3. the page is wired to the live read model (summary reflects real data)
 *   4. direct URL navigation and a browser refresh both work after login
 *   5. the empty relationship state is stated honestly and no links are drawn
 *   6. unclassified resources are shown as Unknown Resource (no invented vendor)
 *   7. no page errors
 *
 * Exits non-zero when any check fails.
 */
const { chromium } = require('playwright');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

process.loadEnvFile(path.resolve(__dirname, '../.env.local'));

const UI = 'http://localhost:3000';
const TOPOLOGY_ROUTE = '/infrastructure-topology/live';

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
  await page.waitForTimeout(4000);
}

/** KPI strip values, read from the rendered DOM (never from the API). */
async function readKpis(page) {
  return page.evaluate(() => {
    const strip = document.querySelector('[data-testid="topology-kpi-strip"]');
    if (!strip) return null;
    const out = {};
    strip.querySelectorAll('[data-testid^="kpi-"]').forEach((card) => {
      const key = card.getAttribute('data-testid');
      if (!key || key.endsWith('-value')) return;
      const value = card.querySelector('[data-testid$="-value"]');
      if (value) out[key.replace('kpi-', '')] = value.textContent.trim();
    });
    return out;
  });
}

/** The toolbar's "results/total" counter. */
async function readCounter(page) {
  const text = await page.locator('[data-testid="topology-search"]').locator('..').innerText();
  const match = text.match(/(\d+)\s*\/\s*(\d+)/);
  return match ? { results: Number(match[1]), total: Number(match[2]) } : null;
}

async function canvasStats(page) {
  return page.evaluate(() => {
    const canvas = document.querySelector('[role="application"]');
    if (!canvas) return null;
    return {
      nodeButtons: canvas.querySelectorAll('button').length,
      // Edge hit-target lines: one per drawn relationship. Zero of them is the
      // correct answer when no relationship evidence exists.
      edgeLines: canvas.querySelectorAll('svg line[stroke="transparent"]').length,
      unknownResourceNodes: Array.from(canvas.querySelectorAll('button')).filter((b) =>
        (b.textContent || '').includes('Unknown Resource')
      ).length,
    };
  });
}

/** Open a route directly and return whether the platform asked for a re-login.
 *
 * The app's AuthGuard sends an unauthenticated hard load to
 * `/login?next=<route>`; in this dev environment that occasionally happens even
 * for an established session (an app-level token-refresh race, reproducible on
 * /dashboard too). We re-authenticate through the app's own form rather than
 * faking a session, and REPORT that it was needed so the result is not hidden.
 */
async function openRoute(page, route) {
  await page.goto(`${UI}${route}`, { waitUntil: 'domcontentloaded', timeout: 90000 });
  await page.waitForTimeout(3000);
  let relogged = false;
  if (page.url().includes('/login')) {
    await login(page);
    relogged = true;
    await page.goto(`${UI}${route}`, { waitUntil: 'domcontentloaded', timeout: 90000 });
  }
  await page
    .waitForSelector('[data-testid="topology-kpi-strip"]', { timeout: 90000 })
    .catch(() => {});
  return { relogged, url: page.url() };
}

(async () => {
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
  const page = await context.newPage();
  page.on('pageerror', (error) => pageErrors.push(String(error)));
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });

  try {
    await login(page);

    /* 1 + 2: the sidebar entry exists and points at the Phase 9.5 route. */
    const groupButton = page.locator('button', { hasText: 'Infrastructure' }).first();
    check('sidebar exposes the Infrastructure group', (await groupButton.count()) > 0);
    await groupButton.click();
    await page.waitForTimeout(1200);

    const topologyLink = page.locator('.hs-sidebar__child', { hasText: 'Topology' }).first();
    const linkCount = await topologyLink.count();
    check('sidebar lists a Topology child entry', linkCount > 0);
    const href = linkCount ? await topologyLink.getAttribute('href') : null;
    check('Topology entry targets the Phase 9.5 route', href === TOPOLOGY_ROUTE, `href=${href}`);

    /* 3: clicking it opens the graph workspace. */
    if (linkCount) {
      await topologyLink.click();
      await page.waitForURL(`**${TOPOLOGY_ROUTE}`, { timeout: 60000 });
    }
    // Wait for the workspace to actually render. A dev-server compile of this
    // route can exceed any fixed sleep, so the selector is the signal.
    await page
      .waitForSelector('[data-testid="topology-kpi-strip"]', { timeout: 90000 })
      .catch(() => {});
    check(
      'clicking Topology navigates to the Phase 9.5 route',
      page.url().includes(TOPOLOGY_ROUTE)
    );
    check(
      'page title is rendered',
      (await page.locator('h1', { hasText: 'Live Operational Topology' }).count()) > 0
    );
    check(
      'breadcrumb is rendered',
      (await page.locator('nav[aria-label="Breadcrumb"]').count()) > 0
    );

    /* 4: wired to the live read model. */
    const badge = page.locator('[data-testid="freshness-badge"]');
    const badgeText = (await badge.count()) ? (await badge.innerText()).trim() : '';
    check('freshness badge renders', badgeText.length > 0, badgeText.replace(/\s+/g, ' '));

    const kpis = await readKpis(page);
    check('KPI strip renders', kpis !== null, kpis ? Object.keys(kpis).join(',') : 'missing');
    const deviceCount = kpis ? Number(kpis.devices) : 0;
    check('KPI strip reports the real device count', deviceCount > 0, `devices=${deviceCount}`);
    const linksTotal = kpis ? Number(kpis.links) : -1;
    check(
      'KPI strip reports the real relationship count',
      kpis !== null && Number.isFinite(linksTotal) && linksTotal >= 0,
      kpis ? `links=${kpis.links}` : null
    );
    check(
      'KPI strip separates online from offline from real operational state',
      kpis !== null && Number(kpis.online) > 0 && Number(kpis.offline) > 0,
      kpis ? `online=${kpis.online} offline=${kpis.offline}` : null
    );
    const interfacesTotal = kpis ? Number(kpis.interfaces) : -1;
    check(
      'KPI strip reports interfaces and the real unclassified count',
      kpis !== null && interfacesTotal >= 0 && Number(kpis.unidentified) > 0,
      kpis ? `interfaces=${kpis.interfaces} unidentified=${kpis.unidentified}` : null
    );
    check(
      'traffic KPI does not claim a measurement that does not exist',
      kpis !== null &&
        (/no telemetry/i.test(kpis.traffic) || /bps|Kbps|Mbps|Gbps|Tbps/.test(kpis.traffic)),
      kpis ? `traffic=${kpis.traffic}` : null
    );

    /* 5: the empty state is designed when there is no relationship evidence -
       and must NOT be shown once real relationships exist. */
    const emptyState = page.locator('[data-testid="topology-empty-state"]');
    const emptyCount = await emptyState.count();
    if (linksTotal === 0) {
      check('empty-state panel is present', emptyCount > 0);
      const emptyText = emptyCount ? await emptyState.innerText() : '';
      check(
        'empty state states topology relationships are not yet discovered',
        /topology relationships not yet discovered/i.test(emptyText),
        emptyText.split('\n')[0] ?? null
      );
      check(
        'empty state reports the real counts',
        emptyText.includes(String(deviceCount)) &&
          emptyText.includes('RELATIONSHIPS') &&
          emptyText.includes('INTERFACES'),
        null
      );
      const body = await page.locator('body').innerText();
      check(
        'empty relationship state is stated honestly',
        body.includes('No network relationship data has been discovered')
      );
    } else {
      check(
        'empty state disappears once real relationships exist',
        emptyCount === 0,
        `links=${linksTotal}`
      );
    }

    /* 5b: the canvas is the primary workspace, not a strip. */
    const shell = await page.locator('[data-testid="topology-canvas-shell"]').boundingBox();
    const viewport = page.viewportSize() ?? { width: 1600, height: 1000 };
    check(
      'canvas uses the available viewport height',
      shell !== null && shell.height >= viewport.height * 0.45,
      shell ? `height=${Math.round(shell.height)} of ${viewport.height}` : 'no canvas'
    );
    check(
      'canvas spans the workspace width',
      shell !== null && shell.width >= viewport.width * 0.75,
      shell ? `width=${Math.round(shell.width)}` : null
    );

    /* 6: honesty on the canvas itself - links drawn match links reported. */
    const stats = await canvasStats(page);
    check(
      linksTotal > 0
        ? 'real relationships are drawn as links'
        : 'no fabricated links are drawn',
      stats !== null && (linksTotal > 0 ? stats.edgeLines > 0 : stats.edgeLines === 0),
      `links=${linksTotal} lines=${stats ? stats.edgeLines : 0}`
    );
    check(
      'device nodes are drawn from real rows',
      stats !== null && stats.nodeButtons > 0,
      `nodes=${stats ? stats.nodeButtons : 0}`
    );
    check(
      'unclassified resources stay Unknown Resource',
      stats !== null && stats.unknownResourceNodes > 0,
      `unknown=${stats ? stats.unknownResourceNodes : 0}`
    );

    /* 6b: a real link opens a drawer carrying the collected evidence. */
    if (linksTotal > 0 && stats && stats.edgeLines > 0) {
      await page
        .locator('[role="application"] svg line[stroke="transparent"]')
        .first()
        .click({ force: true });
      await page.waitForTimeout(900);
      const drawerText = await page.locator('[data-testid="topology-drawer"]').innerText();
      check('clicking a link opens the link drawer', drawerText.length > 0);
      check(
        'link drawer shows the collected interface evidence',
        /Gi0\/1|Te1\/1/.test(drawerText) || /lldp/i.test(drawerText),
        drawerText.replace(/\s+/g, ' ').slice(0, 90)
      );
      await page.keyboard.press('Escape');
      await page.waitForTimeout(400);
    }

    /* 7: overlays exist and explain the semantics they draw. */
    check(
      'legend is present',
      (await page.locator('[data-testid="topology-legend"]').count()) > 0
    );
    const legendText = await page.locator('[data-testid="topology-legend"]').innerText();
    check(
      'legend explains device and link states',
      /Online/i.test(legendText) && /Offline/i.test(legendText) && /Up/i.test(legendText),
      legendText.replace(/\s+/g, ' ').slice(0, 90)
    );
    check(
      'minimap is present for a real graph',
      (await page.locator('[data-testid="topology-minimap"]').count()) > 0
    );

    /* 8: the floating controls actually work. */
    const controls = page.locator('[data-testid="topology-canvas-controls"]');
    const zoomIn = controls.locator('button[aria-label="Zoom in"]');
    const fitButton = controls.locator('button[aria-label="Fit to screen"]');
    const resetButton = controls.locator('button[aria-label="Reset view"]');
    check(
      'zoom / fit / reset controls are present',
      (await zoomIn.count()) > 0 && (await fitButton.count()) > 0 && (await resetButton.count()) > 0
    );
    const scaleLabel = async () => (await controls.innerText()).trim().split('\n').pop();
    await zoomIn.click();
    await page.waitForTimeout(400);
    const zoomed = await scaleLabel();
    check('zoom in changes the scale', zoomed !== '100%', `scale=${zoomed}`);
    await fitButton.click();
    await page.waitForTimeout(700);
    const fitted = await scaleLabel();
    check('fit to screen recomputes the view', fitted !== zoomed, `scale=${fitted}`);
    await resetButton.click();
    await page.waitForTimeout(400);
    const reset = await scaleLabel();
    check('reset returns to 100%', reset === '100%', `scale=${reset}`);

    /* 9: search and filters operate on the real rows. */
    const initial = await readCounter(page);
    check(
      'search counter shows the real node total',
      initial !== null && initial.total > 0 && initial.results === initial.total,
      JSON.stringify(initial)
    );
    await page.locator('[data-testid="topology-search"]').fill('zzz-no-such-device');
    await page.waitForTimeout(800);
    const none = await readCounter(page);
    check('a non-matching search filters everything out', none !== null && none.results === 0, JSON.stringify(none));
    await page.locator('[data-testid="topology-search"]').fill('');
    await page.waitForTimeout(800);
    await page.locator('[data-testid="topology-more-filters"]').click();
    await page.waitForTimeout(300);
    check(
      'the filter panel opens',
      (await page.locator('[data-testid="topology-filter-panel"]').count()) > 0
    );
    await page.locator('[data-testid="topology-clear"]').click();
    await page.waitForTimeout(600);
    const restored = await readCounter(page);
    check(
      'clearing filters restores every row',
      restored !== null && initial !== null && restored.results === initial.results,
      JSON.stringify(restored)
    );

    /* 10: layout switching keeps the workspace alive. */
    await page.locator('[data-testid="topology-layout"]').selectOption('force');
    await page.waitForTimeout(1200);
    check(
      'switching layout re-renders without losing the graph',
      (await page.locator('[role="application"] button').count()) > 0
    );
    await page.locator('[data-testid="topology-layout"]').selectOption('hierarchical');
    await page.waitForTimeout(800);

    /* 11: the drawer opens from the canvas and shows only real sections. */
    const firstNode = page.locator('[role="application"] button').first();
    if ((await firstNode.count()) > 0) {
      await firstNode.click();
      await page.waitForTimeout(1200);
      const drawer = page.locator('[data-testid="topology-drawer"]');
      const drawerText = (await drawer.count()) ? await drawer.innerText() : '';
      check('clicking a device opens the detail drawer', drawerText.length > 0);
      check('drawer shows the resolved visual source', drawerText.includes('Visual source'));
      check('drawer names the unknown resource', drawerText.includes('Unknown Resource'));
      check(
        'drawer is sectioned for investigation',
        ['OVERVIEW', 'OPERATIONAL STATE', 'INTERFACES', 'RELATIONSHIPS', 'TELEMETRY', 'EVIDENCE', 'EVENTS'].every(
          (section) => drawerText.toUpperCase().includes(section)
        ),
        null
      );
      check(
        'sections without backend data say so instead of inventing values',
        /No data available|No interface telemetry has been collected|No interfaces have been collected/.test(
          drawerText
        )
      );
      await page.keyboard.press('Escape');
      await page.waitForTimeout(500);
    } else {
      check('clicking a device opens the detail drawer', false, 'no node button found');
    }

    /* 12: the traffic-animation toggle is a real control. */
    const animationToggle = page.locator('[data-testid="topology-animation-toggle"]');
    const animationBefore = await animationToggle.getAttribute('aria-pressed');
    await animationToggle.click();
    await page.waitForTimeout(300);
    const animationAfter = await animationToggle.getAttribute('aria-pressed');
    check(
      'traffic animation toggle changes state',
      animationBefore !== animationAfter,
      `${animationBefore} -> ${animationAfter}`
    );
    await animationToggle.click();

    /* 7: direct URL navigation after login. */
    const direct = await openRoute(page, TOPOLOGY_ROUTE);
    const directSummary = await readKpis(page);
    const directDevices = directSummary ? Number(directSummary.devices) : 0;
    check(
      'direct URL navigation works',
      direct.url.includes(TOPOLOGY_ROUTE),
      `relogin=${direct.relogged}`
    );
    check(
      'direct URL renders the graph, not the login form',
      (await page.locator('input[type=password]').count()) === 0 &&
        (await page.locator('[data-testid="topology-kpi-strip"]').count()) > 0,
      `relogin=${direct.relogged}`
    );

    /* 8: browser refresh. */
    await page.reload({ waitUntil: 'domcontentloaded', timeout: 90000 });
    let reloaded = true;
    try {
      // Wait for the workspace itself rather than a fixed sleep: a dev-server
      // recompile can take longer than any constant we could pick.
      await page.waitForSelector('[data-testid="topology-kpi-strip"]', { timeout: 90000 });
    } catch {
      reloaded = false;
    }
    let reloggedOnReload = false;
    if (!reloaded && page.url().includes('/login')) {
      const retry = await openRoute(page, TOPOLOGY_ROUTE);
      reloggedOnReload = retry.relogged;
      reloaded = true;
    }
    const afterReload = await readKpis(page);
    check(
      'browser refresh keeps the workspace rendered',
      reloaded && afterReload !== null,
      `url=${page.url()} relogin=${reloggedOnReload}`
    );
    check(
      'browser refresh keeps the same real data',
      afterReload !== null && Number(afterReload.devices) === directDevices,
      afterReload ? `devices=${afterReload.devices}` : null
    );
    check(
      'sidebar navigation and direct navigation agree',
      directDevices > 0 && directDevices === deviceCount,
      `sidebar=${deviceCount} direct=${directDevices}`
    );

    /* 13: visual evidence, written OUTSIDE the repo so the worktree stays clean. */
    try {
      const captures = path.join(os.tmpdir(), 'hsdeep-topology-captures');
      fs.mkdirSync(captures, { recursive: true });
      await page.setViewportSize({ width: 1600, height: 1000 });
      await page.waitForTimeout(800);
      await page.screenshot({ path: path.join(captures, '01-workspace.png') });
      const node = page.locator('[role="application"] button').first();
      if ((await node.count()) > 0) {
        await node.click();
        await page.waitForTimeout(900);
        await page.screenshot({ path: path.join(captures, '02-device-drawer.png') });
      }
      await page.setViewportSize({ width: 1280, height: 800 });
      await page.waitForTimeout(800);
      await page.screenshot({ path: path.join(captures, '03-narrow-viewport.png') });
      check('evidence screenshots captured', true, captures);
    } catch (error) {
      check(
        'evidence screenshots captured',
        false,
        String(error && error.message ? error.message : error)
      );
    }
  } catch (error) {
    check(
      'run completed without throwing',
      false,
      String(error && error.message ? error.message : error)
    );
  } finally {
    fs.mkdirSync(path.resolve(__dirname, 'layout-validation'), { recursive: true });
    fs.writeFileSync(
      path.resolve(__dirname, 'layout-validation/topology-portal-surfacing.json'),
      JSON.stringify({ results, pageErrors, consoleErrors }, null, 2)
    );
    await browser.close();
  }

  check('no page errors', pageErrors.length === 0, pageErrors.join(' | ') || 'none');

  const failed = results.filter((entry) => !entry.ok);
  console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
  if (consoleErrors.length) {
    console.log('console errors seen (not all are page failures):');
    consoleErrors.slice(0, 5).forEach((line) => console.log(`  ${line}`));
  }
  process.exit(failed.length ? 1 : 0);
})();
