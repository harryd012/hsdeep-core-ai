/* Real-browser validation of the task progression fix (P0 §9/§12).
 * node tests/task-progression-browser.cjs
 *
 * Compares what the LIVE PORTAL displays against the REAL backend values for
 * the same concepts, so a stale or fabricated status cannot pass.
 */
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');

process.loadEnvFile(path.resolve(__dirname, '../.env.local'));

const UI = 'http://localhost:3000';
const API = 'http://localhost:8000';
const OUT = path.resolve(__dirname, '../layout-validation');

async function backendState() {
  const login = await fetch(`${API}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      email: process.env.NEXT_PUBLIC_AUTH_EMAIL,
      password: process.env.NEXT_PUBLIC_AUTH_PASSWORD,
    }),
  });
  const { access_token: token } = await login.json();
  const headers = { Authorization: `Bearer ${token}` };
  const counts = await (await fetch(`${API}/api/tasks/counts`, { headers })).json();
  const ov = await (
    await fetch(`${API}/api/command-center/overview`, { headers })
  ).json();
  return { counts, health: ov.health, tasks: ov.tasks, metrics: ov.metrics };
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1400 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(`console: ${m.text()}`);
  });

  try {
    const backend = await backendState();

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
    await page.waitForTimeout(12000);

    const portal = await page.evaluate(() => {
      const body = document.body.innerText;
      const num = (re) => {
        const m = body.match(re);
        return m ? Number(m[1]) : null;
      };
      return {
        queued: num(/QUEUED\s+(\d+)/i),
        activeTasks: num(/ACTIVE TASKS\s+(\d+)/i),
        activeAgents: (body.match(/ACTIVE AGENTS\s+(\d+\s*\/\s*\d+)/i) || [null, null])[1],
        aiCtoWindow: (body.match(/AI CTO[\s\S]{0,200}/) || [null])[0],
        masthead: (body.match(/(?:SYSTEMS OPERATIONAL|API OFFLINE|CHECKING)/) || [null])[0],
      };
    });

    fs.mkdirSync(OUT, { recursive: true });
    await page.screenshot({ path: path.join(OUT, 'task-progression-dashboard.png'), fullPage: true });

    const backendQueued = backend.counts.created ?? 0;
    const result = { backend, portal, pageErrors: errors };
    console.log(JSON.stringify(result, null, 2));

    const ok =
      // The portal's QUEUED must equal the backend's real `created` count.
      portal.queued === backendQueued &&
      portal.activeTasks !== null &&
      /AI ONLINE/.test(portal.aiCtoWindow || '') &&
      portal.masthead === 'SYSTEMS OPERATIONAL' &&
      backend.health?.dependencies?.redis === 'up' &&
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
