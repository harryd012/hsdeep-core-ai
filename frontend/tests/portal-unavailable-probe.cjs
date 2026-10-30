/* Focused probe: WHICH Portal section reports "did not respond"?
 * node tests/portal-unavailable-probe.cjs
 */
const { chromium } = require("playwright");
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

(async () => {
  const res = await fetch(`${API}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: EMAIL, password: PASSWORD }),
  });
  const auth = await res.json();

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await context.addInitScript(([t, r]) => {
    window.localStorage.setItem("hsdeep_auth_token", t);
    if (r) window.localStorage.setItem("hsdeep_refresh_token", r);
  }, [auth.access_token, auth.refresh_token || ""]);
  const page = await context.newPage();

  const failed = [];
  const apiSeen = [];
  page.on("response", (r) => {
    if (!/\/api\//.test(r.url())) return;
    apiSeen.push(`${r.status()} ${r.url().replace(API, "").split("?")[0]}`);
    if (r.status() >= 400) failed.push(`${r.status()} ${r.url().replace(API, "")}`);
  });

  await page.goto(`${UI}/portal`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForTimeout(15000);

  const hits = await page.evaluate(() => {
    const out = [];
    for (const el of Array.from(document.querySelectorAll("*"))) {
      const own = Array.from(el.childNodes)
        .filter((n) => n.nodeType === 3)
        .map((n) => n.textContent || "")
        .join(" ");
      if (/did not respond/i.test(own)) {
        out.push({ tag: el.tagName, text: own.replace(/\s+/g, " ").trim().slice(0, 140) });
      }
    }
    return out;
  });

  console.log("SECTIONS REPORTING 'did not respond':");
  if (hits.length === 0) console.log("  none");
  for (const h of hits) console.log(`  [${h.tag}] ${h.text}`);
  console.log(`\nfailed API calls: ${failed.length ? failed.join(" | ") : "none"}`);
  console.log(`api calls seen: ${[...new Set(apiSeen)].join(" | ")}`);

  await browser.close();
})().catch((e) => {
  console.error("PROBE FAILED:", e.message);
  process.exit(1);
});
