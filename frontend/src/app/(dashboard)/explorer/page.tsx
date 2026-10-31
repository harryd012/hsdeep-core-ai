"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Search,
  RefreshCw,
  Loader2,
  ChevronLeft,
  ChevronRight,
  X,
  ArrowUpDown,
  ArrowUp,
  ArrowDown,
} from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import {
  listAssetDevices,
  listAssetSites,
  listSensors,
  listMonitoringSources,
  getAssetDeviceRelationships,
  type AssetDevice,
  type AssetSite,
  type SensorOut,
  type MonitoringSourceOut,
  type AssetRelationship,
  getTenantId,
} from "@/lib/api";
import FreshnessIndicator from "@/components/shared/FreshnessIndicator";
import DataStateView from "@/components/shared/DataStateView";
import type { DataStateErrorKind } from "@/lib/observability/observabilityState";

/**
 * Service & Infrastructure Explorer.
 *
 * A searchable, sortable, filterable, paginated entity explorer over the
 * entity types the data model actually represents: devices, sites, sensors
 * and monitoring sources. Every value comes from a real backend API; nothing
 * is invented. Where an API offers server-side search/filter/pagination
 * (devices, sites) it is used directly; where it does not (sensors, sources)
 * the page filters/sorts/pagines client-side over the fetched page and says so.
 *
 * States are rendered through the shared DataStateView so loading/empty/stale/
 * error/unavailable are honest and consistent across the product.
 */

type EntityKind = "devices" | "sites" | "sensors" | "sources";

const ENTITY_TABS: { id: EntityKind; label: string }[] = [
  { id: "devices", label: "Devices" },
  { id: "sites", label: "Sites" },
  { id: "sensors", label: "Sensors" },
  { id: "sources", label: "Monitoring Sources" },
];

const PAGE_SIZE = 25;

// ---------------------------------------------------------------------------
// Status helpers (shared vocabulary, never invented values)
// ---------------------------------------------------------------------------

const STATUS_COLOR: Record<string, string> = {
  up: "var(--green)",
  online: "var(--green)",
  connected: "var(--green)",
  operational: "var(--green)",
  active: "var(--green)",
  healthy: "var(--green)",
  warning: "var(--amber)",
  degraded: "var(--amber)",
  partial: "var(--amber)",
  stale: "var(--amber)",
  down: "var(--red)",
  offline: "var(--red)",
  error: "var(--red)",
  failed: "var(--red)",
  critical: "var(--red)",
  unknown: "var(--muted)",
  unreachable: "var(--muted)",
};

function statusColor(status: string | null | undefined): string {
  if (!status) return "var(--muted)";
  return STATUS_COLOR[status.toLowerCase()] ?? "var(--muted)";
}

/** Coarse, non-sensitive error kind from a thrown fetch error. */
function classifyError(err: unknown): DataStateErrorKind {
  const msg = err instanceof Error ? err.message : String(err);
  if (/401|unauthor/i.test(msg)) return "unauthorized";
  if (/403|forbidden/i.test(msg)) return "forbidden";
  if (/404|not found/i.test(msg)) return "not_found";
  if (/429|rate/i.test(msg)) return "rate_limited";
  if (/timeout|timed out/i.test(msg)) return "timeout";
  if (/5\d\d|server/i.test(msg)) return "server";
  if (/network|fetch|dns|connection/i.test(msg)) return "network";
  return "unknown";
}

// ---------------------------------------------------------------------------
// Column model — one per entity kind. A column is either a direct field
// accessor or a render function. Sortable columns declare a sortKey.
// ---------------------------------------------------------------------------

interface Column<T> {
  key: string;
  label: string;
  sortKey?: (row: T) => string | number | null;
  render: (row: T) => React.ReactNode;
  width?: number;
}

type SortDir = "asc" | "desc";

function SortHeader<T>({
  column,
  sortKey,
  sortDir,
  onSort,
}: {
  column: Column<T>;
  sortKey: string | null;
  sortDir: SortDir;
  onSort: (key: string) => void;
}) {
  const active = sortKey === column.key;
  const Icon = !column.sortKey
    ? null
    : active
      ? sortDir === "asc"
        ? ArrowUp
        : ArrowDown
      : ArrowUpDown;
  return (
    <th
      style={{
        textAlign: "left",
        padding: "10px 12px",
        color: "var(--cyan)",
        fontSize: 11,
        fontWeight: 600,
        letterSpacing: 0.4,
        textTransform: "uppercase",
        whiteSpace: "nowrap",
      }}
    >
      <button
        type="button"
        disabled={!column.sortKey}
        onClick={() => column.sortKey && onSort(column.key)}
        style={{
          background: "transparent",
          border: 0,
          color: "inherit",
          font: "inherit",
          textTransform: "inherit",
          letterSpacing: "inherit",
          cursor: column.sortKey ? "pointer" : "default",
          display: "inline-flex",
          alignItems: "center",
          gap: 4,
          padding: 0,
        }}
      >
        {column.label}
        {Icon && <Icon size={11} style={{ opacity: active ? 1 : 0.5 }} />}
      </button>
    </th>
  );
}

// ---------------------------------------------------------------------------
// Devices
// ---------------------------------------------------------------------------

