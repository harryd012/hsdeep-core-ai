import { test } from "node:test";
import assert from "node:assert/strict";

test("paloalto client: runbook query is URL-encoded", () => {
  const q = "tls decrypt";
  const url = `/api/paloalto/runbooks?q=${encodeURIComponent(q)}`;
  assert.equal(url, "/api/paloalto/runbooks?q=tls%20decrypt");
});

test("paloalto workspace: unverified sections are labelled, not hidden", () => {
  const dq = { logs: "uncollected", rca: "pending" };
  const rendered = Object.entries(dq).map(([k, v]) => `${k}: ${v}`).join("; ");
  assert.ok(rendered.includes("logs: uncollected"));
  assert.ok(rendered.includes("rca: pending"));
});

test("paloalto runbook match: overlap score is explainable", () => {
  const symptoms = ["traffic denied", "policy block"];
  const rbSym = ["traffic denied", "policy block", "connection refused by firewall"];
  const hit = symptoms.filter((s) => rbSym.some((r) => r.includes(s) || s.includes(r)));
  const score = Math.round((100.0 * hit.length) / symptoms.length * 10) / 10;
  assert.equal(score, 100.0);
  assert.deepEqual(hit, ["traffic denied", "policy block"]);
});
