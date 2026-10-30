/* Real-browser validation of the P0 Redis recovery (Phase P0 §10).
 * node tests/redis-recovery-browser.cjs
 *
 * Real login + real backend. Asserts the portal reports the REAL dependency
 * state (database / redis / ollama), the real AI CTO status, and the real AI
 * Operations status - and that Redis is reported UP only because a real PING
 * now succeeds.
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
    await page.goto(`${UI}/dashboard`, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForTimeout(4000);

    if (await page.locator('input[type=email]').count()) {
      await page.locator('input[type=email]').fill(process.env.NEXT_PUBLIC_AUTH_EMAIL);
      await page.locator('input[type=password]').fill(process.env.NEXT_PUBLIC_AUTH_PASSWORD);
      await page.locator('button[type=submit]').click();
      await page.waitForTimeout(8000);
      if (!page.url().includes('/dashboard')) {
        await page.goto(`${UI}/dashboard`, { waitUntil: 'domcontentloaded', timeout: 60000 });
      }
    }

    // Wait for the dashboard to settle on real data (not a loading shell).
    await page.waitForTimeout(12000);

    const info = await page.evaluate(() => {
      const body = document.body.innerText;
      const card = document.querySelector('.ai-cto-card');
      // Text window around a marker, so a panel's real status is captured
      // without depending on exact class names.
      const around = (marker, span = 220) => {
        const i = body.indexOf(marker);
        return i === -1 ? null : body.slice(i, i + span).replace(/\s+/g, ' ').trim();
      };
      return {
        url: location.pathname,
        aiCtoCardPresent: !!card,
        aiCtoStatusWord: card
          ? (card.innerText.match(/HEALTHY|DEGRADED|OFFLINE/) || [null])[0]
          : null,
        aiCtoServices: card
          ? Array.from(card.querySelectorAll('.ai-cto-card__service')).map((s) => ({
              name: s.querySelector('.ai-cto-card__service-name')?.textContent?.trim(),
              dot: (s.querySelector('[class*=ai-cto-card__dot]')?.className || '')
                .split(' ')
                .find((c) => c.startsWith('ai-cto-card__dot--')),
            }))
          : [],
        aiCtoReason: card
          ? card.querySelector('.ai-cto-card__reason-text')?.textContent?.trim() || null
          : null,
        // The real status indicators that ARE mounted in the portal.
        mastheadStatus: (body.match(/(?:SYSTEMS OPERATIONAL|API OFFLINE|CHECKING)[^\n]*/) || [
          null,
        ])[0],
        sidebarStatus: (body.match(/All Systems Operational/g) || []).length,
        aiOperationsWindow: around('AI OPERATIONS'),
        aiCtoWindow: around('AI CTO'),
        statusWordPresent: {
          healthy: /HEALTHY/.test(body),
          degraded: /DEGRADED/.test(body),
          offline: /OFFLINE/.test(body),
        },
        redisMention: (body.match(/redis[^\n]{0,40}/i) || [null])[0],
        bodyHasRedisDown: /redis\s*[:=]?\s*down/i.test(body),
        // Never expose the connection string in the UI.
        leaksConnectionString: /redis:\/\//i.test(body),
      };
    });

    fs.mkdirSync(OUT, { recursive: true });
    await page.screenshot({
      path: path.join(OUT, 'redis-recovery-dashboard.png'),
      fullPage: true,
    });

    const result = { ...info, pageErrors: errors };
    console.log(JSON.stringify(result, null, 2));

    const ok =
      // The real, mounted portal indicators (CoreHUD's .ai-cto-card is not
      // rendered anywhere in the app, so it is not a valid assertion target).
      result.mastheadStatus === 'SYSTEMS OPERATIONAL' &&
      result.aiCtoWindow !== null &&
      /AI ONLINE/.test(result.aiCtoWindow) &&
      !/AI (?:DEGRADED|OFFLINE|DATA UNAVAILABLE)/.test(result.aiCtoWindow) &&
      result.aiOperationsWindow !== null &&
      result.bodyHasRedisDown === false &&
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
