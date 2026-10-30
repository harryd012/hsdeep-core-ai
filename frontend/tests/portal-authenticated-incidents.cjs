/* Authenticated Portal incident validation (real browser, real API).
 * node tests/portal-authenticated-incidents.cjs
 *
 * 1. Logs in through the real /api/auth/login and injects the SAME localStorage
 *    token the login flow writes (the app's existing session mechanism).
 * 2. Loads the Portal routes and reports rendered state, console/page errors and
 *    failed API calls - including whether the "Unavailable" state appears.
 * 3. Times the authenticated /api/incidents endpoint IN THE BROWSER, 3 reps each
 *    for limit=100 and limit=25.
 *
 * Nothing is mocked and no failure is hidden.
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
const ROUTES = ["/portal", "/portal/operations", "/portal/resources/incidents"];

async function login() {
  const res = await fetch(`${API}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: EMAIL, password: PASSWORD }),
  });
  if (!res.ok) throw new Error(`login failed: ${res.status} ${res.statusText}`);
  return res.json();
}

function ms(t) {
  return t === -1 ? null : Math.round(t);
}

(async () => {
  const auth = await login();
  console.log(`login: 200 (token ${String(auth.access_token).slice(0, 10)}...)`);

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await context.addInitScript(
    ([token, refresh]) => {
      window.localStorage.setItem("hsdeep_auth_token", token);
      if (refresh) window.localStorage.setItem("hsdeep_refresh_token", refresh);
    },
    [auth.access_token, auth.refresh_token || ""],
  );

  const page = await context.newPage();
  const consoleErrors = [];
  const pageErrors = [];
  const failed = [];
  const api = [];
  page.on("pageerror", (e) => pageErrors.push(e.message));
  page.on("console", (m) => {
    if (m.type() === "error") consoleErrors.push(m.text());
  });
  page.on("response", (r) => {
    const url = r.url();
    if (!/\/api\//.test(url)) return;
    const t = r.request().timing();
    api.push({
      path: url.replace(API, ""),
      status: r.status(),
      ms: ms((t.responseEnd ?? -1) - (t.requestStart ?? -1)),
    });
    if (r.status() >= 400) failed.push(`${r.status()} ${url.replace(API, "")}`);
  });

  for (const route of ROUTES) {
    await page.goto(`${UI}${route}`, { waitUntil: "domcontentloaded", timeout: 60000 });
    await page.waitForTimeout(14000); // allow the 5 parallel calls (and any timeout) to settle
    const info = await page.evaluate(() => {
      const text = document.body.innerText || "";
      const rows = document.querySelectorAll("table tbody tr").length;
      return {
        unavailableIncidents: text.includes("the incidents API did not respond"),
        unavailableAlerts: text.includes("the alerts API did not respond"),
        shell: text.includes("AUTONOMOUS INFRASTRUCTURE INTELLIGENCE"),
        tableRows: rows,
        hasActiveIncidents: text.includes("ACTIVE INCIDENTS"),
        hasAiSummary: text.includes("AI OPERATIONS SUMMARY"),
        snippet: text.replace(/\s+/g, " ").slice(0, 200),
      };
    });
    console.log(`\nROUTE ${route}`);
    console.log(
      `  shell=${info.shell} activeIncidentsSection=${info.hasActiveIncidents} aiSummary=${info.hasAiSummary} tableRows=${info.tableRows}`,
    );
    console.log(
      `  unavailable: incidents=${info.unavailableIncidents} alerts=${info.unavailableAlerts}`,
    );
    console.log(`  snippet: ${info.snippet}`);
  }

  const timings = await page.evaluate(async (apiBase) => {
    const token = window.localStorage.getItem("hsdeep_auth_token");
    const out = {};
    for (const limit of [100, 25]) {
      const runs = [];
      let rows = 0;
      for (let i = 0; i < 3; i += 1) {
        const t0 = performance.now();
        const r = await fetch(`${apiBase}/api/incidents?limit=${limit}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        const body = await r.json();
        runs.push(Math.round(performance.now() - t0));
        rows = Array.isArray(body) ? body.length : 0;
        out.status = r.status;
      }
      const sorted = [...runs].sort((a, b) => a - b);
      out[`limit${limit}`] = {
        runs,
        min: sorted[0],
        max: sorted[sorted.length - 1],
        avg: Math.round(runs.reduce((a, b) => a + b, 0) / runs.length),
        rows,
      };
    }
    return out;
  }, API);

  console.log("\nAPI TIMINGS (in-browser, authenticated)");
  console.log(`  status=${timings.status}`);
  for (const key of ["limit100", "limit25"]) {
    const t = timings[key];
    console.log(
      `  ${key}: min=${t.min}ms max=${t.max}ms avg=${t.avg}ms rows=${t.rows} runs=[${t.runs.join(", ")}]`,
    );
  }

  console.log("\nPAGE API CALLS OBSERVED");
  for (const c of api) console.log(`  ${c.status} ${c.path} ${c.ms ?? "?"}ms`);
  console.log(`\nfailed API calls: ${failed.length ? failed.join(" | ") : "none"}`);
  console.log(`console errors: ${consoleErrors.length ? consoleErrors.join(" | ") : "none"}`);
  console.log(`page errors: ${pageErrors.length ? pageErrors.join(" | ") : "none"}`);

  await browser.close();
})().catch((e) => {
  console.error("VALIDATION FAILED:", e.message);
  process.exit(1);
});
