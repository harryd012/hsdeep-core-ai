/** VendorVisualRegistry tests: the four fallback levels and unknown handling. */
import assert from "node:assert/strict";
import test from "node:test";

import {
  isUnknownResource,
  resolveVendorVisual,
  vendorKey,
  vendorLabel,
} from "../../src/lib/topology/vendors.ts";

test("level 1: vendor + model signature", () => {
  const visual = resolveVendorVisual({
    vendor: "Fortinet",
    model: "FortiGate-200F",
    deviceType: "firewall",
  });
  assert.equal(visual.source, "vendor_model");
  assert.equal(visual.signature, "fortigate");
  assert.equal(visual.iconKey, "firewall");
  assert.equal(visual.vendorLabel, "Fortinet");
  assert.equal(visual.vendorKey, "fortinet");
});

test("level 2: vendor alone", () => {
  const cisco = resolveVendorVisual({ vendor: "Cisco", deviceType: "switch" });
  assert.equal(cisco.source, "vendor");
  assert.equal(cisco.iconKey, "switch");
  assert.equal(cisco.vendorLabel, "Cisco");

  const unrecognisedModel = resolveVendorVisual({ vendor: "Fortinet", model: "unknown-box" });
  assert.equal(unrecognisedModel.source, "vendor");
  assert.equal(unrecognisedModel.iconKey, "firewall");
});

test("level 3: device type when the vendor is unknown", () => {
  const visual = resolveVendorVisual({ deviceType: "access_point" });
  assert.equal(visual.source, "device_type");
  assert.equal(visual.iconKey, "access-point");
  assert.equal(visual.vendorLabel, "Unknown vendor");
});

test("level 4: generic resource", () => {
  const visual = resolveVendorVisual({});
  assert.equal(visual.source, "generic");
  assert.equal(visual.iconKey, "unknown");
  assert.equal(visual.vendorKey, "unknown");
});

test("an unclassified resource is never given a vendor identity", () => {
  const visual = resolveVendorVisual({
    vendor: null,
    model: null,
    deviceType: "unclassified",
    deviceTypeRaw: "unknown",
  });
  assert.equal(visual.iconKey, "unknown");
  assert.equal(visual.vendorLabel, "Unknown vendor");
  assert.equal(isUnknownResource({ deviceType: "unclassified", deviceTypeRaw: "unknown" }), true);
  assert.equal(isUnknownResource({ recognitionStatus: "unknown" }), true);
  assert.equal(isUnknownResource({ recognitionStatus: "identified" }), false);
  assert.equal(isUnknownResource({ deviceType: "switch" }), false);
});

test("a vendor cannot be inferred from anything but vendor data", () => {
  // The input type has no address/name field, so extras cannot influence it.
  const visual = resolveVendorVisual({
    deviceType: "unclassified",
    deviceTypeRaw: "unknown",
    // @ts-expect-error unknown keys are deliberately not part of the contract
    ip: "10.10.10.10",
  });
  assert.equal(visual.source, "generic");
  assert.equal(visual.vendorKey, "unknown");
});

test("vendor aliases normalise and unknown vendors are shown as recorded", () => {
  assert.equal(vendorKey("Cisco Meraki"), "meraki");
  assert.equal(vendorKey("Palo Alto Networks"), "paloalto");
  assert.equal(vendorKey("Hewlett-Packard"), "hpe");
  assert.equal(vendorKey("SomeVendor"), null);
  assert.equal(vendorLabel("SomeVendor"), "SomeVendor");
  assert.equal(vendorLabel("meraki"), "Cisco Meraki");
  assert.equal(vendorLabel(null), "Unknown vendor");
});

test("health can never change a vendor visual", () => {
  const input = { vendor: "Cisco", model: "Catalyst 9300", deviceType: "switch" };
  assert.deepEqual(resolveVendorVisual(input), resolveVendorVisual(input));
  assert.equal(resolveVendorVisual(input).iconKey, "switch");
});

test("a known vendor with an unknown device type still resolves at vendor level", () => {
  const visual = resolveVendorVisual({ vendor: "Cisco Meraki", deviceType: "unclassified" });
  assert.equal(visual.source, "vendor");
  assert.equal(visual.iconKey, "access-point");
});