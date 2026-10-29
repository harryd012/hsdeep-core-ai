import { test } from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";

import {
  ACCENTS,
  DEFAULT_APPEARANCE,
  THEMES,
  THEME_BOOT_SCRIPT,
  normalizeAppearance,
  parseStoredAppearance,
  resolveTheme,
} from "../src/lib/theme.ts";

test("defaults to light, comfortable, theme accent", () => {
  assert.deepEqual(DEFAULT_APPEARANCE, { theme: "light", accent: null, density: "comfortable" });
  assert.deepEqual(parseStoredAppearance(null), DEFAULT_APPEARANCE);
});

test("stored values are validated, unknown ones fall back", () => {
  assert.deepEqual(normalizeAppearance({ theme: "midnight", accent: "#0D9488", density: "compact" }), {
    theme: "midnight",
    accent: "#0d9488",
    density: "compact",
  });
  assert.deepEqual(normalizeAppearance({ theme: "neon", accent: "red; background:url(x)", density: "tiny" }), DEFAULT_APPEARANCE);
  assert.deepEqual(parseStoredAppearance("{not json"), DEFAULT_APPEARANCE);
});

test("the first theme switch's saved value still applies", () => {
  assert.equal(parseStoredAppearance(null, "dark").theme, "dark");
  assert.equal(parseStoredAppearance(null, "light").theme, "light");
  // The new key wins over the legacy one.
  assert.equal(parseStoredAppearance(JSON.stringify({ theme: "graphite" }), "dark").theme, "graphite");
});

test("match-system resolves from the OS preference", () => {
  assert.equal(resolveTheme("system", true), "dark");
  assert.equal(resolveTheme("system", false), "light");
  assert.equal(resolveTheme("contrast", true), "contrast");
});

test("every theme and accent offered is one the parser accepts", () => {
  for (const t of THEMES) assert.equal(normalizeAppearance({ theme: t.id }).theme, t.id);
  for (const a of ACCENTS) assert.equal(normalizeAppearance({ accent: a.value }).accent, a.value);
});

function runBoot(store, prefersDark = false) {
  const attrs = {};
  const props = {};
  const documentElement = {
    setAttribute: (k, v) => { attrs[k] = v; },
    style: { setProperty: (k, v) => { props[k] = v; } },
  };
  vm.runInNewContext(THEME_BOOT_SCRIPT, {
    document: { documentElement },
    localStorage: { getItem: (k) => (k in store ? store[k] : null) },
    window: { matchMedia: () => ({ matches: prefersDark }) },
    matchMedia: () => ({ matches: prefersDark }),
  });
  return { attrs, props };
}

test("boot script applies the saved appearance before paint", () => {
  const out = runBoot({ "hsdeep-appearance": JSON.stringify({ theme: "midnight", accent: "#7c3aed", density: "compact" }) });
  assert.deepEqual(out.attrs, { "data-theme": "midnight", "data-density": "compact" });
  assert.deepEqual(out.props, { "--ecc-accent": "#7c3aed", "--cyan": "#7c3aed" });
});

test("boot script: defaults, legacy key, system theme and bad input", () => {
  assert.equal(runBoot({}).attrs["data-theme"], "light");
  assert.equal(runBoot({ "hsdeep-theme": "dark" }).attrs["data-theme"], "dark");
  assert.equal(runBoot({ "hsdeep-appearance": JSON.stringify({ theme: "system" }) }, true).attrs["data-theme"], "dark");
  const bad = runBoot({ "hsdeep-appearance": JSON.stringify({ theme: "x", accent: "javascript:1" }) });
  assert.equal(bad.attrs["data-theme"], "light");
  assert.deepEqual(bad.props, {});
});
