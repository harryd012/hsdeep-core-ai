import { test } from "node:test";
import assert from "node:assert/strict";

import { formatBytes, toCsv } from "../src/lib/csv.ts";

const cols = [
  { header: "Name", value: (r) => r.name },
  { header: "Points", value: (r) => r.points },
];

test("toCsv writes a header and one CRLF line per row; null is empty", () => {
  assert.equal(toCsv(cols, [{ name: "web-01", points: 30 }, { name: "db-01", points: null }]), "Name,Points\r\nweb-01,30\r\ndb-01,\r\n");
});

test("toCsv quotes commas, quotes and newlines", () => {
  assert.equal(toCsv(cols, [{ name: 'a,"b"\nc', points: 1 }]), 'Name,Points\r\n"a,""b""\nc",1\r\n');
});

test("toCsv never lets a text cell run as a spreadsheet formula; numbers stay numbers", () => {
  assert.equal(toCsv(cols, [{ name: "=HYPERLINK(1)", points: -5 }]), "Name,Points\r\n'=HYPERLINK(1),-5\r\n");
  assert.equal(toCsv(cols, [{ name: "-cmd", points: 0 }]), "Name,Points\r\n'-cmd,0\r\n");
});

test("formatBytes uses decimal units and says when nothing is reported", () => {
  assert.equal(formatBytes(null), "—");
  assert.equal(formatBytes(512), "512 B");
  assert.equal(formatBytes(2_000_000_000), "2.0 GB");
  assert.equal(formatBytes(1_500_000_000_000), "1.5 TB");
});
