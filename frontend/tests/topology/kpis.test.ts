/** KPI strip model tests (Phase 9.7A): real values, no invented deltas. */
import assert from "node:assert/strict";
import test from "node:test";

import { attentionKpis, buildKpis, type KpiSummaryInput } from "../../src/lib/topology/kpis.ts";

/** The shape the live read model actually returns for this deployment. */
const SUMMARY: KpiSummaryInput = {
  devices: 265,
  online: 11,
  warning: 0,
  offline: 254,
  unknown: 0,
  stale: 0,
  maintenance: 0,
  links: 0,
  interfacesTotal: 0,
  unidentified: 265,
  trafficMeasured: false,
  rxLabel: null,
  txLabel: null,
};

test("the strip carries exactly the backend values", () => {
  const cards = buildKpis(SUMMARY);
  const byKey = new Map(cards.map((card) => [card.key, card]));

  assert.equal(cards.length, 10);
  assert.equal(byKey.get("devices")!.display, "265");
  assert.equal(byKey.get("online")!.display, "11");
  assert.equal(byKey.get("offline")!.display, "254");
  assert.equal(byKey.get("links")!.value, 0);
  assert.equal(byKey.get("interfaces")!.value, 0);
  assert.equal(byKey.get("unidentified")!.display, "265");
});

test("no percentage change is ever invented", () => {
  for (const card of buildKpis(SUMMARY)) {
    assert.equal(card.delta, null, card.key);
  }
});

test("unmeasured traffic is not a zero", () => {
  const traffic = buildKpis(SUMMARY).find((card) => card.key === "traffic")!;
  assert.equal(traffic.value, null);
  assert.equal(traffic.display, "No telemetry");
  assert.equal(traffic.tone, "muted");
  assert.match(traffic.tooltip, /have ever been collected/);
});

test("measured traffic shows both directions", () => {
  const traffic = buildKpis({
    ...SUMMARY,
    trafficMeasured: true,
    rxLabel: "1.2 Gbps",
    txLabel: "950 Mbps",
  }).find((card) => card.key === "traffic")!;
  assert.equal(traffic.display, "1.2 Gbps ↓ / 950 Mbps ↑");
  assert.equal(traffic.tone, "neutral");
});

test("every card has a semantic tone, an icon and an accessible tooltip", () => {
  const cards = buildKpis(SUMMARY);
  for (const card of cards) {
    assert.ok(card.label.length > 0, card.key);
    assert.ok(card.tooltip.length > 10, card.key);
    assert.ok(card.iconKey.length > 0, card.key);
    assert.ok(["neutral", "good", "warn", "bad", "muted"].includes(card.tone), card.key);
  }
  const tones = Object.fromEntries(cards.map((card) => [card.key, card.tone]));
  assert.equal(tones.online, "good");
  assert.equal(tones.warning, "warn");
  assert.equal(tones.offline, "bad");
  assert.equal(tones.unknown, "muted");
  assert.equal(tones.stale, "muted");
});

test("the card order is stable across refreshes", () => {
  const first = buildKpis(SUMMARY).map((card) => card.key);
  const second = buildKpis({ ...SUMMARY, devices: 999 }).map((card) => card.key);
  assert.deepEqual(first, second);
});

test("attention cards need a non-zero count as well as a bad tone", () => {
  // Warning is 0 in this deployment, so only the real outage is surfaced.
  assert.deepEqual(
    attentionKpis(buildKpis(SUMMARY)).map((card) => card.key),
    ["offline"]
  );
  assert.deepEqual(
    attentionKpis(buildKpis({ ...SUMMARY, warning: 3, offline: 0 })).map((card) => card.key),
    ["warning"]
  );
});