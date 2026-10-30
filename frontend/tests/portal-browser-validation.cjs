/* Portal browser validation (runtime, real browser).
 * node tests/portal-browser-validation.cjs
 *
 * Reports for each required route: HTTP status of the document, final URL,
 * whether PortalShell rendered, whether the LEGACY sidebar chrome leaked in,
 * whether the legacy CopilotBar leaked in, and structural classes.
 *
 * It never fabricates data: API failures are reported as observed.
 */
const { chromium } = require("playwright");
const path = require("node:path");

process.loadEnvFile(path.resolve(__dirname, "../.env.local"));
const UI = "http://localhost:3000";
const REQUIRED = ["/dashboard", "/portal", "/portal/operations", "/portal/resources/devices"];
const PORTAL_NAV = [
  "/portal",
  "/portal/operations",
  "/portal/resources",
  "/portal/resources/devices",
  "/portal/resources/sites",
  "/portal/resources/sources",
  "/portal/resources/alerts",
  "/portal/resources/incidents",
];

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1700, height: 1300 } });

  const consoleErrors = [];
  const pageErrors = [];
  const failedRequests = [];
  const docStatus = {};

  page.on("pageerror", (e) => pageErrors.push(e.message));
  page.on("console", (m) => {
    if (m.type() === "error") consoleErrors.push(m.text());
  });
  page.on("response", (r) => {
    const u = r.url();
    if (r.request().resourceType() === "document") docStatus[new URL(u).pathname] = r.status();
    if (r.status() >= 400 && /\/api\//.test(u)) failedRequests.push(`${r.status()} ${u}`);
  });

  async function probe(route) {
    await page.goto(`${UI}${route}`, { waitUntil: "domcontentloaded", timeout: 60000 });
    await page.waitForTimeout(6000);
    const info = await page.evaluate(() => {
      const text = document.body.innerText || "";
      const hasClass = (re) =>
        Array.from(document.querySelectorAll("*")).some((el) => {
          const c = el.className;
          return typeof c === "string" && re.test(c);
        });
      return {
        url: location.pathname,
        textLen: text.length,
        // Portal-only marker (PortalShell top bar):
        portalShell: text.includes("AUTONOMOUS INFRASTRUCTURE INTELLIGENCE"),
        // Legacy-sidebar-only marker (a legacy nav child):
        legacySidebar: text.includes("Asset Inventory"),
        copilot: hasClass(/copilot/i),
        hsApp: !!document.querySelector(".hs-app"),
        hsMain: !!document.querySelector(".hs-main"),
        asides: document.querySelectorAll("aside").length,
        head: text.slice(0, 240).replace(/\s+/g, " "),
      };
    });
    return { route, status: docStatus[route] ?? null, ...info };
  }

  const out = { required: [], nav: [], clickNav: null, consoleErrors, pageErrors, failedRequests };

  try {
    for (const r of REQUIRED) out.required.push(await probe(r));

    // Click-through: legacy sidebar -> Command Center
    await page.goto(`${UI}/dashboard`, { waitUntil: "domcontentloaded", timeout: 60000 });
    await page.waitForTimeout(4000);
    const link = page.locator('a[href="/portal"]').first();
    const hasLink = (await link.count()) > 0;
    if (hasLink) {
      await link.click();
      await page.waitForTimeout(6000);
      out.clickNav = { clicked: true, url: new URL(page.url()).pathname };
    } else {
      out.clickNav = { clicked: false, url: null, reason: 'no a[href="/portal"] in sidebar' };
    }

    for (const r of PORTAL_NAV) out.nav.push(await probe(r));
  } finally {
    console.log(JSON.stringify(out, null, 2));
    await browser.close();
  }
})().catch((e) => {
  console.error("ERROR:", e.message);
  process.exitCode = 1;
});