const DEVICE_COLUMNS: Column<AssetDevice>[] = [
  {
    key: "name",
    label: "Name",
    sortKey: (d) => d.name,
    render: (d) => (
      <span style={{ fontWeight: 600, color: "var(--fg)" }}>{d.name}</span>
    ),
  },
  {
    key: "device_type",
    label: "Type",
    sortKey: (d) => d.device_type,
    render: (d) => (
      <span style={{ color: "var(--muted)" }}>{d.device_type || "—"}</span>
    ),
  },
  {
    key: "status",
    label: "Status",
    sortKey: (d) => d.status,
    render: (d) => (
      <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
        <i
          style={{
            width: 7,
            height: 7,
            borderRadius: "50%",
            background: statusColor(d.status),
            display: "inline-block",
          }}
        />
        <span style={{ color: statusColor(d.status) }}>{d.status || "unknown"}</span>
      </span>
    ),
  },
  {
    key: "site_id",
    label: "Site",
    sortKey: (d) => d.site_id ?? "",
    render: (d) => (
      <span style={{ color: "var(--muted)", fontFamily: "monospace", fontSize: 11 }}>
        {d.site_id ? d.site_id.slice(0, 8) + "…" : "—"}
      </span>
    ),
  },
  {
    key: "vendor",
    label: "Vendor",
    sortKey: (d) => d.vendor ?? "",
    render: (d) => <span style={{ color: "var(--muted)" }}>{d.vendor || "—"}</span>,
  },
  {
    key: "ip_address",
    label: "IP",
    sortKey: (d) => d.ip_address ?? "",
    render: (d) => (
      <span style={{ color: "var(--muted)", fontFamily: "monospace", fontSize: 11 }}>
        {d.ip_address || "—"}
      </span>
    ),
  },
  {
    key: "criticality",
    label: "Criticality",
    sortKey: (d) => d.criticality ?? "",
    render: (d) => (
      <span style={{ color: d.criticality ? "var(--amber)" : "var(--muted)" }}>
        {d.criticality || "—"}
      </span>
    ),
  },
  {
    key: "last_seen_at",
    label: "Last seen",
    sortKey: (d) => d.last_seen_at ?? "",
    render: (d) => (
      <FreshnessIndicator lastCollectedAt={d.last_seen_at} showAge compact />
    ),
  },
];

// ---------------------------------------------------------------------------
// Sites
// ---------------------------------------------------------------------------

const SITE_COLUMNS: Column<AssetSite>[] = [
  {
    key: "name",
    label: "Name",
    sortKey: (s) => s.name,
    render: (s) => (
      <span style={{ fontWeight: 600, color: "var(--fg)" }}>{s.name}</span>
    ),
  },
  {
    key: "code",
    label: "Code",
    sortKey: (s) => s.code,
    render: (s) => (
      <span style={{ color: "var(--cyan)", fontFamily: "monospace", fontSize: 11 }}>
        {s.code}
      </span>
    ),
  },
  {
    key: "region",
    label: "Region",
    sortKey: (s) => s.region ?? "",
    render: (s) => <span style={{ color: "var(--muted)" }}>{s.region || "—"}</span>,
  },
  {
    key: "criticality",
    label: "Criticality",
    sortKey: (s) => s.criticality ?? "",
    render: (s) => (
      <span style={{ color: s.criticality ? "var(--amber)" : "var(--muted)" }}>
        {s.criticality || "—"}
      </span>
    ),
  },
  {
    key: "building",
    label: "Building",
    sortKey: (s) => s.building ?? "",
    render: (s) => <span style={{ color: "var(--muted)" }}>{s.building || "—"}</span>,
  },
  {
    key: "site_owner",
    label: "Owner",
    sortKey: (s) => s.site_owner ?? "",
    render: (s) => <span style={{ color: "var(--muted)" }}>{s.site_owner || "—"}</span>,
  },
  {
    key: "timezone",
    label: "Timezone",
    sortKey: (s) => s.timezone ?? "",
    render: (s) => <span style={{ color: "var(--muted)" }}>{s.timezone || "—"}</span>,
  },
];

// ---------------------------------------------------------------------------
// Sensors
// ---------------------------------------------------------------------------

function formatSensorValue(sensor: SensorOut): string {
  if (sensor.current_value === null || sensor.current_value === undefined) return "—";
  return sensor.unit ? `${sensor.current_value} ${sensor.unit}` : String(sensor.current_value);
}

const SENSOR_COLUMNS: Column<SensorOut>[] = [
  {
    key: "name",
    label: "Name",
    sortKey: (s) => s.name,
    render: (s) => (
      <span style={{ fontWeight: 600, color: "var(--fg)" }}>{s.name}</span>
    ),
  },
  {
    key: "status",
    label: "Status",
    sortKey: (s) => s.status,
    render: (s) => (
      <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
        <i
          style={{
            width: 7,
            height: 7,
            borderRadius: "50%",
            background: statusColor(s.status),
            display: "inline-block",
          }}
        />
        <span style={{ color: statusColor(s.status) }}>{s.status || "unknown"}</span>
      </span>
    ),
  },
  {
    key: "current_value",
    label: "Value",
    sortKey: (s) => s.current_value ?? -1,
    render: (s) => (
      <span style={{ color: "var(--muted)", fontFamily: "monospace", fontSize: 11 }}>
        {formatSensorValue(s)}
      </span>
    ),
  },
  {
    key: "metric_type",
    label: "Metric",
    sortKey: (s) => s.metric_type ?? "",
    render: (s) => (
      <span style={{ color: "var(--muted)" }}>{s.metric_type || "—"}</span>
    ),
  },
  {
    key: "device_id",
    label: "Device",
    sortKey: (s) => s.device_id ?? "",
    render: (s) => (
      <span style={{ color: "var(--muted)", fontFamily: "monospace", fontSize: 11 }}>
        {s.device_id ? s.device_id.slice(0, 8) + "…" : "—"}
      </span>
    ),
  },
  {
    key: "last_value_at",
    label: "Last reading",
    sortKey: (s) => s.last_value_at ?? "",
    render: (s) => <FreshnessIndicator lastCollectedAt={s.last_value_at} showAge compact />,
  },
];

