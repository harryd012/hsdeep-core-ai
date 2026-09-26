"use client";

/**
 * TopologyToolbar — compact operational filter and control bar (Phase 9.7A).
 *
 * Two rows at most: the primary row (search, site, status, filters, layout) and a
 * collapsible panel for the long-tail facets. Nothing here invents a facet: every
 * chip is generated from values that exist in this tenant's data, and the
 * "Unclassified" toggle filters on the backend's own recognition status.
 */
import { useMemo, useState } from "react";
import { ChevronDown, Filter, Search, Sliders } from "lucide-react";

import { LAYOUTS, type LayoutKind } from "../../lib/topology/layout";
import { DEVICE_STATE_VISUALS, type DeviceState } from "../../lib/topology/status";
import type { FilterOptions, TopologyFilters } from "./topologyRenderTypes";

const STATE_ORDER: DeviceState[] = [
  "ONLINE",
  "WARNING",
  "OFFLINE",
  "UNKNOWN",
  "STALE",
  "MAINTENANCE",
];

export interface TopologyToolbarProps {
  query: string;
  onQueryChange: (value: string) => void;
  siteFilter: string;
  onSiteFilterChange: (value: string) => void;
  siteOptions: { id: string; label: string }[];
  options: FilterOptions | null;
  filters: TopologyFilters;
  onToggleState: (state: DeviceState) => void;
  onToggleUnidentified: () => void;
  onToggleVendor: (key: string) => void;
  onToggleDeviceType: (type: string) => void;
  onToggleRelationship: (type: string) => void;
  onToggleEvidence: (tag: TopologyFilters["evidence"][number]) => void;
  onClear: () => void;
  layout: LayoutKind;
  onLayoutChange: (layout: LayoutKind) => void;
  /** Results after filtering, shown next to the search box. */
  resultCount: number;
  totalCount: number;
}

function chipStyle(active: boolean) {
  return {
    fontSize: 11,
    padding: "3px 8px",
    borderRadius: 999,
    border: `1px solid ${active ? "var(--cyan, #38bdf8)" : "var(--border)"}`,
    background: active ? "rgba(56,189,248,0.14)" : "transparent",
    color: active ? "var(--fg)" : "var(--muted)",
    cursor: "pointer",
    whiteSpace: "nowrap" as const,
  };
}

function controlStyle() {
  return {
    background: "transparent",
    color: "var(--fg)",
    border: "1px solid var(--border)",
    borderRadius: 6,
    padding: "3px 6px",
    fontSize: 11,
  };
}

