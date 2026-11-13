import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  MOBILE_NAV_MAX_WIDTH,
  MOBILE_NAV_QUERY,
  collapseButtonRole,
  drawerReducer,
} from "../src/lib/mobile-nav.ts";

test("drawer toggles open and closed", () => {
  assert.equal(drawerReducer(false, { type: "toggle" }), true);
  assert.equal(drawerReducer(true, { type: "toggle" }), false);
});

test("close, navigation and Escape close the drawer", () => {
  assert.equal(drawerReducer(true, { type: "close" }), false);
  assert.equal(drawerReducer(true, { type: "navigate" }), false);
  assert.equal(drawerReducer(true, { type: "key", key: "Escape" }), false);
  assert.equal(drawerReducer(true, { type: "key", key: "Enter" }), true);
});

test("collapse button closes the drawer on a phone and toggles the rail on desktop", () => {
  assert.deepEqual(collapseButtonRole(true, true), { action: "close", label: "Close navigation" });
  assert.deepEqual(collapseButtonRole(false, false), { action: "rail", label: "Collapse navigation" });
  assert.deepEqual(collapseButtonRole(false, true), { action: "rail", label: "Expand navigation" });
});

test("CSS drawer breakpoint matches the JS media query", () => {
  assert.equal(MOBILE_NAV_QUERY, `(max-width: ${MOBILE_NAV_MAX_WIDTH}px)`);
  const css = readFileSync(new URL("../src/app/styles/navigation.css", import.meta.url), "utf8");
  assert.ok(css.includes(`@media ${MOBILE_NAV_QUERY}`), "navigation.css must use the drawer breakpoint");
  assert.ok(css.includes(`@media (min-width: ${MOBILE_NAV_MAX_WIDTH + 1}px)`), "collapsed rail must be desktop-only");
});