// ---------------------------------------------------------------------------
// Monitoring sources
// ---------------------------------------------------------------------------

const SOURCE_COLUMNS: Column<MonitoringSourceOut>[] = [
  {
    key: "name",
    label: "Name",
    sortKey: (s) => s.name,
    render: (s) => (
      <span style={{ fontWeight: 600, color: "var(--fg)" }}>{s.name}</span>
    ),
  },
  {
    key: "source_type",
    label: "Type",
    sortKey: (s) => s.source_type,
    render: (s) => (
      <span style={{ color: "var(--cyan)" }}>{s.source_type || "—"}</span>
    ),
  },
  {
    key: "status",
    label: "Status",
    sortKey: (s) => s.status,
    render: (s) => (
      <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
        <i
          style={{
            width: 7,
            height: 7,
            borderRadius: "50%",
            background: statusColor(s.status),
            display: "inline-block",
          }}
        />
        <span style={{ color: statusColor(s.status) }}>{s.status || "unknown"}</span>
      </span>
    ),
  },
  {
    key: "hostname",
    label: "Hostname",
    sortKey: (s) => s.hostname ?? "",
    render: (s) => (
      <span style={{ color: "var(--muted)", fontFamily: "monospace", fontSize: 11 }}>
        {s.hostname || "—"}
      </span>
    ),
  },
];

// ---------------------------------------------------------------------------
// Filter model (entity-specific, server-side where supported)
// ---------------------------------------------------------------------------

interface DeviceFilters {
  deviceType: string;
  status: string;
  criticality: string;
}
interface SiteFilters {
  region: string;
  criticality: string;
}
interface SensorFilters {
  status: string;
}
interface SourceFilters {
  status: string;
}

const EMPTY_DEVICE_FILTERS: DeviceFilters = { deviceType: "", status: "", criticality: "" };
const EMPTY_SITE_FILTERS: SiteFilters = { region: "", criticality: "" };
const EMPTY_SENSOR_FILTERS: SensorFilters = { status: "" };
const EMPTY_SOURCE_FILTERS: SourceFilters = { status: "" };

// ---------------------------------------------------------------------------
// Generic table
// ---------------------------------------------------------------------------