export default function TopologyToolbar({
  query,
  onQueryChange,
  siteFilter,
  onSiteFilterChange,
  siteOptions,
  options,
  filters,
  onToggleState,
  onToggleUnidentified,
  onToggleVendor,
  onToggleDeviceType,
  onToggleRelationship,
  onToggleEvidence,
  onClear,
  layout,
  onLayoutChange,
  resultCount,
  totalCount,
}: TopologyToolbarProps) {
  const [showFilters, setShowFilters] = useState(false);

  const activeFacets =
    filters.vendors.length +
    filters.deviceTypes.length +
    filters.relationshipTypes.length +
    filters.evidence.length +
    (filters.unidentifiedOnly ? 1 : 0);

  const evidenceTags = useMemo(() => {
    const present = new Set(options?.classifications ?? []);
    return ["observed", "inferred", "unclassified", "active", "passive", "api"].filter(
      (tag) => tag === "observed" || tag === "inferred" || present.has(tag)
    );
  }, [options]);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div
        data-testid="topology-toolbar"
        style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}
      >
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            border: "1px solid var(--border)",
            borderRadius: 7,
            padding: "3px 8px",
            flex: "0 1 300px",
            minWidth: 200,
          }}
        >
          <Search size={13} aria-hidden="true" />
          <input
            value={query}
            onChange={(event) => onQueryChange(event.target.value)}
            placeholder="Search hostname, IP, serial, vendor, model, site…"
            aria-label="Search topology"
            data-testid="topology-search"
            style={{
              background: "transparent",
              border: "none",
              outline: "none",
              color: "var(--fg)",
              fontSize: 12,
              width: "100%",
            }}
          />
          <span style={{ fontSize: 10, color: "var(--muted)", whiteSpace: "nowrap" }}>
            {resultCount}/{totalCount}
          </span>
        </span>

        <label style={{ fontSize: 11, color: "var(--muted)", display: "inline-flex", gap: 5 }}>
          Site
          <select
            value={siteFilter}
            onChange={(event) => onSiteFilterChange(event.target.value)}
            style={controlStyle()}
            aria-label="Filter by site"
            data-testid="topology-site-filter"
          >
            <option value="">All sites</option>
            {siteOptions.map((site) => (
              <option key={site.id} value={site.id.replace(/^site:/, "")}>
                {site.label}
              </option>
            ))}
          </select>
        </label>

        <span
          style={{ display: "inline-flex", gap: 5, flexWrap: "wrap", alignItems: "center" }}
          aria-label="Filter by device state"
        >
          {STATE_ORDER.filter((state) => !options || options.states.includes(state)).map(
            (state) => (
              <button
                key={state}
                type="button"
                onClick={() => onToggleState(state)}
                style={chipStyle(filters.states.includes(state))}
                aria-pressed={filters.states.includes(state)}
                title={DEVICE_STATE_VISUALS[state].description}
              >
                {DEVICE_STATE_VISUALS[state].label}
              </button>
            )
          )}
          <button
            type="button"
            onClick={onToggleUnidentified}
            style={chipStyle(filters.unidentifiedOnly)}
            aria-pressed={filters.unidentifiedOnly}
            data-testid="topology-unclassified-filter"
            title="Only resources the platform never classified (identity stays unknown)"
          >
            Unclassified
          </button>
        </span>

        <span style={{ display: "inline-flex", gap: 5, alignItems: "center" }}>
          <button
            type="button"
            onClick={() => setShowFilters((value) => !value)}
            style={{
              ...chipStyle(showFilters || activeFacets > 0),
              display: "inline-flex",
              gap: 5,
            }}
            aria-expanded={showFilters}
            data-testid="topology-more-filters"
          >
            <Filter size={11} />
            Filters{activeFacets ? ` (${activeFacets})` : ""}
            <ChevronDown size={11} />
          </button>
          <button
            type="button"
            onClick={onClear}
            style={chipStyle(false)}
            data-testid="topology-clear"
          >
            Clear
          </button>
        </span>

        <label
          style={{
            fontSize: 11,
            color: "var(--muted)",
            display: "inline-flex",
            gap: 5,
            marginLeft: "auto",
          }}
        >
          <Sliders size={12} aria-hidden="true" style={{ alignSelf: "center" }} />
          Layout
          <select
            value={layout}
            onChange={(event) => onLayoutChange(event.target.value as LayoutKind)}
            style={controlStyle()}
            aria-label="Topology layout"
            data-testid="topology-layout"
          >
            {LAYOUTS.map((entry) => (
              <option key={entry.kind} value={entry.kind} title={entry.hint}>
                {entry.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {showFilters ? (
        <div
          data-testid="topology-filter-panel"
          style={{
            display: "flex",
            gap: 6,
            flexWrap: "wrap",
            alignItems: "center",
            padding: "6px 8px",
            border: "1px dashed var(--border)",
            borderRadius: 8,
          }}
        >
          {(options?.vendors ?? []).map((vendor) => (
            <button
              key={vendor.key}
              type="button"
              onClick={() => onToggleVendor(vendor.key)}
              style={chipStyle(filters.vendors.includes(vendor.key))}
              aria-pressed={filters.vendors.includes(vendor.key)}
            >
              {vendor.label}
            </button>
          ))}
          {(options?.deviceTypes ?? []).map((type) => (
            <button
              key={type}
              type="button"
              onClick={() => onToggleDeviceType(type)}
              style={chipStyle(filters.deviceTypes.includes(type))}
              aria-pressed={filters.deviceTypes.includes(type)}
            >
              {type}
            </button>
          ))}
          {(options?.relationshipTypes ?? []).map((type) => (
            <button
              key={type}
              type="button"
              onClick={() => onToggleRelationship(type)}
              style={chipStyle(filters.relationshipTypes.includes(type))}
              aria-pressed={filters.relationshipTypes.includes(type)}
            >
              {type.replace(/_/g, " ")}
            </button>
          ))}
          {evidenceTags.map((tag) => (
            <button
              key={tag}
              type="button"
              onClick={() => onToggleEvidence(tag as TopologyFilters["evidence"][number])}
              style={chipStyle(
                filters.evidence.includes(tag as TopologyFilters["evidence"][number])
              )}
              aria-pressed={filters.evidence.includes(tag as TopologyFilters["evidence"][number])}
            >
              {tag}
            </button>
          ))}
          {!options?.vendors.length &&
          !options?.deviceTypes.length &&
          !options?.relationshipTypes.length ? (
            <span style={{ fontSize: 11, color: "var(--muted)" }}>
              No vendor, device-type or relationship values exist in this tenant yet.
            </span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}