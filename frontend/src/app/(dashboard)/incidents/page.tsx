"use client";

// Opt out of static prerendering: the client component below reads
// useSearchParams(), which Next.js forbids during prerender without a
// Suspense boundary. We wrap it in a <Suspense> boundary (per App Router
// convention) so a loading fallback is shown while search params resolve.
export const dynamic = "force-dynamic";

import { Suspense, useEffect, useState, useCallback } from "react";
import { useSearchParams } from "next/navigation";
import {
  AlertTriangle, CheckCircle2, Bell, Activity, Zap, Shield,
  TrendingDown, Layers, ChevronDown, ChevronRight, Clock,
  Play, Archive, XCircle, Eye, Brain,
} from "lucide-react";
import {
  Incident, AlertIntelligenceMetrics, CollectorHealthList, IncidentSeverity,
  listIncidents, getAlertIntelligenceMetrics, getCollectorHealth,
  acknowledgeIncident, investigateIncident, monitorIncident,
  recoverIncident, closeIncident, archiveIncident,
} from "@/lib/api";

function timeAgo(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const minutes = Math.max(0, Math.round(diffMs / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

function duration(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const minutes = Math.max(0, Math.round(diffMs / 60000));
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.round(hours / 24)}d`;
}

const severityColor = (s: string): string => {
  if (s === "critical") return "var(--red)";
  if (s === "warning") return "var(--amber)";
  return "#119be1";
};

const statusColor = (s: string): string => {
  if (s === "open") return "var(--red)";
  if (s === "acknowledged") return "var(--amber)";
  if (s === "investigating") return "#119be1";
  if (s === "monitoring") return "var(--cyan)";
  if (s === "recovered" || s === "resolved") return "var(--green)";
  if (s === "closed") return "var(--muted)";
  if (s === "archived") return "var(--muted)";
  return "var(--muted)";
};

const stateColor = (s: string): string => {
  if (s === "healthy") return "var(--green)";
  if (s === "degraded") return "var(--amber)";
  if (s === "failing") return "var(--red)";
  return "var(--muted)";
};

function LoadingFallback() {
  return (
    <div className="dashboard" style={{ paddingTop: "clamp(84px,8.5vh,104px)" }}>
      <div className="overview" style={{ gridTemplateColumns: "1fr", gap: 16 }}>
        <section className="hud-panel">
          <h2>ALERT INTELLIGENCE METRICS</h2>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 16, marginBottom: 24 }}>
            {["Active Incidents", "Correlated", "Suppressed", "Duplicates Removed", "Noise Reduction"].map((label) => (
              <div key={label} style={{ textAlign: "center", padding: 16, border: "1px solid var(--line)", borderRadius: 8 }}>
                <div style={{ fontSize: "clamp(24px,1.8vw,36px)", color: "var(--muted)", fontWeight: 700 }}>—</div>
                <div style={{ fontSize: "clamp(10px,.65vw,12px)", color: "var(--muted)" }}>{label}</div>
              </div>
            ))}
          </div>
          <h2>CORRELATED INCIDENTS</h2>
          <p style={{ color: "var(--muted)", fontSize: 12 }}>Loading incidents…</p>
        </section>
      </div>
    </div>
  );
}

function IncidentIntelligenceContent() {
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [metrics, setMetrics] = useState<AlertIntelligenceMetrics | null>(null);
  const [collectorHealth, setCollectorHealth] = useState<CollectorHealthList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  const searchParams = useSearchParams();

  const load = useCallback(async () => {
    try {
      const severity = searchParams.get("severity") as IncidentSeverity | null;
      const [incidentList, metricsData, healthData] = await Promise.all([
        listIncidents({ severity: severity ?? undefined, limit: 50 }),
        getAlertIntelligenceMetrics(),
        getCollectorHealth(),
      ]);
      setIncidents(incidentList);
      setMetrics(metricsData);
      setCollectorHealth(healthData);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load incident intelligence");
    } finally {
      setLoaded(true);
    }
  }, [searchParams]);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, 15000);
    return () => window.clearInterval(timer);
  }, [load]);

  const handleAction = async (incidentId: string, action: string) => {
    setActionLoading(`${incidentId}:${action}`);
    try {
      if (action === "acknowledge") await acknowledgeIncident(incidentId, "00000000-0000-0000-0000-000000000000");
      else if (action === "investigate") await investigateIncident(incidentId);
      else if (action === "monitor") await monitorIncident(incidentId);
      else if (action === "recover") await recoverIncident(incidentId);
      else if (action === "close") await closeIncident(incidentId);
      else if (action === "archive") await archiveIncident(incidentId);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : `Failed to ${action} incident`);
    } finally {
      setActionLoading(null);
    }
  };

  return (
    <div className="dashboard" style={{ paddingTop: "clamp(84px,8.5vh,104px)" }}>
      <div className="overview" style={{ gridTemplateColumns: "1fr", gap: 16 }}>
        {/* Alert Intelligence Metrics */}
        <section className="hud-panel">
          <h2><Activity size={18} style={{ display: "inline", marginRight: 8 }} />ALERT INTELLIGENCE METRICS</h2>
          {error && (
            <p style={{ display: "flex", gap: 6, alignItems: "center", color: "var(--amber)", fontSize: 12 }}>
              <AlertTriangle size={14} /> {error}
            </p>
          )}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 16, marginBottom: 24 }}>
            {[
              { label: "Active Incidents", value: metrics?.active_incidents ?? 0, icon: AlertTriangle, color: "var(--red)" },
              { label: "Correlated", value: metrics?.correlated_incidents ?? 0, icon: Layers, color: "var(--cyan)" },
              { label: "Suppressed", value: metrics?.suppressed_alerts ?? 0, icon: TrendingDown, color: "var(--amber)" },
              { label: "Duplicates Removed", value: metrics?.duplicate_alerts_removed ?? 0, icon: XCircle, color: "var(--amber)" },
              { label: "Noise Reduction", value: `${metrics?.noise_reduction_pct ?? 0}%`, icon: Zap, color: "var(--green)" },
            ].map(({ label, value, icon: Icon, color }) => (
              <div key={label} style={{ textAlign: "center", padding: 16, border: "1px solid var(--line)", borderRadius: 8 }}>
                <Icon size={20} style={{ color, margin: "0 auto 8px" }} />
                <div style={{ fontSize: "clamp(24px,1.8vw,36px)", color, fontWeight: 700 }}>{value}</div>
                <div style={{ fontSize: "clamp(10px,.65vw,12px)", color: "var(--muted)" }}>{label}</div>
              </div>
            ))}
          </div>
        </section>

        {/* Collector Health */}
        {collectorHealth && collectorHealth.collectors.length > 0 && (
          <section className="hud-panel">
            <h2><Shield size={18} style={{ display: "inline", marginRight: 8 }} />COLLECTOR HEALTH</h2>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 12, marginBottom: 16 }}>
              {[
                { label: "Healthy", value: collectorHealth.healthy_count, color: "var(--green)" },
                { label: "Degraded", value: collectorHealth.degraded_count, color: "var(--amber)" },
                { label: "Failing", value: collectorHealth.failing_count, color: "var(--red)" },
                { label: "Unknown", value: collectorHealth.unknown_count, color: "var(--muted)" },
              ].map(({ label, value, color }) => (
                <div key={label} style={{ textAlign: "center", padding: 12, border: "1px solid var(--line)", borderRadius: 8 }}>
                  <div style={{ fontSize: 28, color, fontWeight: 700 }}>{value}</div>
                  <div style={{ fontSize: 11, color: "var(--muted)" }}>{label}</div>
                </div>
              ))}
            </div>
            <div style={{ overflowY: "auto", maxHeight: 200 }}>
              {collectorHealth.collectors.map((c) => (
                <div key={c.source_id} className="alert-row" style={{ padding: "8px 12px" }}>
                  <i style={{ background: stateColor(c.current_state) }} />
                  <div style={{ flex: 1 }}>
                    <span style={{ fontSize: 13 }}>{c.source_name}</span>
                    <small style={{ display: "block", color: "var(--muted)", fontSize: 11 }}>
                      {c.current_state} · {c.success_rate_pct}% success · {c.total_runs} runs
                      {c.last_error && ` · ${c.last_error.substring(0, 60)}`}
                    </small>
                  </div>
                  <time style={{ fontSize: 11, color: "var(--muted)" }}>
                    {c.last_success_at ? timeAgo(c.last_success_at) : "—"}
                  </time>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* Incident Cards */}
        <section className="hud-panel">
          <h2><Bell size={18} style={{ display: "inline", marginRight: 8 }} />CORRELATED INCIDENTS ({incidents.length})</h2>
          {!loaded && <p style={{ color: "var(--muted)", fontSize: 12 }}>Loading incidents…</p>}
          {loaded && incidents.length === 0 && (
            <div style={{ textAlign: "center", padding: 32 }}>
              <CheckCircle2 size={48} style={{ color: "var(--green)", margin: "0 auto 12px" }} />
              <p style={{ color: "var(--muted)" }}>No active incidents. All systems operational.</p>
            </div>
          )}
          <div style={{ overflowY: "auto", maxHeight: 600 }}>
            {incidents.map((inc) => {
              const isExpanded = expandedId === inc.id;
              const evidence = (inc.supporting_evidence as Record<string, unknown[]>)?.evidence as
                { alert_id: string; title: string; reason: string; severity: string; category: string }[] | undefined;
              const assets = (inc.affected_assets as unknown as Record<string, unknown[]>)?.assets as
                { id: string; type: string; name: string }[] | undefined;
              return (
                <div key={inc.id} style={{ border: "1px solid var(--line)", borderRadius: 8, marginBottom: 12, overflow: "hidden" }}>
                  {/* Card Header */}
                  <div
                    className="alert-row"
                    style={{ cursor: "pointer", padding: "12px 16px" }}
                    onClick={() => setExpandedId(isExpanded ? null : inc.id)}
                  >
                    <i style={{ background: severityColor(inc.severity) }} />
                    <div style={{ flex: 1 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                        <span style={{ fontSize: 14, fontWeight: 600 }}>{inc.title}</span>
                        {/* Severity badge */}
                        <span style={{
                          padding: "2px 8px", borderRadius: 4, fontSize: 10, fontWeight: 700,
                          background: severityColor(inc.severity), color: "#fff",
                        }}>
                          {inc.severity.toUpperCase()}
                        </span>
                        {/* Status badge */}
                        <span style={{
                          padding: "2px 8px", borderRadius: 4, fontSize: 10,
                          border: `1px solid ${statusColor(inc.status)}`, color: statusColor(inc.status),
                        }}>
                          {inc.status}
                        </span>
                        {/* AI badge */}
                        {inc.correlation_type && (
                          <span style={{
                            padding: "2px 8px", borderRadius: 4, fontSize: 10,
                            background: "rgba(17,155,225,0.15)", color: "#119be1",
                            display: "flex", alignItems: "center", gap: 4,
                          }}>
                            <Brain size={10} /> {inc.correlation_type}
                          </span>
                        )}
                        {/* Automation badge */}
                        {inc.automation_available && (
                          <span style={{
                            padding: "2px 8px", borderRadius: 4, fontSize: 10,
                            background: "rgba(0,200,83,0.15)", color: "var(--green)",
                            display: "flex", alignItems: "center", gap: 4,
                          }}>
                            <Zap size={10} /> Auto
                          </span>
                        )}
                      </div>
                      <small style={{ display: "block", color: "var(--muted)", fontSize: 11, marginTop: 4 }}>
                        {inc.alert_count} alerts · {inc.total_occurrences} occurrences · {duration(inc.opened_at)} duration
                        {inc.device_name && ` · ${inc.device_name}`}
                        {inc.site_name && ` · ${inc.site_name}`}
                      </small>
                    </div>
                    {isExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                  </div>

                  {/* Expanded Details */}
                  {isExpanded && (
                    <div style={{ padding: "16px", borderTop: "1px solid var(--line)", background: "rgba(0,0,0,0.2)" }}>
                      {/* Root Cause Summary */}
                      {inc.root_cause_summary && (
                        <div style={{ marginBottom: 16 }}>
                          <h3 style={{ fontSize: 12, color: "var(--cyan)", marginBottom: 4, display: "flex", alignItems: "center", gap: 6 }}>
                            <Brain size={14} /> AI ROOT CAUSE
                          </h3>
                          <p style={{ fontSize: 12, color: "var(--muted)", lineHeight: 1.5 }}>{inc.root_cause_summary}</p>
                        </div>
                      )}

                      {/* Supporting Evidence */}
                      {evidence && evidence.length > 0 && (
                        <div style={{ marginBottom: 16 }}>
                          <h3 style={{ fontSize: 12, color: "var(--cyan)", marginBottom: 4 }}>SUPPORTING EVIDENCE</h3>
                          {evidence.map((ev, i) => (
                            <div key={i} style={{ display: "flex", gap: 8, fontSize: 11, color: "var(--muted)", marginBottom: 4 }}>
                              <span style={{ color: severityColor(ev.severity) }}>●</span>
                              <span>{ev.title}</span>
                              <span style={{ opacity: 0.6 }}>({ev.reason})</span>
                            </div>
                          ))}
                        </div>
                      )}

                      {/* Affected Assets */}
                      {assets && assets.length > 0 && (
                        <div style={{ marginBottom: 16 }}>
                          <h3 style={{ fontSize: 12, color: "var(--cyan)", marginBottom: 4 }}>AFFECTED ASSETS</h3>
                          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                            {assets.map((a, i) => (
                              <span key={i} style={{
                                padding: "2px 8px", borderRadius: 4, fontSize: 11,
                                border: "1px solid var(--line)", color: "var(--muted)",
                              }}>
                                {a.type}: {a.name}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* Timeline */}
                      <div style={{ marginBottom: 16, display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: 8 }}>
                        {[
                          { label: "First Seen", value: timeAgo(inc.opened_at), icon: Clock },
                          { label: "Last Seen", value: timeAgo(inc.last_alert_at), icon: Clock },
                          { label: "Assigned", value: inc.assigned_team || "Unassigned", icon: Eye },
                        ].map(({ label, value, icon: Icon }) => (
                          <div key={label} style={{ padding: 8, border: "1px solid var(--line)", borderRadius: 6 }}>
                            <Icon size={12} style={{ color: "var(--muted)", marginBottom: 4 }} />
                            <div style={{ fontSize: 11, color: "var(--muted)" }}>{label}</div>
                            <div style={{ fontSize: 12 }}>{value}</div>
                          </div>
                        ))}
                      </div>

                      {/* Quick Actions */}
                      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                        {inc.status === "open" && (
                          <button
                            className="hud-btn"
                            disabled={actionLoading === `${inc.id}:acknowledge`}
                            onClick={(e) => { e.stopPropagation(); handleAction(inc.id, "acknowledge"); }}
                            style={{ fontSize: 11, padding: "4px 12px" }}
                          >
                            <CheckCircle2 size={12} /> Acknowledge
                          </button>
                        )}
                        {(inc.status === "acknowledged" || inc.status === "open") && (
                          <button
                            className="hud-btn"
                            disabled={actionLoading === `${inc.id}:investigate`}
                            onClick={(e) => { e.stopPropagation(); handleAction(inc.id, "investigate"); }}
                            style={{ fontSize: 11, padding: "4px 12px" }}
                          >
                            <Eye size={12} /> Investigate
                          </button>
                        )}
                        {inc.status === "investigating" && (
                          <button
                            className="hud-btn"
                            disabled={actionLoading === `${inc.id}:monitor`}
                            onClick={(e) => { e.stopPropagation(); handleAction(inc.id, "monitor"); }}
                            style={{ fontSize: 11, padding: "4px 12px" }}
                          >
                            <Activity size={12} /> Monitor
                          </button>
                        )}
                        {(inc.status === "investigating" || inc.status === "monitoring") && (
                          <button
                            className="hud-btn"
                            disabled={actionLoading === `${inc.id}:recover`}
                            onClick={(e) => { e.stopPropagation(); handleAction(inc.id, "recover"); }}
                            style={{ fontSize: 11, padding: "4px 12px" }}
                          >
                            <CheckCircle2 size={12} /> Recover
                          </button>
                        )}
                        {(inc.status === "recovered" || inc.status === "resolved") && (
                          <button
                            className="hud-btn"
                            disabled={actionLoading === `${inc.id}:close`}
                            onClick={(e) => { e.stopPropagation(); handleAction(inc.id, "close"); }}
                            style={{ fontSize: 11, padding: "4px 12px" }}
                          >
                            <XCircle size={12} /> Close
                          </button>
                        )}
                        {inc.status === "closed" && (
                          <button
                            className="hud-btn"
                            disabled={actionLoading === `${inc.id}:archive`}
                            onClick={(e) => { e.stopPropagation(); handleAction(inc.id, "archive"); }}
                            style={{ fontSize: 11, padding: "4px 12px" }}
                          >
                            <Archive size={12} /> Archive
                          </button>
                        )}
                        {inc.automation_available && (
                          <button
                            className="hud-btn"
                            style={{ fontSize: 11, padding: "4px 12px", borderColor: "var(--green)", color: "var(--green)" }}
                            onClick={(e) => e.stopPropagation()}
                          >
                            <Play size={12} /> Run SOP
                          </button>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </section>
      </div>
    </div>
  );
}

export default function IncidentIntelligencePage() {
  return (
    <Suspense fallback={<LoadingFallback />}>
      <IncidentIntelligenceContent />
    </Suspense>
  );
}