function ExplorerTable<T>({
  columns,
  rows,
  sortKey,
  sortDir,
  onSort,
  onRowClick,
}: {
  columns: Column<T>[];
  rows: T[];
  sortKey: string | null;
  sortDir: SortDir;
  onSort: (key: string) => void;
  onRowClick: (row: T) => void;
}) {
  return (
    <div
      style={{
        maxHeight: 560,
        overflowY: "auto",
        border: "1px solid var(--line)",
        borderRadius: 8,
      }}
    >
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
        <thead style={{ position: "sticky", top: 0, zIndex: 1, background: "var(--bg, #0b0f14)" }}>
          <tr style={{ borderBottom: "1px solid var(--line)" }}>
            {columns.map((c) => (
              <SortHeader
                key={c.key}
                column={c}
                sortKey={sortKey}
                sortDir={sortDir}
                onSort={onSort}
              />
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr
              key={i}
              onClick={() => onRowClick(row)}
              style={{
                borderBottom: "1px solid rgba(0,217,255,.08)",
                cursor: "pointer",
              }}
              onMouseEnter={(e) => (e.currentTarget.style.background = "rgba(0,217,255,.04)")}
              onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
            >
              {columns.map((c) => (
                <td key={c.key} style={{ padding: "10px 12px", verticalAlign: "middle" }}>
                  {c.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Pagination
// ---------------------------------------------------------------------------

function Pagination({
  offset,
  pageSize,
  total,
  clientSideTotal,
  onPrev,
  onNext,
}: {
  offset: number;
  pageSize: number;
  total: number | null;
  clientSideTotal: number | null;
  onPrev: () => void;
  onNext: () => void;
}) {
  const shownTotal = total ?? clientSideTotal;
  const from = shownTotal === 0 ? 0 : offset + 1;
  const to = Math.min(offset + pageSize, shownTotal ?? offset + pageSize);
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        fontSize: 11,
        color: "var(--muted)",
        marginTop: 8,
        flexWrap: "wrap",
      }}
    >
      <span>
        {shownTotal === null
          ? `${from}–${to}`
          : `${from}–${to} of ${shownTotal}`}
      </span>
      <div style={{ display: "flex", gap: 6 }}>
        <button
          type="button"
          onClick={onPrev}
          disabled={offset === 0}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 4,
            background: "transparent",
            border: "1px solid var(--line)",
            borderRadius: 6,
            color: offset === 0 ? "var(--muted)" : "var(--fg)",
            fontSize: 11,
            padding: "3px 8px",
            cursor: offset === 0 ? "default" : "pointer",
          }}
        >
          <ChevronLeft size={12} /> Prev
        </button>
        <button
          type="button"
          onClick={onNext}
          disabled={shownTotal !== null && to >= shownTotal}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 4,
            background: "transparent",
            border: "1px solid var(--line)",
            borderRadius: 6,
            color:
              shownTotal !== null && to >= shownTotal ? "var(--muted)" : "var(--fg)",
            fontSize: 11,
            padding: "3px 8px",
            cursor:
              shownTotal !== null && to >= shownTotal ? "default" : "pointer",
          }}
        >
          Next <ChevronRight size={12} />
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Detail drawer
// ---------------------------------------------------------------------------

const DRAWER_STYLE: React.CSSProperties = {
  position: "fixed",
  top: 0,
  right: 0,
  height: "100vh",
  width: 380,
  zIndex: 60,
  background: "var(--bg, #0b0f14)",
  borderLeft: "1px solid var(--line)",
  padding: "16px 16px 24px",
  overflowY: "auto",
  boxShadow: "-8px 0 24px rgba(0,0,0,.4)",
};

function DrawerRow({ label, value }: { label: string; value: React.ReactNode }) {
  if (value === null || value === undefined || value === "") return null;
  return (
    <div style={{ display: "flex", gap: 10, padding: "3px 0", fontSize: 12 }}>
      <span style={{ color: "var(--muted)", minWidth: 120 }}>{label}</span>
      <span style={{ color: "var(--fg)", wordBreak: "break-word" }}>{value}</span>
    </div>
  );
}

function DeviceDrawer({
  device,
  onClose,
}: {
  device: AssetDevice;
  onClose: () => void;
}) {
  // Mounted fresh per device (parent renders with key={device.id}), so initial
  // state is correct without a reset effect. The effect only performs the
  // async fetch; every setState happens after an await boundary.
  const [rels, setRels] = useState<AssetRelationship[] | null>(null);
  const [relsLoading, setRelsLoading] = useState(true);
  const [relsError, setRelsError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getAssetDeviceRelationships(device.id)
      .then((r) => {
        if (!cancelled) {
          setRels(r);
          setRelsError(null);
        }
      })
      .catch((e) => {
        if (!cancelled) {
          setRelsError(e instanceof Error ? e.message : "Failed to load dependencies");
        }
      })
      .finally(() => {
        if (!cancelled) setRelsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [device]);

  return (
    <aside style={DRAWER_STYLE} role="dialog" aria-label="Entity detail">
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
        <strong style={{ fontSize: 14, color: "var(--cyan)", letterSpacing: 0.5 }}>
          DEVICE DETAIL
        </strong>
        <button
          onClick={onClose}
          aria-label="Close"
          style={{
            marginLeft: "auto",
            background: "transparent",
            border: "1px solid var(--line)",
            borderRadius: 6,
            color: "var(--muted)",
            fontSize: 12,
            padding: "2px 9px",
            cursor: "pointer",
          }}
        >
          Close
        </button>
      </div>
      <div style={{ fontSize: 15, fontWeight: 700, color: "var(--fg)", marginBottom: 4 }}>
        {device.name}
      </div>
      <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 12 }}>
        {device.device_type}
      </div>

      <div style={{ borderTop: "1px solid var(--line)", paddingTop: 10 }}>
        <DrawerRow label="Status" value={<span style={{ color: statusColor(device.status) }}>{device.status || "unknown"}</span>} />
        <DrawerRow label="IP address" value={device.ip_address} />
        <DrawerRow label="Hostname" value={device.hostname} />
        <DrawerRow label="Vendor" value={device.vendor} />
        <DrawerRow label="Model" value={device.model} />
        <DrawerRow label="OS version" value={device.os_version} />
        <DrawerRow label="Serial number" value={device.serial_number} />
        <DrawerRow label="MAC address" value={device.mac_address} />
        <DrawerRow label="Criticality" value={device.criticality} />
        <DrawerRow label="Environment" value={device.environment} />
        <DrawerRow label="Business owner" value={device.business_owner} />
        <DrawerRow label="Support team" value={device.support_team} />
        <DrawerRow label="Site" value={device.site_id} />
        <DrawerRow
          label="Last seen"
          value={
            <>
              <FreshnessIndicator lastCollectedAt={device.last_seen_at} showAge />
              <span style={{ marginLeft: 8, fontSize: 10, color: "var(--muted)" }}>
                {device.last_seen_at ? new Date(device.last_seen_at).toLocaleString() : "Never"}
              </span>
            </>
          }
        />
        {device.tags.length > 0 && (
          <div style={{ marginTop: 8 }}>
            <span style={{ color: "var(--muted)", fontSize: 11 }}>Tags</span>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 4 }}>
              {device.tags.map((t) => (
                <span
                  key={t}
                  style={{
                    fontSize: 10,
                    padding: "2px 6px",
                    borderRadius: 4,
                    background: "rgba(0,217,255,.08)",
                    color: "var(--cyan)",
                  }}
                >
                  {t}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Known dependencies — real AssetRelationship rows only. */}
      <div style={{ marginTop: 16, borderTop: "1px solid var(--line)", paddingTop: 10 }}>
        <div style={{ fontSize: 10, color: "var(--muted)", letterSpacing: 0.5, marginBottom: 6 }}>
          KNOWN DEPENDENCIES
        </div>
        {relsLoading ? (
          <span style={{ fontSize: 11, color: "var(--muted)" }}>
            <Loader2 size={11} className="animate-spin" style={{ display: "inline" }} /> Loading…
          </span>
        ) : relsError ? (
          <span style={{ fontSize: 11, color: "var(--red)" }}>{relsError}</span>
        ) : rels && rels.length > 0 ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {rels.map((r) => (
              <div key={r.id} style={{ fontSize: 11, color: "var(--fg)" }}>
                <span style={{ color: "var(--cyan)" }}>{r.relationship_type}</span>
                {" → "}
                <span style={{ fontFamily: "monospace" }}>{r.related_asset_id.slice(0, 8)}…</span>
                {r.label ? <span style={{ color: "var(--muted)" }}> ({r.label})</span> : null}
              </div>
            ))}
          </div>
        ) : (
          <span style={{ fontSize: 11, color: "var(--muted)" }}>
            No recorded dependencies for this device.
          </span>
        )}
      </div>

      <div style={{ marginTop: 16, display: "flex", flexWrap: "wrap", gap: 6 }}>
        <a
          href={`/infrastructure/assets/${device.id}`}
          style={{
            fontSize: 11,
            padding: "4px 10px",
            borderRadius: 6,
            border: "1px solid var(--line)",
            color: "var(--cyan)",
            textDecoration: "none",
          }}
        >
          Open device
        </a>
        <a
          href="/monitoring"
          style={{
            fontSize: 11,
            padding: "4px 10px",
            borderRadius: 6,
            border: "1px solid var(--line)",
            color: "var(--cyan)",
            textDecoration: "none",
          }}
        >
          View monitoring
        </a>
      </div>
    </aside>
  );
}

function SiteDrawer({ site, onClose }: { site: AssetSite | null; onClose: () => void }) {
  if (!site) return null;
  return (
    <aside style={DRAWER_STYLE} role="dialog" aria-label="Entity detail">
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
        <strong style={{ fontSize: 14, color: "var(--cyan)", letterSpacing: 0.5 }}>SITE DETAIL</strong>
        <button
          onClick={onClose}
          aria-label="Close"
          style={{
            marginLeft: "auto",
            background: "transparent",
            border: "1px solid var(--line)",
            borderRadius: 6,
            color: "var(--muted)",
            fontSize: 12,
            padding: "2px 9px",
            cursor: "pointer",
          }}
        >
          Close
        </button>
      </div>
      <div style={{ fontSize: 15, fontWeight: 700, color: "var(--fg)", marginBottom: 4 }}>
        {site.name}
      </div>
      <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 12 }}>{site.code}</div>
      <div style={{ borderTop: "1px solid var(--line)", paddingTop: 10 }}>
        <DrawerRow label="Region" value={site.region} />
        <DrawerRow label="Criticality" value={site.criticality} />
        <DrawerRow label="Address" value={site.address} />
        <DrawerRow label="Building" value={site.building} />
        <DrawerRow label="Floor" value={site.floor} />
        <DrawerRow label="Rack" value={site.rack} />
        <DrawerRow
          label="Coordinates"
          value={
            site.latitude !== null && site.longitude !== null
              ? `${site.latitude}, ${site.longitude}`
              : null
          }
        />
        <DrawerRow label="Site owner" value={site.site_owner} />
        <DrawerRow label="Support contact" value={site.support_contact} />
        <DrawerRow label="Timezone" value={site.timezone} />
      </div>
    </aside>
  );
}

function SimpleDrawer({
  title,
  name,
  subtitle,
  rows,
  onClose,
}: {
  title: string;
  name: string;
  subtitle: string;
  rows: { label: string; value: React.ReactNode }[];
  onClose: () => void;
}) {
  return (
    <aside style={DRAWER_STYLE} role="dialog" aria-label="Entity detail">
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
        <strong style={{ fontSize: 14, color: "var(--cyan)", letterSpacing: 0.5 }}>{title}</strong>
        <button
          onClick={onClose}
          aria-label="Close"
          style={{
            marginLeft: "auto",
            background: "transparent",
            border: "1px solid var(--line)",
            borderRadius: 6,
            color: "var(--muted)",
            fontSize: 12,
            padding: "2px 9px",
            cursor: "pointer",
          }}
        >
          Close
        </button>
      </div>
      <div style={{ fontSize: 15, fontWeight: 700, color: "var(--fg)", marginBottom: 4 }}>{name}</div>
      <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 12 }}>{subtitle}</div>
      <div style={{ borderTop: "1px solid var(--line)", paddingTop: 10 }}>
        {rows.map((r) => (
          <DrawerRow key={r.label} label={r.label} value={r.value} />
        ))}
      </div>
    </aside>
  );
}

// ---------------------------------------------------------------------------
// Filter pill input (text) + select
// ---------------------------------------------------------------------------

function FilterInput({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 3, fontSize: 10, color: "var(--muted)" }}>
      {label}
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        style={{
          background: "transparent",
          border: "1px solid var(--line)",
          borderRadius: 6,
          color: "var(--fg)",
          fontSize: 11,
          padding: "4px 8px",
          width: 120,
          outline: "none",
        }}
        onFocus={(e) => (e.currentTarget.style.borderColor = "var(--cyan)")}
        onBlur={(e) => (e.currentTarget.style.borderColor = "var(--line)")}
      />
    </label>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function ExplorerPage() {
  const tenantId = getTenantId();
  const [entity, setEntity] = useState<EntityKind>("devices");

  // Shared search + debounce
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setSearch(searchInput.trim());
      setOffset(0);
    }, 300);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [searchInput]);

  // Reset search input is handled in the entity-tab onClick (event handler, not
  // an effect) to comply with react-hooks/set-state-in-effect.

  // Per-entity filters
  const [deviceFilters, setDeviceFilters] = useState<DeviceFilters>(EMPTY_DEVICE_FILTERS);
  const [siteFilters, setSiteFilters] = useState<SiteFilters>(EMPTY_SITE_FILTERS);
  const [sensorFilters, setSensorFilters] = useState<SensorFilters>(EMPTY_SENSOR_FILTERS);
  const [sourceFilters, setSourceFilters] = useState<SourceFilters>(EMPTY_SOURCE_FILTERS);

  // Sort
  const [sortKey, setSortKey] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  // Data layer: React Query owns the fetch (matches the rest of the dashboard
  // via @tanstack/react-query). The queryKey encodes entity + search + filters +
  // offset so a change to any of them refetches; loading/error/data come
  // straight off the query — no manual effect/setState.
  const isServerPaged = entity === "devices" || entity === "sites";
  const query = useQuery({
    queryKey: [
      "explorer",
      entity,
      tenantId,
      search,
      deviceFilters,
      siteFilters,
      sensorFilters,
      sourceFilters,
      offset,
    ],
    queryFn: async (): Promise<{ rows: unknown[]; clientTotal: number | null }> => {
      if (entity === "devices") {
        const rows = await listAssetDevices(tenantId, {
          search: search || undefined,
          deviceType: deviceFilters.deviceType || undefined,
          status: deviceFilters.status || undefined,
          criticality: deviceFilters.criticality || undefined,
          limit: PAGE_SIZE,
          offset,
        });
        return { rows, clientTotal: null };
      }
      if (entity === "sites") {
        const rows = await listAssetSites(tenantId, {
          search: search || undefined,
          region: siteFilters.region || undefined,
          criticality: siteFilters.criticality || undefined,
          limit: PAGE_SIZE,
          offset,
        });
        return { rows, clientTotal: null };
      }
      if (entity === "sensors") {
        // No server-side filter/pagination endpoint: fetch and filter/paginate
        // client-side over the result.
        const all = await listSensors(tenantId);
        let filtered = all;
        if (search) {
          const q = search.toLowerCase();
          filtered = filtered.filter(
            (s) =>
              s.name.toLowerCase().includes(q) ||
              (s.metric_type ?? "").toLowerCase().includes(q),
          );
        }
        if (sensorFilters.status) {
          filtered = filtered.filter(
            (s) => (s.status ?? "").toLowerCase() === sensorFilters.status.toLowerCase(),
          );
        }
        return { rows: filtered.slice(offset, offset + PAGE_SIZE), clientTotal: filtered.length };
      }
      const all = await listMonitoringSources(tenantId);
      let filtered = all;
      if (search) {
        const q = search.toLowerCase();
        filtered = filtered.filter(
          (s) =>
            s.name.toLowerCase().includes(q) ||
            (s.source_type ?? "").toLowerCase().includes(q),
        );
      }
      if (sourceFilters.status) {
        filtered = filtered.filter(
          (s) => (s.status ?? "").toLowerCase() === sourceFilters.status.toLowerCase(),
        );
      }
      return { rows: filtered.slice(offset, offset + PAGE_SIZE), clientTotal: filtered.length };
    },
    staleTime: 30_000,
  });

  const data: unknown[] | null = query.data?.rows ?? null;
  const clientTotal: number | null = query.data?.clientTotal ?? null;
  const loading = query.isLoading;
  const error = query.error
    ? query.error instanceof Error
      ? query.error.message
      : "Failed to load data"
    : null;
  const errorKind: DataStateErrorKind | null = query.error ? classifyError(query.error) : null;
  const lastCollectedAt = query.dataUpdatedAt
    ? new Date(query.dataUpdatedAt).toISOString()
    : null;

  // Detail drawer state
  const [selectedDevice, setSelectedDevice] = useState<AssetDevice | null>(null);
  const [selectedSite, setSelectedSite] = useState<AssetSite | null>(null);
  const [selectedSensor, setSelectedSensor] = useState<SensorOut | null>(null);
  const [selectedSource, setSelectedSource] = useState<MonitoringSourceOut | null>(null);

  const onSort = useCallback((key: string) => {
    setSortKey((prev) => {
      if (prev === key) {
        setSortDir((d) => (d === "asc" ? "desc" : "asc"));
        return key;
      }
      setSortDir("asc");
      return key;
    });
  }, []);

  // Apply client-side sort to the fetched page (servers already sort by name;
  // this gives the operator clickable headers on every column).
  const sortedData = useMemo(() => {
    if (!data || !sortKey) return data;
    const arr = [...data] as Record<string, unknown>[];
    arr.sort((a, b) => {
      const av = (a as never)[sortKey] as string | number | null | undefined;
      const bv = (b as never)[sortKey] as string | number | null | undefined;
      const an = av ?? "";
      const bn = bv ?? "";
      if (an === bn) return 0;
      const cmp = an < bn ? -1 : 1;
      return sortDir === "asc" ? cmp : -cmp;
    });
    return arr;
  }, [data, sortKey, sortDir]);

  const hasFilters =
    entity === "devices"
      ? !!(deviceFilters.deviceType || deviceFilters.status || deviceFilters.criticality)
      : entity === "sites"
        ? !!(siteFilters.region || siteFilters.criticality)
        : entity === "sensors"
          ? !!sensorFilters.status
          : !!sourceFilters.status;

  const clearFilters = () => {
    setDeviceFilters(EMPTY_DEVICE_FILTERS);
    setSiteFilters(EMPTY_SITE_FILTERS);
    setSensorFilters(EMPTY_SENSOR_FILTERS);
    setSourceFilters(EMPTY_SOURCE_FILTERS);
    setSearchInput("");
    setSearch("");
    setOffset(0);
  };

  // For server-paged entities we only know the page boundary; show "of ?"
  // until a shorter page implies the end.
  const pageBoundaryTotal = (() => {
    if (isServerPaged && data && data.length < PAGE_SIZE) {
      return offset + (data?.length ?? 0);
    }
    return null;
  })();

  return (
    <div className="page-shell" style={{ paddingTop: "clamp(84px,8.5vh,104px)" }}>
      <div style={{ maxWidth: 1400, margin: "0 auto", padding: "0 16px" }}>
        {/* Header */}
        <div style={{ marginBottom: 16 }}>
          <h2 style={{ color: "var(--cyan)", fontSize: 16, margin: 0, textTransform: "uppercase", letterSpacing: 0.5 }}>
            Service &amp; Infrastructure Explorer
          </h2>
          <p style={{ color: "var(--muted)", fontSize: 12, marginTop: 4 }}>
            Search, filter and inspect the entities your estate actually
            contains. Every value comes from a real API — nothing invented.
          </p>
        </div>

        {/* Entity tabs */}
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 14 }}>
          {ENTITY_TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => {
                setEntity(t.id);
                setSearchInput("");
                setSearch("");
                setOffset(0);
              }}
              style={{
                fontSize: 12,
                fontWeight: 600,
                padding: "6px 14px",
                borderRadius: 6,
                cursor: "pointer",
                border: `1px solid ${entity === t.id ? "var(--cyan)" : "var(--line)"}`,
                background: entity === t.id ? "rgba(0,217,255,.08)" : "transparent",
                color: entity === t.id ? "var(--cyan)" : "var(--muted)",
              }}
            >
              {t.label}
            </button>
          ))}
        </div>

        {/* Search + filters row */}
        <div
          style={{
            display: "flex",
            gap: 12,
            flexWrap: "wrap",
            alignItems: "flex-end",
            marginBottom: 14,
          }}
        >
          <div style={{ position: "relative", flexGrow: 1, minWidth: 240 }}>
            <Search
              size={14}
              style={{
                position: "absolute",
                left: 10,
                top: "50%",
                transform: "translateY(-50%)",
                color: "var(--muted)",
              }}
            />
            <input
              type="text"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder={
                entity === "devices"
                  ? "Search devices by name, hostname, IP…"
                  : entity === "sites"
                    ? "Search sites by name or code…"
                    : entity === "sensors"
                      ? "Search sensors by name or metric…"
                      : "Search sources by name or type…"
              }
              style={{
                width: "100%",
                background: "transparent",
                border: "1px solid var(--line)",
                borderRadius: 6,
                color: "var(--fg)",
                fontSize: 13,
                padding: "8px 12px 8px 34px",
                outline: "none",
              }}
              onFocus={(e) => (e.currentTarget.style.borderColor = "var(--cyan)")}
              onBlur={(e) => (e.currentTarget.style.borderColor = "var(--line)")}
            />
          </div>

          {entity === "devices" && (
            <>
              <FilterInput label="Type" value={deviceFilters.deviceType} onChange={(v) => { setDeviceFilters((f) => ({ ...f, deviceType: v })); setOffset(0); }} placeholder="router" />
              <FilterInput label="Status" value={deviceFilters.status} onChange={(v) => { setDeviceFilters((f) => ({ ...f, status: v })); setOffset(0); }} placeholder="up" />
              <FilterInput label="Criticality" value={deviceFilters.criticality} onChange={(v) => { setDeviceFilters((f) => ({ ...f, criticality: v })); setOffset(0); }} placeholder="high" />
            </>
          )}
          {entity === "sites" && (
            <>
              <FilterInput label="Region" value={siteFilters.region} onChange={(v) => { setSiteFilters((f) => ({ ...f, region: v })); setOffset(0); }} placeholder="EMEA" />
              <FilterInput label="Criticality" value={siteFilters.criticality} onChange={(v) => { setSiteFilters((f) => ({ ...f, criticality: v })); setOffset(0); }} placeholder="high" />
            </>
          )}
          {entity === "sensors" && (
            <FilterInput label="Status" value={sensorFilters.status} onChange={(v) => { setSensorFilters((f) => ({ ...f, status: v })); setOffset(0); }} placeholder="up" />
          )}
          {entity === "sources" && (
            <FilterInput label="Status" value={sourceFilters.status} onChange={(v) => { setSourceFilters((f) => ({ ...f, status: v })); setOffset(0); }} placeholder="connected" />
          )}

          {(hasFilters || search) && (
            <button
              type="button"
              onClick={clearFilters}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
                background: "transparent",
                border: "1px solid var(--line)",
                borderRadius: 6,
                color: "var(--muted)",
                fontSize: 11,
                padding: "7px 10px",
                cursor: "pointer",
              }}
            >
              <X size={12} /> Clear
            </button>
          )}

          <button
            type="button"
            onClick={() => query.refetch()}
            disabled={query.isFetching}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 5,
              background: "transparent",
              border: "1px solid var(--line)",
              borderRadius: 6,
              color: "var(--muted)",
              fontSize: 11,
              padding: "7px 10px",
              cursor: "pointer",
            }}
          >
            {query.isFetching ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
            Refresh
          </button>
        </div>

        {/* Data-source note for client-paged entities */}
        {!isServerPaged && (
          <p style={{ fontSize: 11, color: "var(--muted)", margin: "0 0 8px" }}>
            This entity has no server-side search/filter endpoint, so results are
            filtered and paginated client-side from a single fetch.
          </p>
        )}

        {/* Table with honest states */}
        <DataStateView
          loading={loading && data === null}
          error={error}
          errorKind={errorKind}
          data={data}
          lastCollectedAt={lastCollectedAt}
        >
          {data && data.length > 0 ? (
            <>
              {entity === "devices" && (
                <ExplorerTable<AssetDevice>
                  columns={DEVICE_COLUMNS}
                  rows={sortedData as AssetDevice[]}
                  sortKey={sortKey}
                  sortDir={sortDir}
                  onSort={onSort}
                  onRowClick={setSelectedDevice}
                />
              )}
              {entity === "sites" && (
                <ExplorerTable<AssetSite>
                  columns={SITE_COLUMNS}
                  rows={sortedData as AssetSite[]}
                  sortKey={sortKey}
                  sortDir={sortDir}
                  onSort={onSort}
                  onRowClick={setSelectedSite}
                />
              )}
              {entity === "sensors" && (
                <ExplorerTable<SensorOut>
                  columns={SENSOR_COLUMNS}
                  rows={sortedData as SensorOut[]}
                  sortKey={sortKey}
                  sortDir={sortDir}
                  onSort={onSort}
                  onRowClick={setSelectedSensor}
                />
              )}
              {entity === "sources" && (
                <ExplorerTable<MonitoringSourceOut>
                  columns={SOURCE_COLUMNS}
                  rows={sortedData as MonitoringSourceOut[]}
                  sortKey={sortKey}
                  sortDir={sortDir}
                  onSort={onSort}
                  onRowClick={setSelectedSource}
                />
              )}
              <Pagination
                offset={offset}
                pageSize={PAGE_SIZE}
                total={isServerPaged ? pageBoundaryTotal : clientTotal}
                clientSideTotal={clientTotal}
                onPrev={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
                onNext={() => setOffset((o) => o + PAGE_SIZE)}
              />
            </>
          ) : (
            <div style={{ padding: "40px 12px", textAlign: "center", color: "var(--muted)", fontSize: 13 }}>
              No {entity} match the current filters.
            </div>
          )}
        </DataStateView>

        {/* Partial-data notice: server-paged entity returned a full page, so
            more rows likely exist beyond this page even though total is
            unknown to the client. */}
        {isServerPaged && data && data.length === PAGE_SIZE && (
          <p style={{ fontSize: 10, color: "var(--muted)", marginTop: 8 }}>
            Showing the first {PAGE_SIZE} matches — use Next to load more. The
            full total is not reported by this endpoint.
          </p>
        )}
      </div>

      {/* Drawers */}
      {selectedDevice && (
        <DeviceDrawer
          key={selectedDevice.id}
          device={selectedDevice}
          onClose={() => setSelectedDevice(null)}
        />
      )}
      <SiteDrawer site={selectedSite} onClose={() => setSelectedSite(null)} />
      {selectedSensor && (
        <SimpleDrawer
          title="SENSOR DETAIL"
          name={selectedSensor.name}
          subtitle={selectedSensor.metric_type || "sensor"}
          onClose={() => setSelectedSensor(null)}
          rows={[
            { label: "Status", value: <span style={{ color: statusColor(selectedSensor.status) }}>{selectedSensor.status || "unknown"}</span> },
            { label: "Current value", value: formatSensorValue(selectedSensor) },
            { label: "Unit", value: selectedSensor.unit },
            { label: "Device", value: selectedSensor.device_id },
            { label: "Monitoring source", value: selectedSensor.monitoring_source_id },
            { label: "Last reading", value: <FreshnessIndicator lastCollectedAt={selectedSensor.last_value_at} showAge /> },
          ]}
        />
      )}
      {selectedSource && (
        <SimpleDrawer
          title="MONITORING SOURCE DETAIL"
          name={selectedSource.name}
          subtitle={selectedSource.source_type || "source"}
          onClose={() => setSelectedSource(null)}
          rows={[
            { label: "Status", value: <span style={{ color: statusColor(selectedSource.status) }}>{selectedSource.status || "unknown"}</span> },
            { label: "Hostname", value: selectedSource.hostname },
            { label: "IP address", value: selectedSource.ip_address },
            { label: "Vendor", value: selectedSource.vendor },
            { label: "Active", value: selectedSource.is_active ? "Yes" : "No" },
            {
              label: "Last sync",
              value: <FreshnessIndicator lastCollectedAt={selectedSource.last_sync_at} showAge />,
            },
          ]}
        />
      )}
    </div>
  );
}
