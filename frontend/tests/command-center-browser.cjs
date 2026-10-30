/* Real-browser verification; no request mocking or persisted credentials.
 * node tests/command-center-browser.cjs [baseline|final]
 */
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
process.loadEnvFile(path.resolve(__dirname, '../.env.local'));
const phase = process.argv[2] === 'final' ? 'final' : 'baseline';
const output = path.resolve(__dirname, '../layout-validation');
(async () => {
  const browser = await chromium.launch({ headless: false });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  const responses = [], errors = [], payloads = {}, layouts = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('response', async r => {
    const url = new URL(r.url());
    if (url.port !== '8000') return;
    responses.push({ path: url.pathname, status: r.status() });
    if (url.pathname.includes('/auth/')) return;
    try { payloads[url.pathname] = await r.json(); } catch {}
  });
  try {
    await page.goto('http://localhost:3000/dashboard', { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForTimeout(5000);
    if (await page.locator('input[type=email]').count()) {
      await page.locator('input[type=email]').fill(process.env.NEXT_PUBLIC_AUTH_EMAIL);
      await page.locator('input[type=password]').fill(process.env.NEXT_PUBLIC_AUTH_PASSWORD);
      await page.locator('button[type=submit]').click();
    }
    await page.waitForURL('**/dashboard', { timeout: 30000 });
    await page.waitForTimeout(10000);
    for (const width of [1920, 1440, 1366]) {
      await page.setViewportSize({ width, height: 1080 });
      await page.waitForTimeout(800);
      layouts.push(await page.evaluate(() => ({
        width: innerWidth, scrollWidth: document.documentElement.scrollWidth,
        text: document.querySelector('main')?.innerText,
        panels: [...document.querySelectorAll('main section')].map(el => {
          const r = el.getBoundingClientRect();
          return { title: el.querySelector('h2,h3')?.textContent, x:r.x, y:r.y, width:r.width, height:r.height };
        }),
      })));
      await page.screenshot({ path: path.join(output, `command-center-${phase}-${width}.png`), fullPage: true });
    }
    let refresh = null;
    if (phase === 'final') {
      const before = responses.length;
      await page.getByRole('button', { name: 'Refresh dashboard', exact: true }).click();
      await page.waitForTimeout(5000);
      refresh = { requests: responses.slice(before), url: page.url() };
    }
    const counts = {};
    for (const [route, body] of Object.entries(payloads)) {
      counts[route] = Array.isArray(body) ? { count: body.length, fields: Object.keys(body[0] ?? {}) } : body;
    }
    fs.writeFileSync(path.join(output, `command-center-${phase}.json`), JSON.stringify({ layouts, responses, errors, counts, refresh }, null, 2));
    console.log(JSON.stringify({ phase, layouts: layouts.map(({text,panels,...x})=>x), responses, errors, refresh }, null, 2));
    if (phase === 'final' && (errors.length || layouts.some(x => x.scrollWidth > x.width))) process.exitCode = 1;
    const signout = page.getByRole('button', { name: 'Sign out', exact: true });
    if (await signout.count()) { await signout.click(); await page.waitForTimeout(1000); }
  } finally { await browser.close(); }
})().catch(e => { console.error(e.message); process.exitCode = 1; });
