"use client";

/**
 * /settings/audit-log — who did what in this organization (enterprise security
 * programme S1). Reads GET /api/audit/events (audit.read: Owner, Admin) with
 * filters; any row opens its before / after values; the filtered log exports
 * as CSV, and the export itself is recorded.
 */

import { Fragment, useEffect, useMemo, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronLeft, ChevronRight, Download, History } from "lucide-react";
import { Banner, PageShell, Panel, Pill, SearchBox, ago, titleCaseWords } from "@/components/ui/PageKit";
import { useAuth } from "@/lib/auth-context";
import { downloadAuditCsv, getAuditFacets, listAuditEvents, type AuditEvent, type AuditFilters } from "@/lib/security";

const PAGE = 50;
const WINDOWS = [
  { key: "24h", label: "Last 24 hours", hours: 24 },
  { key: "7d", label: "Last 7 days", hours: 24 * 7 },
  { key: "30d", label: "Last 30 days", hours: 24 * 30 },
  { key: "90d", label: "Last 90 days", hours: 24 * 90 },
  { key: "all", label: "All time", hours: 0 },
];

/** "user.roles_changed" -> tone by what kind of change it is. */
function actionTone(action: string): "bad" | "warn" | "info" | "neutral" | "good" {
  if (/failed|deleted|revoked|signed_out|ended|locked/.test(action)) return "warn";
  if (/security\.|role\.|roles_changed|password|support_access|exported/.test(action)) return "info";
  if (/created|accepted|verified/.test(action)) return "good";
  return "neutral";
}

function Values({ label, values }: { label: string; values: Record<string, unknown> }) {
  const entries = Object.entries(values ?? {});
  if (!entries.length) return null;
  return (
    <div className="al-values">
      <h4>{label}</h4>
      <dl>
        {entries.map(([k, v]) => (
          <Fragment key={k}>
            <dt>{k}</dt>
            <dd>{typeof v === "object" && v !== null ? JSON.stringify(v) : String(v)}</dd>
          </Fragment>
        ))}
      </dl>
    </div>
  );
}

