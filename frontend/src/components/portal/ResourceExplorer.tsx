"use client";

/**
 * Generic Resource Explorer.
 *
 * Renders exactly what the registry declares for the selected type - columns,
 * real loader, per-type tabs. There is NO resource-specific logic here and no
 * invented data: a type with `implemented: false` renders that type's own
 * declared `unavailableReason` instead of an empty table.
 *
 * Performance: nothing is fetched until a type is SELECTED, every request is
 * bounded by DEFAULT_RESOURCE_LIMIT, and there are no per-row requests.
 * Tenant context is left entirely to the existing authenticated API client -
 * this component never accepts or sends a tenant id of its own.
 */

import Link from "next/link";
import { useEffect, useState } from "react";

import {
  EmptyState,
  ErrorState,
  LoadingState,
  ResourceHealth,
} from "@/components/portal/PortalShell";
import { getResourceType, implementedResourceTypes } from "@/lib/resources/resource-registry";
import { DEFAULT_RESOURCE_LIMIT, type ResourceSummary } from "@/lib/resources/resource-types";

type LoadState = "idle" | "loading" | "ready" | "error";
const TYPES = implementedResourceTypes();

export function ResourceExplorer({ fixedType }: { fixedType?: string }) {
  const [key, setKey] = useState(fixedType ?? "");
  const [search, setSearch] = useState("");
  const [applied, setApplied] = useState("");
  const [reloadKey, setReloadKey] = useState(0);
  const [rows, setRows] = useState<unknown[] | null>(null);
  const [state, setState] = useState<LoadState>("idle");
  const [error, setError] = useState<string | null>(null);

  const def = key ? getResourceType(key) : undefined;

  // The fetch lives inside the effect (not an external helper) so no setState is
  // invoked synchronously in the effect body - the cancelled flag and the async
  // IIFE are the established pattern in this codebase. Refresh re-runs it via
  // `reloadKey` rather than calling across the effect boundary.
  useEffect(() => {
    let cancelled = false;

    (async () => {
      const loader = def?.list;
      if (!loader) {
        if (!cancelled) {
          setRows(null);
          setState("idle");
        }
        return;
      }
      if (!cancelled) {
        setState("loading");
        setRows(null);
        setError(null);
      }
      try {
        const data = await loader(undefined, {
          search: def.supportsSearch && applied ? applied : undefined,
          limit: DEFAULT_RESOURCE_LIMIT,
        });
        if (cancelled) return;
        setRows(data);
        setState("ready");
      } catch (err) {
        if (cancelled) return;
        // Readable message only - never a raw stack or backend trace.
        setError(err instanceof Error ? err.message : "The resources could not be loaded.");
        setState("error");
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [def, applied, reloadKey]);

  const summaries: ResourceSummary[] = (rows ?? []).map((row) => {
    if (def?.toSummary) return def.toSummary(row);
    const id = String((row as { id?: string }).id ?? "");
    return { id, name: id || "-" };
  });

  // Local filter in addition to server-side search, for APIs without a search
  // parameter.
  const needle = applied.trim().toLowerCase();
  const visible = needle
    ? summaries.filter((s) =>
        [s.name, s.subtitle ?? "", s.status ?? ""].some((v) => v.toLowerCase().includes(needle)),
      )
    : summaries;

  const selected = Boolean(def?.implemented);

  return (
    <>
      <div style={toolbarStyle}>
        {!fixedType && (
          <>
            <label htmlFor="resource-type" style={labelStyle}>
              Resource type
            </label>
            <select
              id="resource-type"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              style={inputStyle}
            >
              {!key && <option value="">Select a resource type...</option>}
              {TYPES.map((t) => (
                <option key={t.key} value={t.key}>
                  {t.label}
                </option>
              ))}
            </select>
          </>
        )}

        {selected && def && (
          <>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                setApplied(search);
              }}
              style={{ display: "flex", gap: 6 }}
            >
              <label htmlFor="resource-search" style={srOnly}>
                Search {def.label}
              </label>
              <input
                id="resource-search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={`Search ${def.label.toLowerCase()}...`}
                style={inputStyle}
              />
              <button type="submit" style={btnStyle}>
                Search
              </button>
            </form>
            <button
              type="button"
              onClick={() => setReloadKey((k) => k + 1)}
              style={btnStyle}
              aria-label={`Refresh ${def.label}`}
            >
              Refresh
            </button>
            {state === "ready" && (
              <span style={{ fontSize: 12, color: "var(--muted)" }}>
                {visible.length} of {summaries.length} shown
              </span>
            )}
          </>
        )}
      </div>

      {!def && <EmptyState message="Select a resource type to load its resources." />}

      {def && !def.implemented && (
        <div className="hud-panel" style={{ padding: 12 }}>
          <ResourceHealth label="Coming soon" tone="idle" />
          <p style={{ fontSize: 12, color: "#a7bec7", marginTop: 8 }}>
            {def.unavailableReason ?? "This resource type is not available yet."}
          </p>
        </div>
      )}

      {selected && def && (
        <>
          {state === "loading" && <LoadingState label={`Loading ${def.label.toLowerCase()}...`} />}
          {state === "error" && (
            <ErrorState message={error ?? "The resources could not be loaded."} />
          )}
          {state === "ready" && summaries.length === 0 && (
            <EmptyState message={`No ${def.label.toLowerCase()} were returned for this tenant.`} />
          )}
          {state === "ready" && summaries.length > 0 && visible.length === 0 && (
            <EmptyState message="No resources match the current search." />
          )}
          {state === "ready" && visible.length > 0 && (
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                <thead>
                  <tr style={{ textAlign: "left", color: "var(--cyan)" }}>
                    {def.columns.map((c) => (
                      <th key={c.key} style={thStyle}>
                        {c.label}
                      </th>
                    ))}
                    <th style={thStyle}>Open</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((s) => {
                    const raw = (rows ?? [])[summaries.indexOf(s)];
                    return (
                      <tr key={s.id} style={{ borderTop: "1px solid var(--line)" }}>
                        {def.columns.map((c) => (
                          <td
                            key={c.key}
                            style={{ ...tdStyle, fontFamily: c.mono ? "monospace" : undefined }}
                          >
                            {c.value(raw)}
                          </td>
                        ))}
                        <td style={tdStyle}>
                          <Link
                            href={`/portal/resources/${def.key}/${encodeURIComponent(s.id)}`}
                            style={{ color: "var(--cyan)", fontSize: 12 }}
                          >
                            Open
                          </Link>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </>
  );
}

const srOnly: React.CSSProperties = { position: "absolute", left: -9999 };
const labelStyle: React.CSSProperties = { fontSize: 12, color: "var(--muted)" };
const toolbarStyle: React.CSSProperties = {
  display: "flex",
  gap: 8,
  flexWrap: "wrap",
  alignItems: "center",
  marginBottom: 10,
};
const inputStyle: React.CSSProperties = {
  padding: "4px 8px",
  fontSize: 12,
  background: "transparent",
  color: "inherit",
  border: "1px solid var(--line)",
  borderRadius: 6,
  minWidth: 200,
};
const btnStyle: React.CSSProperties = {
  padding: "4px 10px",
  fontSize: 12,
  background: "transparent",
  color: "var(--cyan)",
  border: "1px solid var(--cyan)",
  borderRadius: 6,
  cursor: "pointer",
};
const thStyle: React.CSSProperties = { padding: "8px 10px", whiteSpace: "nowrap" };
const tdStyle: React.CSSProperties = { padding: "8px 10px", color: "#a7bec7" };
