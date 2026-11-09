/**
 * platformObservability.test.ts - the Platform Observability page's data and
 * navigation rules.
 *
 * Run with:  node --test tests/observability/platformObservability.test.ts
 *
 * Locks: only the five delivery labels exist; references are official HTTPS
 * docs; the Grafana link never carries credentials and is absent when unset;
 * the nav item is shown to platform superusers only; and the plan module holds
 * no secret-looking values (it ships in the public bundle).
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  DELIVERY_STATUS_LABEL,
  METRIC_CATALOGUE,
  PLAN_PHASES,
  REFERENCES,
  grafanaUrl,
  phaseCounts,
} from "../../src/lib/observability/platformObservabilityPlan.ts";
import { NAV_CONFIG, visibleNav } from "../../src/lib/navigation.ts";

const OFFICIAL_HOSTS = new Set(["prometheus.io", "prometheus.github.io", "grafana.com", "sre.google"]);

test("every phase uses one of the five delivery labels", () => {
  assert.deepEqual(Object.keys(DELIVERY_STATUS_LABEL).sort(), [
    "blocked", "implemented-not-live", "implemented-tested", "not-implemented", "partial",
  ]);
  for (const p of PLAN_PHASES) assert.ok(p.status in DELIVERY_STATUS_LABEL, p.id);
  assert.deepEqual(PLAN_PHASES.map((p) => p.id), ["P0", "P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8"]);
  const counts = phaseCounts();
  assert.equal(Object.values(counts).reduce((a, b) => a + b, 0), PLAN_PHASES.length);
});

test("references are official https documentation", () => {
  assert.ok(REFERENCES.length >= 10);
  for (const r of REFERENCES) {
    const url = new URL(r.url);
    assert.equal(url.protocol, "https:", r.url);
    assert.ok(OFFICIAL_HOSTS.has(url.hostname), r.url);
  }
});

test("catalogue names follow Prometheus naming and carry no tenant labels", () => {
  for (const m of METRIC_CATALOGUE) {
    assert.match(m.name, /^hsdeep_[a-z_]+$/);
    if (m.type === "counter") assert.ok(m.name.endsWith("_total"), m.name);
    assert.doesNotMatch(m.labels, /tenant|user|device|ip|connection/i, m.name);
  }
});

test("grafana link: absent when unset, never with credentials or odd schemes", () => {
  assert.equal(grafanaUrl(undefined), null);
  assert.equal(grafanaUrl("   "), null);
  assert.equal(grafanaUrl("not a url"), null);
  assert.equal(grafanaUrl("javascript:alert(1)"), null);
  assert.equal(grafanaUrl("https://admin:secret@grafana.internal"), null);
  assert.equal(grafanaUrl("https://grafana.internal/d/platform"), "https://grafana.internal/d/platform");
});

test("platform observability nav is for superusers only", () => {
  const item = NAV_CONFIG.find((g) => g.id === "platform-observability");
  assert.ok(item, "nav item exists");
  assert.equal(item.route, "/observability");
  assert.equal(item.superuserOnly, true);

  const everyPermission = [...new Set(NAV_CONFIG.map((g) => g.permission).filter(Boolean))] as string[];
  const tenantAdmin = visibleNav({ is_superuser: false, permissions: everyPermission });
  assert.ok(!tenantAdmin.some((g) => g.id === "platform-observability"));
  assert.ok(tenantAdmin.some((g) => g.id === "dashboard"), "other items unaffected");

  const superuser = visibleNav({ is_superuser: true, permissions: [] });
  assert.ok(superuser.some((g) => g.id === "platform-observability"));
});

test("plan module and page hold no secret-looking values", () => {
  for (const file of [
    "src/lib/observability/platformObservabilityPlan.ts",
    "src/app/(dashboard)/observability/page.tsx",
  ]) {
    const text = readFileSync(new URL(`../../${file}`, import.meta.url), "utf8");
    assert.doesNotMatch(text, /METRICS_BEARER_TOKEN\s*=|NEXT_PUBLIC_[A-Z_]*(TOKEN|PASSWORD|SECRET)/, file);
    assert.doesNotMatch(text, /Bearer [A-Za-z0-9._-]{12,}/, file);
  }
});