export default function AuditLogPage() {
  const { user } = useAuth();
  const allowed = !!user && (user.is_superuser || user.permissions.includes("audit.read"));
  const [window_, setWindow] = useState("7d");
  const [action, setAction] = useState("");
  const [actorInput, setActorInput] = useState("");
  const [qInput, setQInput] = useState("");
  const [actor, setActor] = useState("");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Debounce the free-text filters.
  useEffect(() => {
    const t = window.setTimeout(() => { setActor(actorInput.trim()); setQ(qInput.trim()); setOffset(0); }, 300);
    return () => window.clearTimeout(t);
  }, [actorInput, qInput]);

  // The window is part of the query key; its start time is computed when the request runs.
  const base = { action: action || undefined, actor: actor || undefined, q: q || undefined };
  const withSince = (): AuditFilters => {
    const hours = WINDOWS.find((w) => w.key === window_)?.hours ?? 0;
    return { ...base, since: hours ? new Date(Date.now() - hours * 3600_000).toISOString() : undefined };
  };

  const facets = useQuery({ queryKey: ["audit", "facets"], queryFn: getAuditFacets, enabled: allowed, staleTime: 60_000 });
  const page = useQuery({
    queryKey: ["audit", "events", base, window_, offset],
    queryFn: () => listAuditEvents(withSince(), PAGE, offset),
    enabled: allowed,
    placeholderData: keepPreviousData,
  });
  const groups = useMemo(() => {
    const prefixes = new Set((facets.data?.actions ?? []).map((a) => a.split(".")[0]));
    return [...prefixes].sort();
  }, [facets.data]);

  async function exportCsv() {
    setExporting(true);
    setError(null);
    try {
      await downloadAuditCsv(withSince());
    } catch (e) {
      setError(e instanceof Error ? e.message : "The export failed.");
    } finally {
      setExporting(false);
    }
  }

  const data = page.data;
  return (
    <PageShell
      title="Audit log"
      icon={History}
      subtitle="Every security-relevant change in your organization: sign-ins, users and roles, policy, access and exports."
      crumbs={[{ label: "Settings", href: "/settings" }, { label: "Security", href: "/settings/security" }, { label: "Audit log" }]}
      actions={allowed ? (
        <button type="button" className="pk-button" onClick={exportCsv} disabled={exporting}>
          <Download size={14} aria-hidden="true" /> {exporting ? "Exporting…" : "Export CSV"}
        </button>
      ) : undefined}
    >
      {!allowed ? (
        <Banner tone="info">The audit log needs the audit.read permission (Owner or Admin).</Banner>
      ) : (
        <Panel
          title={data ? `Events ${data.total.toLocaleString("en-US")}` : "Events"}
          state={data ? "ready" : page.isError ? "error" : "loading"}
          onRetry={() => page.refetch()}
          skeletonHeight={420}
          aside={data && data.total > 0 ? `${offset + 1}–${Math.min(offset + PAGE, data.total)} of ${data.total.toLocaleString("en-US")}` : undefined}
        >
          {error && <Banner tone="bad">{error}</Banner>}
          <div className="pk-toolbar al-toolbar">
            <label className="ecc-sr-only" htmlFor="al-window">Time window</label>
            <select id="al-window" className="pk-input" value={window_} onChange={(e) => { setWindow(e.target.value); setOffset(0); }}>
              {WINDOWS.map((w) => <option key={w.key} value={w.key}>{w.label}</option>)}
            </select>
            <label className="ecc-sr-only" htmlFor="al-action">Action</label>
            <select id="al-action" className="pk-input" value={action} onChange={(e) => { setAction(e.target.value); setOffset(0); }}>
              <option value="">All actions</option>
              {groups.map((g) => (
                <optgroup key={g} label={titleCaseWords(g.replace(/_/g, " "))}>
                  <option value={`${g}.`}>All {g.replace(/_/g, " ")} events</option>
                  {(facets.data?.actions ?? []).filter((a) => a.startsWith(`${g}.`)).map((a) => <option key={a} value={a}>{a}</option>)}
                </optgroup>
              ))}
            </select>
            <SearchBox value={actorInput} onChange={setActorInput} placeholder="Actor email" label="Filter by actor" />
            <SearchBox value={qInput} onChange={setQInput} placeholder="Search descriptions" label="Search audit events" />
          </div>

          <div className="pk-table-wrap" style={{ maxHeight: "calc(100vh - 330px)" }}>
            <table className="pk-table al-table">
              <caption className="ecc-sr-only">Audit events</caption>
              <thead>
                <tr>
                  <th scope="col" style={{ width: 150 }}>When</th>
                  <th scope="col" style={{ width: 210 }}>Action</th>
                  <th scope="col" style={{ width: 220 }}>Actor</th>
                  <th scope="col">What happened</th>
                  <th scope="col" style={{ width: 36 }}><span className="ecc-sr-only">Details</span></th>
                </tr>
              </thead>
              <tbody>
                {data && data.rows.length === 0 && (
                  <tr><td colSpan={5} style={{ textAlign: "center", padding: 28, color: "var(--ecc-text-muted)" }}>No events match these filters.</td></tr>
                )}
                {(data?.rows ?? []).map((ev: AuditEvent) => {
                  const expanded = open === ev.id;
                  const hasValues = Object.keys(ev.old_values ?? {}).length + Object.keys(ev.new_values ?? {}).length > 0;
                  return (
                    <Fragment key={ev.id}>
                      <tr className={hasValues ? "pk-row--link" : undefined} onClick={hasValues ? () => setOpen(expanded ? null : ev.id) : undefined}>
                        <td title={new Date(ev.created_at).toLocaleString()}>
                          <span className="pk-cell-main">{ago(ev.created_at)}</span>
                          <span className="pk-cell-sub">{new Date(ev.created_at).toLocaleString()}</span>
                        </td>
                        <td><Pill tone={actionTone(ev.action)} dot={false}>{ev.action}</Pill></td>
                        <td>
                          <span className="pk-cell-main">{ev.actor_email ?? (ev.actor_type === "system" ? "System" : "—")}</span>
                          {ev.ip_address && <span className="pk-cell-sub">{ev.ip_address}</span>}
                        </td>
                        <td>{ev.description ?? <span className="pk-muted">—</span>}</td>
                        <td>
                          {hasValues && (
                            <button type="button" className="al-expand" aria-expanded={expanded} aria-label={expanded ? "Hide details" : "Show details"}
                              onClick={(e) => { e.stopPropagation(); setOpen(expanded ? null : ev.id); }}>
                              <ChevronDown size={15} style={{ transform: expanded ? "rotate(180deg)" : undefined }} />
                            </button>
                          )}
                        </td>
                      </tr>
                      {expanded && (
                        <tr className="al-detail">
                          <td colSpan={5}>
                            <div className="al-detail__grid">
                              <Values label="Before" values={ev.old_values} />
                              <Values label="After" values={ev.new_values} />
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
          {data && data.total > PAGE && (
            <div className="pk-table-foot">
              <span>Page {Math.floor(offset / PAGE) + 1} of {Math.ceil(data.total / PAGE)}</span>
              <span style={{ display: "inline-flex", gap: 6 }}>
                <button type="button" className="pk-button" style={{ height: 28 }} disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
                  <ChevronLeft size={14} /> Previous
                </button>
                <button type="button" className="pk-button" style={{ height: 28 }} disabled={offset + PAGE >= data.total} onClick={() => setOffset(offset + PAGE)}>
                  Next <ChevronRight size={14} />
                </button>
              </span>
            </div>
          )}
        </Panel>
      )}
    </PageShell>
  );
}
