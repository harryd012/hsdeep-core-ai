/**
 * VendorVisualRegistry (Phase 9.5).
 *
 * Resolves "which icon/label does this resource get" from vendor + model +
 * canonical device type, in ONE place. React components never branch on a vendor
 * name; they read what this registry returns.
 *
 * Resolution order — the four levels the product specifies, and the only order
 * this module can take (`source` reports which level matched):
 *
 *   1. vendor + model signature   e.g. Fortinet + FortiGate-200F
 *   2. vendor                     e.g. Cisco, model unknown
 *   3. device type                e.g. switch, vendor unknown
 *   4. generic resource           nothing recognised
 *
 * Deliberate non-features:
 *   * No network call to any external site for a logo. Visuals are icon KEYS
 *     mapped to the app's existing bundled icon set by the component layer, so an
 *     unavailable asset can never break the canvas.
 *   * `VendorVisualInput` has no address and no name field. A vendor cannot be
 *     inferred from an IP, a hostname or a device label because the resolver is
 *     structurally incapable of seeing them.
 *   * Health never recolours vendor identity. The icon is constant; only the
 *     status indicator changes.
 */

export type VisualIconKey =
  | "firewall"
  | "router"
  | "switch"
  | "access-point"
  | "wireless-controller"
  | "server"
  | "virtual-machine"
  | "cloud"
  | "storage"
  | "endpoint"
  | "application"
  | "unknown";

export type VisualSource = "vendor_model" | "vendor" | "device_type" | "generic";

export interface VendorVisual {
  /** Normalised vendor token, or "other"/"unknown" when nothing matched. */
  vendorKey: string;
  /** What to print: the RECORDED vendor string, else "Unknown vendor". */
  vendorLabel: string;
  iconKey: VisualIconKey;
  /** The canonical device type this visual was resolved for (echoed, not judged). */
  deviceType: string | null;
  source: VisualSource;
  /** Present only at level 1 — the model signature that decided it. */
  signature?: string;
}

const UNKNOWN_TYPES = new Set(["", "unknown", "unclassified", "other", "generic"]);

/** Recorded vendor spelling → canonical vendor token. */
const VENDOR_ALIASES: Record<string, string> = {
  fortinet: "fortinet",
  fortigate: "fortinet",
  "fortinet inc": "fortinet",
  cisco: "cisco",
  "cisco systems": "cisco",
  meraki: "meraki",
  "cisco meraki": "meraki",
  aruba: "aruba",
  "aruba networks": "aruba",
  "hpe aruba": "aruba",
  "palo alto": "paloalto",
  "palo alto networks": "paloalto",
  paloaltonetworks: "paloalto",
  vmware: "vmware",
  microsoft: "microsoft",
  azure: "microsoft",
  "microsoft azure": "microsoft",
  amazon: "aws",
  aws: "aws",
  "amazon web services": "aws",
  google: "gcp",
  gcp: "gcp",
  "google cloud": "gcp",
  juniper: "juniper",
  "juniper networks": "juniper",
  hpe: "hpe",
  "hewlett packard": "hpe",
  "hewlett-packard": "hpe",
  ubiquiti: "ubiquiti",
  dell: "dell",
  netgear: "netgear",
};

const VENDOR_LABELS: Record<string, string> = {
  fortinet: "Fortinet",
  cisco: "Cisco",
  meraki: "Cisco Meraki",
  aruba: "Aruba",
  paloalto: "Palo Alto Networks",
  vmware: "VMware",
  microsoft: "Microsoft",
  aws: "Amazon Web Services",
  gcp: "Google Cloud",
  juniper: "Juniper",
  hpe: "HPE",
  ubiquiti: "Ubiquiti",
  dell: "Dell",
  netgear: "NETGEAR",
};

/** Level 1: a model series that identifies the product family on its own. */
const MODEL_SIGNATURES: readonly {
  vendorKey: string;
  needles: readonly string[];
  iconKey: VisualIconKey;
  signature: string;
}[] = [
  { vendorKey: "fortinet", needles: ["fortigate"], iconKey: "firewall", signature: "fortigate" },
  { vendorKey: "fortinet", needles: ["fortiswitch"], iconKey: "switch", signature: "fortiswitch" },
  { vendorKey: "fortinet", needles: ["fortiap"], iconKey: "access-point", signature: "fortiap" },
  {
    vendorKey: "cisco",
    needles: ["catalyst", "c9300", "c9200", "c9500"],
    iconKey: "switch",
    signature: "catalyst",
  },
  { vendorKey: "cisco", needles: ["nexus"], iconKey: "switch", signature: "nexus" },
  { vendorKey: "cisco", needles: ["isr4", "asr1"], iconKey: "router", signature: "cisco-edge-router" },
  { vendorKey: "paloalto", needles: ["pa-"], iconKey: "firewall", signature: "pa-series" },
  {
    vendorKey: "aruba",
    needles: ["aruba"],
    iconKey: "wireless-controller",
    signature: "aruba-controller",
  },
  {
    vendorKey: "vmware",
    needles: ["esxi", "vsphere", "vmware"],
    iconKey: "virtual-machine",
    signature: "esxi",
  },
  { vendorKey: "meraki", needles: ["meraki"], iconKey: "access-point", signature: "meraki" },
];

