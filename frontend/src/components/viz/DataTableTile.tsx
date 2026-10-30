"use client";

import { memo, type ReactNode } from "react";
import TileShell from "@/components/viz/TileShell";

/**
 * DataTableTile — a compact, dense table inside a TileShell. Columns declare a
 * render function per row so the tile stays presentational (no data logic).
 * Supports a click-through handler per row (drill-down) and an optional footer
 * link.
 */
export interface DataTableColumn<T> {
  key: string;
  label: string;
  align?: "left" | "right" | "center";
  render: (row: T) => ReactNode;
}

export interface DataTableTileProps<T> {
  title: string;
  subtitle?: string;
  columns: DataTableColumn<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  onRowClick?: (row: T) => void;
  footerLink?: { href: string; label: string };
  loading?: boolean;
  error?: unknown;
  lastCollectedAt?: string | null;
  onRefresh?: () => void;
  refreshing?: boolean;
  maxHeight?: number;
}

export default memo(function DataTableTile<T>({
  title,
  subtitle,
  columns,
  rows,
  rowKey,
  onRowClick,
  footerLink,
  loading,
  error,
  lastCollectedAt,
  onRefresh,
  refreshing,
  maxHeight = 280,
}: DataTableTileProps<T>) {
  return (
    <TileShell
      title={title}
      subtitle={subtitle}
      onRefresh={onRefresh}
      refreshing={refreshing}
      lastCollectedAt={lastCollectedAt}
      loading={loading}
      error={error}
      data={rows.length > 0 ? rows : null}
      minHeight={180}
    >
      <div style={{ maxHeight, overflowY: "auto", borderRadius: 6, border: "1px solid var(--line, #1f2d38)" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead style={{ position: "sticky", top: 0, background: "var(--surface, rgba(7,19,27,0.6))", zIndex: 1 }}>
            <tr style={{ borderBottom: "1px solid var(--line, #1f2d38)" }}>
              {columns.map((c) => (
                <th
                  key={c.key}
                  style={{
                    textAlign: c.align ?? "left",
                    padding: "7px 10px",
                    fontSize: 10,
                    fontWeight: 600,
                    letterSpacing: 0.4,
                    textTransform: "uppercase",
                    color: "var(--cyan)",
                  }}
                >
                  {c.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                key={rowKey(row)}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                style={{
                  borderBottom: "1px solid rgba(0,217,255,.06)",
                  cursor: onRowClick ? "pointer" : "default",
                }}
                onMouseEnter={(e) => (e.currentTarget.style.background = "rgba(0,217,255,.04)")}
                onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
              >
                {columns.map((c) => (
                  <td key={c.key} style={{ padding: "7px 10px", textAlign: c.align ?? "left", verticalAlign: "middle" }}>
                    {c.render(row)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {footerLink && (
        <a
          href={footerLink.href}
          style={{ display: "inline-block", marginTop: 8, fontSize: 11, color: "var(--cyan)", textDecoration: "none" }}
        >
          {footerLink.label} →
        </a>
      )}
    </TileShell>
  );
}) as <T>(props: DataTableTileProps<T>) => React.JSX.Element;
