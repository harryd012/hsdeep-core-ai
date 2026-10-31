/* Focused follow-up probe for the three open questions from the first run:
 *  (a) does /portal eventually render past AuthGuard's "CHECKING SESSION…"?
 *  (b) how is the "Command Center" sidebar entry rendered, and does clicking it
 *      navigate to /portal?
 *  (c) does /portal/resources/devices render REAL table rows from the API?
 * node tests/portal-followup-probe.cjs
 */
const { chromium } = require("playwright");
const path = require("node:path");

process.loadEnvFile(path.resolve(__dirname, "../.env.local"));
const UI = "http://localhost:3000";

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1700, height: 1300 } });
  const consoleErrors = [];
  const pageErrors = [];
  page.on("pageerror", (e) => pageErrors.push(e.message));
  page.on("console", (m) => {
    if (m.type() === "error") consoleErrors.push(m.text());
  });

  const out = {};

  try {
    // (a) /portal with a generous wait - does it get past AuthGuard?
    await page.goto(`${UI}/portal`, { waitUntil: "domcontentloaded", timeout: 60000 });
    let portalReady = null;
    for (let i = 0; i < 12; i++) {
      await page.waitForTimeout(2000);
      const t = await page.evaluate(() => document.body.innerText || "");
      if (t.includes("AUTONOMOUS INFRASTRUCTURE INTELLIGENCE")) {
        portalReady = { waitedMs: (i + 1) * 2000, textLen: t.length };
        break;
      }
    }
    out.portalReady = portalReady;
    out.portalSections = await page.evaluate(() => {
      const t = document.body.innerText || "";
      return {
        hasCommandCenter: t.includes("Command Center"),
        hasOperationsSection: t.includes("OPERATIONS"),
        hasInfrastructureSection: t.includes("INFRASTRUCTURE"),
        hasErrors: /Unavailable/.test(t),
      };
    });

    // (b) How is the Command Center entry rendered on /dashboard?
    await page.goto(`${UI}/dashboard`, { waitUntil: "domcontentloaded", timeout: 60000 });
    await page.waitForTimeout(6000);
    out.sidebarEntry = await page.evaluate(() => {
      const hits = [];
      for (const el of Array.from(document.querySelectorAll("a,button"))) {
        const t = (el.textContent || "").trim();
        if (t.includes("Command Center")) {
          hits.push({ tag: el.tagName, href: el.getAttribute("href"), cls: (el.className || "").slice(0, 40) });
        }
      }
      return hits;
    });

    // Click it by text (works for either an <a> or a <button>).
    const target = page.locator('a:has-text("Command Center"), button:has-text("Command Center")').first();
    if ((await target.count()) > 0) {
      await target.click();
      await page.waitForTimeout(8000);
      out.clickResult = {
        url: new URL(page.url()).pathname,
        portalShell: (await page.evaluate(() => document.body.innerText || "")).includes(
          "AUTONOMOUS INFRASTRUCTURE INTELLIGENCE",
        ),
      };
    } else {
      out.clickResult = { url: null, reason: "no clickable Command Center element found" };
    }

    // (c) Real device rows from the API.
    await page.goto(`${UI}/portal/resources/devices`, { waitUntil: "domcontentloaded", timeout: 60000 });
    await page.waitForTimeout(10000);
    out.devices = await page.evaluate(() => {
      const text = document.body.innerText || "";
      const rows = document.querySelectorAll("table tbody tr").length;
      const api = window.performance
        .getEntriesByType("resource")
        .map((r) => r.name)
        .filter((n) => n.includes("/api/"));
      return {
        tableRows: rows,
        apiCalls: api,
        emptyState: text.includes("No devices were returned"),
        unavailable: text.includes("Unavailable"),
        searching: text.includes("Loading"),
      };
    });

    out.consoleErrors = consoleErrors;
    out.pageErrors = pageErrors;
  } finally {
    console.log(JSON.stringify(out, null, 2));
    await browser.close();
  }
})().catch((e) => {
  console.error("ERROR:", e.message);
  process.exitCode = 1;
});