/** Level 2: a vendor with no model detail — the vendor's primary product shape. */
const VENDOR_ICONS: Record<string, VisualIconKey> = {
  fortinet: "firewall",
  cisco: "switch",
  meraki: "access-point",
  aruba: "wireless-controller",
  paloalto: "firewall",
  vmware: "virtual-machine",
  microsoft: "cloud",
  aws: "cloud",
  gcp: "cloud",
  juniper: "router",
  hpe: "switch",
  ubiquiti: "access-point",
  dell: "server",
  netgear: "switch",
};

/** Level 3: canonical device type → icon. */
const DEVICE_TYPE_ICONS: Record<string, VisualIconKey> = {
  firewall: "firewall",
  security_gateway: "firewall",
  router: "router",
  switch: "switch",
  network_device: "switch",
  network_appliance: "switch",
  access_point: "access-point",
  wireless_controller: "wireless-controller",
  server: "server",
  virtual_machine: "virtual-machine",
  hypervisor: "server",
  container_host: "server",
  storage_system: "storage",
  cloud_resource: "cloud",
  cloud_service: "cloud",
  cloud_subscription: "cloud",
  cloud_account: "cloud",
  cloud_platform: "cloud",
  cloud_database: "cloud",
  cloud_storage: "storage",
  cloud_network: "cloud",
  endpoint: "endpoint",
  workstation: "endpoint",
  laptop: "endpoint",
  mobile_device: "endpoint",
  iot_device: "endpoint",
  application: "application",
  service: "application",
  api: "application",
  sensor: "endpoint",
};

export interface VendorVisualInput {
  vendor?: string | null;
  model?: string | null;
  /** Canonical device type from the graph (`unclassified` when unrecognised). */
  deviceType?: string | null;
  deviceTypeRaw?: string | null;
}

/**
 * True when the platform never classified this resource.
 *
 * Drives the "Unknown Resource" presentation and suppresses vendor branding. It
 * is the frontend's echo of the backend's recognition rule, not a second opinion:
 * `recognitionStatus` is the backend's token and wins when it is supplied.
 */
export function isUnknownResource(input: {
  deviceType?: string | null;
  deviceTypeRaw?: string | null;
  recognitionStatus?: string | null;
}): boolean {
  if (input.recognitionStatus) {
    return input.recognitionStatus.toLowerCase() === "unknown";
  }
  const canonical = (input.deviceType ?? "").trim().toLowerCase();
  const raw = (input.deviceTypeRaw ?? "").trim().toLowerCase();
  return UNKNOWN_TYPES.has(canonical) && UNKNOWN_TYPES.has(raw);
}

/** Canonical vendor token for a recorded vendor string, or null. */
export function vendorKey(vendor: string | null | undefined): string | null {
  const token = (vendor ?? "").trim().toLowerCase();
  if (!token) return null;
  return VENDOR_ALIASES[token] ?? null;
}

/** Label to print for a recorded vendor: the recorded string, never a guess. */
export function vendorLabel(vendor: string | null | undefined): string {
  const recorded = (vendor ?? "").trim();
  if (!recorded) return "Unknown vendor";
  const key = vendorKey(recorded);
  return key ? (VENDOR_LABELS[key] ?? recorded) : recorded;
}

/**
 * Resolve the visual identity for one resource, applying the four levels in
 * order. Always returns a usable visual, so the canvas can never fail to draw a
 * node because an asset or a vendor is missing.
 */
export function resolveVendorVisual(input: VendorVisualInput): VendorVisual {
  const deviceType = (input.deviceType ?? "").trim().toLowerCase() || null;
  const recordedVendor = (input.vendor ?? "").trim();
  const key = vendorKey(recordedVendor);
  const model = (input.model ?? "").trim().toLowerCase();

  // 1. vendor + model signature
  if (key && model) {
    for (const candidate of MODEL_SIGNATURES) {
      if (candidate.vendorKey !== key) continue;
      if (candidate.needles.some((needle) => model.includes(needle))) {
        return {
          vendorKey: key,
          vendorLabel: vendorLabel(recordedVendor),
          iconKey: candidate.iconKey,
          deviceType,
          source: "vendor_model",
          signature: candidate.signature,
        };
      }
    }
  }

  // 2. vendor alone
  if (key && VENDOR_ICONS[key]) {
    return {
      vendorKey: key,
      vendorLabel: vendorLabel(recordedVendor),
      iconKey: VENDOR_ICONS[key],
      deviceType,
      source: "vendor",
    };
  }

  // 3. device type
  if (deviceType && DEVICE_TYPE_ICONS[deviceType]) {
    return {
      vendorKey: key ?? "other",
      vendorLabel: vendorLabel(recordedVendor),
      iconKey: DEVICE_TYPE_ICONS[deviceType],
      deviceType,
      source: "device_type",
    };
  }

  // 4. generic resource
  return {
    vendorKey: key ?? "unknown",
    vendorLabel: vendorLabel(recordedVendor),
    iconKey: "unknown",
    deviceType,
    source: "generic",
  };
}
