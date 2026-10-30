/* Focused browser check for the reported defect: raw PRTG HTML leaking into
 * the Alerts UI. Logs in for real, visits /alerts, and asserts the markup is
 * not present in the rendered DOM text.
 * node tests/alerts-raw-html.cjs
 */
const { chromium } = require('playwright');
const path = require('node:path');
process.loadEnvFile(path.resolve(__dirname, '../.env.local'));

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });

  try {
    await page.goto('http://localhost:3000/alerts', { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForTimeout(4000);

    if (await page.locator('input[type=email]').count()) {
      await page.locator('input[type=email]').fill(process.env.NEXT_PUBLIC_AUTH_EMAIL);
      await page.locator('input[type=password]').fill(process.env.NEXT_PUBLIC_AUTH_PASSWORD);
      await page.locator('button[type=submit]').click();
      await page.waitForTimeout(6000);
    }

    const info = await page.evaluate(() => {
      const main = document.querySelector('main');
      const text = main ? main.innerText : '';
      return {
        url: location.pathname,
        textLength: text.length,
        // The literal markup strings the operator reported seeing.
        hasLogmessage: text.includes('logmessage'),
        hasDivTag: /<\s*div/i.test(text),
        hasMoreicon: text.includes('moreicon'),
        // Any element whose innerHTML was injected as markup rather than text?
        innerHtmlInjected: /<div[^>]*class="logmessage"/i.test(main ? main.innerHTML : ''),
        rawBadgeCount: (text.match(/· raw/g) || []).length,
        alertRows: document.querySelectorAll('.alert-row').length,
        sample: text.split('\n').slice(0, 25).join(' | ').slice(0, 900),
      };
    });

    await page.screenshot({ path: path.resolve(__dirname, '../layout-validation/alerts-normalized.png'), fullPage: false });
    console.log(JSON.stringify({ info, errors }, null, 2));

    const failed = info.hasLogmessage || info.hasDivTag || info.hasMoreicon || errors.length;
    if (failed) process.exitCode = 1;
  } finally {
    await browser.close();
  }
})().catch(e => { console.error('ERROR:', e.message); process.exitCode = 1; });
