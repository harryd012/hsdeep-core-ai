"use client";

/**
 * /portal/resources/[resourceType]/[resourceId] — generic Resource Detail.
 *
 * Driven entirely by the registry; there is no per-resource-type code. A tab
 * renders content only when the registry marks it available - otherwise it shows
 * an explicit not-available state. No fake charts, fake alerts or fake AI.
 *
 * Detail data loads only after a specific resource is selected. Where the API has
 * no single-resource getter, the row is resolved from the same BOUNDED list the
 * explorer uses rather than inventing an endpoint.
 *
 * Secret-shaped properties are filtered defensively so a credential cannot reach
 * the DOM even if a backend projection regresses.
 */

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import {
  EmptyState,
  ErrorState,
  LoadingState,
  PortalShell,
  ResourceHealth,
} from "@/components/portal/PortalShell";
import IncidentInvestigation from "@/components/portal/IncidentInvestigation";
import { getResourceType } from "@/lib/resources/resource-registry";
import {
  DEFAULT_RESOURCE_LIMIT,
  type ResourceDetail,
  type ResourceTabKey,
} from "@/lib/resources/resource-types";

type LoadState = "loading" | "ready" | "not_found" | "error";

/** Never render anything secret-shaped. */
const SECRET_KEY = /pass|secret|token|hash|credential|api[_-]?key|community/i;

function renderValue(v: unknown): string {
  if (v === null || v === undefined || v === "") return "-";
  if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") return String(v);
  if (Array.isArray(v)) return v.length ? v.map(renderValue).join(", ") : "-";
  if (typeof v === "object") {
    const parts = Object.entries(v as Record<string, unknown>)
      .filter(([k]) => !SECRET_KEY.test(k))
      .map(([k, val]) => `${k}: ${renderValue(val)}`);
    return parts.length ? parts.join("; ") : "-";
  }
  return String(v);
}

function PropertyList({ properties }: { properties: Record<string, unknown> }) {
  const entries = Object.entries(properties).filter(([k, v]) => !SECRET_KEY.test(k) && v != null);
  if (entries.length === 0) return <EmptyState message="No properties were returned." />;
  return (
    <dl style={dlStyle}>
      {entries.map(([k, v]) => (
        <div key={k} style={{ display: "contents" }}>
          <dt style={{ fontSize: 12, color: "var(--muted)" }}>{k}</dt>
          <dd style={{ margin: 0, fontSize: 13, color: "#a7bec7" }}>{renderValue(v)}</dd>
        </div>
      ))}
    </dl>
  );
}

