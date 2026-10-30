/* Portal responsive + screenshot validation (real browser, authenticated).
 * node tests/portal-responsive-validation.cjs
 *
 * Checks the required viewports for horizontal overflow, single PortalShell,
 * readable tables and truthful empty/error states, and saves screenshots of the
 * Command Center, Operations, the incident list and an incident detail route.
 */
const { chromium } = require("playwright");
const fs = require("node:fs");
const path = require("node:path");

process.loadEnvFile(path.resolve(__dirname, "../.env.local"));
const UI = "http://localhost:3000";
const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const EMAIL = process.env.NEXT_PUBLIC_AUTH_EMAIL;
// Credentials come from frontend/.env.local (gitignored) - never from a literal
// committed to the repository (see ci/secret_scan.py).
const PASSWORD = process.env.NEXT_PUBLIC_AUTH_PASSWORD;
if (!EMAIL || !PASSWORD) {
  console.error(
    "NEXT_PUBLIC_AUTH_EMAIL / NEXT_PUBLIC_AUTH_PASSWORD missing from frontend/.env.local - this harness needs a real dev session.",
  );
  process.exit(2);
}
const SHOTS = path.resolve(__dirname, "_artifacts");
const VIEWPORTS = [
  { name: "1440x900", width: 1440, height: 900 },
  { name: "1700x1300", width: 1700, height: 1300 },
  { name: "1280x900", width: 1280, height: 900 },
];

async function login() {
  const res = await fetch(`${API}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: EMAIL, password: PASSWORD }),
  });
  if (!res.ok) throw new Error(`login failed: ${res.status}`);
  return res.json();
}

async function inspect(page) {
  return page.evaluate(() => {
    const text = document.body.innerText || "";
    const de = document.documentElement;
    return {
      shellMarkers: (text.match(/AUTONOMOUS INFRASTRUCTURE INTELLIGENCE/g) || []).length,
      asides: document.querySelectorAll("aside").length,
      overflowPx: Math.max(0, de.scrollWidth - de.clientWidth),
      tableRows: document.querySelectorAll("table tbody tr").length,
      unavailable: text.includes("did not respond"),
      intelligenceUnavailable: text.includes("the incident intelligence API did not respond"),
      workspaceSections: Array.from(document.querySelectorAll("section[aria-label]")).map((s) =>
        s.getAttribute("aria-label"),
      ),
      hasAiSummary: text.includes("AI OPERATIONS SUMMARY"),
    };
  });
}

(async () => {
  const auth = await login();
  fs.mkdirSync(SHOTS, { recursive: true });
  const browser = await chromium.launch({ headless: true });

  const context = await browser.newContext();
  await context.addInitScript(
    ([t, r]) => {
      window.localStorage.setItem("hsdeep_auth_token", t);
      if (r) window.localStorage.setItem("hsdeep_refresh_token", r);
    },
    [auth.access_token, auth.refresh_token || ""],
  );

  for (const vp of VIEWPORTS) {
    const page = await context.newPage();
    await page.setViewportSize({ width: vp.width, height: vp.height });
    await page.goto(`${UI}/portal`, { waitUntil: "domcontentloaded", timeout: 60000 });
    await page.waitForTimeout(12000);
    const info = await inspect(page);
    console.log(
      `${vp.name}: shellMarkers=${info.shellMarkers} asides=${info.asides} ` +
        `overflow=${info.overflowPx}px rows=${info.tableRows} ` +
        `aiSummary=${info.hasAiSummary} unavailable=${info.unavailable} ` +
        `intelligenceUnavailable=${info.intelligenceUnavailable}`,
    );
    await page.screenshot({ path: path.join(SHOTS, `command-center-${vp.name}.png`), fullPage: false });
    if (vp.name === "1440x900") {
      await page.goto(`${UI}/portal/operations`, { waitUntil: "domcontentloaded", timeout: 60000 });
      await page.waitForTimeout(9000);
      await page.screenshot({ path: path.join(SHOTS, "operations-1440x900.png") });

      await page.goto(`${UI}/portal/resources/incidents`, {
        waitUntil: "domcontentloaded",
        timeout: 60000,
      });
      await page.waitForTimeout(9000);
      const listInfo = await inspect(page);
      await page.screenshot({ path: path.join(SHOTS, "incident-list-1440x900.png") });

      const href = await page
        .evaluate(() => {
          const a = document.querySelector("table tbody tr a[href*='/incidents/']");
          return a ? a.getAttribute("href") : null;
        })
        .catch(() => null);
      console.log(`  incident list rows=${listInfo.tableRows} detailLink=${href ?? "none"}`);
      if (href) {
        await page.goto(`${UI}${href}`, { waitUntil: "domcontentloaded", timeout: 60000 });
        await page.waitForTimeout(9000);
        const detail = await inspect(page);
        console.log(
          `  incident detail: shellMarkers=${detail.shellMarkers} rows=${detail.tableRows} ` +
            `unavailable=${detail.unavailable}`,
        );
        console.log(
          `  investigation sections present: ${
            detail.workspaceSections.length
              ? detail.workspaceSections.join(" | ")
              : "NONE (workspace did not render)"
          }`,
        );
        await page.screenshot({ path: path.join(SHOTS, "incident-detail-1440x900.png") });
      }
    }
    await page.close();
  }

  console.log(`screenshots written to ${SHOTS}`);
  await browser.close();
})().catch((e) => {
  console.error("RESPONSIVE VALIDATION FAILED:", e.message);
  process.exit(1);
});
