/* Focused regression: node tests/soc-unavailable.cjs [--browser]
 * Browser mode reuses the existing application session; it never logs in.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'src/lib/soc/socAdapter.ts'), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText;
const context = { exports: {} };
vm.runInNewContext(compiled, context);
const expected = { blocked: 'Blocked Attacks', identity: 'Identity Alerts', malware: 'Malware Detected' };
const data = context.exports.buildSocData({ alerts: [], incidents: [], events: [], devices: [] });
for (const [key, label] of Object.entries(expected)) {
  const card = data.kpis.find(k => k.key === key);
  assert.ok(card, label);
  assert.equal(card.value, null, `${label}: unavailable is not zero or demo telemetry`);
  assert.equal(card.change, null, `${label}: no invented trend`);
  assert.equal(card.source, 'fallback');
  console.log(`PASS adapter: ${label} value=null, change=null`);
}
assert.ok(data.workflow.every(stage => stage.count === null));
console.log('PASS workflow counts remain unavailable');

async function browserCheck() {
  const { chromium } = require('playwright');
  const browser = await chromium.launchPersistentContext(path.join(root, 'layout-validation/.pw-profile'), {
    headless: true, viewport: { width: 1920, height: 1080 },
  });
  try {
    const page = browser.pages()[0] || await browser.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
    await page.goto('http://localhost:3000/dashboard', { waitUntil: 'domcontentloaded', timeout: 60000 });
    for (const label of Object.values(expected)) {
      const card = page.locator('.soc-kpi').filter({ hasText: label });
      await card.waitFor({ state: 'visible', timeout: 30000 });
      assert.equal(await card.count(), 1);
      assert.equal((await card.locator('.soc-kpi__value').innerText()).trim(), '—', label);
      assert.match(await card.locator('.soc-kpi__foot').innerText(), /Unavailable/);
      assert.equal(await card.locator('.soc-kpi__change').count(), 0);
      assert.doesNotMatch(await card.innerText(), /demo data/);
      console.log(`PASS browser: ${label} shows — / Unavailable; no trend badge`);
    }
    assert.equal(new URL(page.url()).pathname, '/dashboard');
    const screenshot = path.join(root, 'layout-validation/soc-unavailable-1920.png');
    await page.screenshot({ path: screenshot, fullPage: true });
    assert.deepEqual(errors, [], 'No browser console or React errors');
    console.log(`PASS browser: no console/React errors; screenshot=${screenshot}`);
  } finally {
    await browser.close();
  }
}
if (process.argv.includes('--browser')) browserCheck().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