export default function ResourceDetailPage() {
  const params = useParams<{ resourceType: string; resourceId: string }>();
  const typeKey = String(params?.resourceType ?? "");
  const resourceId = String(params?.resourceId ?? "");
  const def = getResourceType(typeKey);

  const [detail, setDetail] = useState<ResourceDetail | null>(null);
  const [state, setState] = useState<LoadState>("loading");
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<ResourceTabKey>("overview");
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      if (!def?.implemented) {
        if (!cancelled) {
          setState("not_found");
          setDetail(null);
        }
        return;
      }
      if (!cancelled) {
        setState("loading");
        setError(null);
      }
      try {
        let row: unknown = undefined;
        if (def.detail) {
          row = await def.detail(resourceId, undefined);
        } else if (def.list) {
          // Bounded resolution: no per-row detail requests and no invented endpoint.
          const rows = await def.list(undefined, { limit: DEFAULT_RESOURCE_LIMIT });
          row = rows.find((r) => String((r as { id?: string }).id ?? "") === resourceId);
        }
        if (cancelled) return;
        if (!row) {
          setDetail(null);
          setState("not_found");
          return;
        }
        setDetail({
          summary: def.toSummary ? def.toSummary(row) : { id: resourceId, name: resourceId },
          properties: def.toProperties ? def.toProperties(row) : {},
          description: def.toDescription?.(row),
        });
        setState("ready");
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "The resource could not be loaded.");
        setState("error");
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [def, resourceId, reloadKey]);

  const tabs = def?.tabs ?? [];
  const head = detail?.summary;

  return (
    <PortalShell
      breadcrumb={["Portal", "Resources", def?.label ?? typeKey, head?.name ?? resourceId]}
      title={head?.name ?? "Resource"}
      description={def?.label}
      actions={
        <button type="button" onClick={() => setReloadKey((k) => k + 1)} style={btnStyle}>
          Refresh
        </button>
      }
    >
      {state === "loading" && <LoadingState />}
      {state === "error" && <ErrorState message={error ?? "The resource could not be loaded."} />}
      {state === "not_found" && (
        <>
          <p style={{ color: "var(--amber)", fontSize: 14, marginBottom: 4 }}>RESOURCE NOT FOUND</p>
          <EmptyState message="The requested resource could not be found." />
          <Link href="/portal/resources" style={{ color: "var(--cyan)", fontSize: 12 }}>
            Back to Resources
          </Link>
        </>
      )}

        {typeKey === "incidents" && <IncidentInvestigation incidentId={resourceId} />}

        {typeKey !== "incidents" && state === "ready" && detail && head && (
        <>
          <div className="hud-panel" style={{ padding: 12, marginBottom: 12 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <strong style={{ fontSize: 15 }}>{head.name}</strong>
              {head.status && (
                <span style={{ fontSize: 12, color: "var(--muted)" }}>{head.status}</span>
              )}
              {head.health && <ResourceHealth label={head.health.label} tone={head.health.tone} />}
            </div>
            <p style={{ fontSize: 12, color: "var(--muted)", margin: "6px 0 0" }}>
              {[
                def?.label,
                head.site ? `Site: ${head.site}` : null,
                head.vendor ? `Vendor: ${head.vendor}` : null,
                head.category ? `Type: ${head.category}` : null,
                head.lastUpdated ? `Updated: ${head.lastUpdated}` : null,
              ]
                .filter(Boolean)
                .join(" \u00b7 ")}
            </p>
          </div>

          <div role="tablist" aria-label="Resource sections" style={tabListStyle}>
            {tabs.map((t) => (
              <button
                key={t.key}
                role="tab"
                type="button"
                aria-selected={activeTab === t.key}
                title={t.available ? undefined : (t.unavailableReason ?? "Not available")}
                onClick={() => setActiveTab(t.key)}
                style={{
                  padding: "4px 10px",
                  fontSize: 12,
                  background: activeTab === t.key ? "rgba(0,255,247,0.06)" : "transparent",
                  color: t.available ? "var(--cyan)" : "var(--muted)",
                  border: "1px solid var(--line)",
                  borderRadius: 6,
                  cursor: "pointer",
                }}
              >
                {t.label}
              </button>
            ))}
          </div>

          {tabs.map((t) =>
            t.key !== activeTab ? null : (
              <div key={t.key} role="tabpanel" aria-label={t.label}>
                {!t.available && (
                  <EmptyState message={t.unavailableReason ?? "Not available for this resource type."} />
                )}
                {t.available && t.key === "overview" && (
                  <>
                    {detail.description && (
                      <p style={{ fontSize: 13, color: "#a7bec7", marginTop: 0 }}>{detail.description}</p>
                    )}
                    <PropertyList properties={detail.properties} />
                  </>
                )}
                {t.available && t.key === "properties" && <PropertyList properties={detail.properties} />}
              </div>
            ),
          )}
        </>
      )}
    </PortalShell>
  );
}

const btnStyle: React.CSSProperties = {
  padding: "4px 10px",
  fontSize: 12,
  background: "transparent",
  color: "var(--cyan)",
  border: "1px solid var(--cyan)",
  borderRadius: 6,
  cursor: "pointer",
};
const tabListStyle: React.CSSProperties = {
  display: "flex",
  gap: 6,
  flexWrap: "wrap",
  marginBottom: 10,
};
const dlStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "220px 1fr",
  rowGap: 6,
  columnGap: 12,
  margin: 0,
};
