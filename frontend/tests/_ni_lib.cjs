/* Real-browser validation of the Network Intelligence surfaces.
 * node tests/network-intelligence-validation.cjs
 *
 * Uses the EXISTING login form + .env.local credentials (same mechanism as
 * tests/command-center-browser.cjs). No auth bypass, no mocking of the app's
 * own API. Route interception is used ONLY to simulate the
 * network-intelligence endpoint being unavailable (a client-side condition).
 */
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');

process.loadEnvFile(path.resolve(__dirname, '../.env.local'));

const OUT = path.resolve(__dirname, '../layout-validation');
const NI_KEY = 'hsdeep_network_intelligence_last_evaluation';
const UI = 'http://localhost:3000';
const NI_PATH = '/api/network-intelligence';

const report = { phases: {}, pageErrors: [], consoleErrors: [] };

function note(phase, key, value) {
  report.phases[phase] = report.phases[phase] || {};
  report.phases[phase][key] = value;
}

function dump() {
  fs.mkdirSync(OUT, { recursive: true });
  fs.writeFileSync(
    path.join(OUT, 'network-intelligence-runtime.json'),
    JSON.stringify(report, null, 2),
  );
}

async function login(page) {
  await page.goto(`${UI}/dashboard`, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(4000);
  if (await page.locator('input[type=email]').count()) {
    await page.locator('input[type=email]').fill(process.env.NEXT_PUBLIC_AUTH_EMAIL);
    await page.locator('input[type=password]').fill(process.env.NEXT_PUBLIC_AUTH_PASSWORD);
    await page.locator('button[type=submit]').click();
    await page.waitForURL('**/dashboard', { timeout: 45000 });
  }
  await page.waitForTimeout(4000);
}

async function readTiles(page, sel) {
  return page.evaluate((s) => {
    const out = {};
    document.querySelectorAll(s).forEach((el) => {
      const label = el.querySelector('.ni-status__label,.ni-widget__label')
        ?.textContent?.trim();
      const value = el.querySelector('strong')?.textContent?.trim();
      if (label) out[label] = value;
    });
    return out;
  }, sel);
}

async function readKv(page, cardIndex) {
  return page.evaluate((i) => {
    const out = {};
    const card = document.querySelectorAll('.ni-card')[i];
    if (!card) return out;
    card.querySelectorAll('.ni-kv > div').forEach((d) => {
      const dt = d.querySelector('dt')?.textContent?.trim();
      const dd = d.querySelector('dd')?.textContent?.trim();
      if (dt) out[dt] = dd;
    });
    return out;
  }, cardIndex);
}

module.exports = { chromium, fs, path, OUT, NI_KEY, NI_PATH, UI, report, note, dump, login, readTiles, readKv };
