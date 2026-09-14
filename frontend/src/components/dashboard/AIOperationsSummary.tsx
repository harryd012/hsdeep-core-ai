"use client";

import { memo, useEffect, useState } from "react";
import { Server } from "lucide-react";
import { getAIInsights } from "@/lib/api";
import Panel from "./Panel";

const REFRESH_MS = 60000;

interface RawAIInsight {
  kind?: string;
  type?: string;
  severity?: string;
  priority?: string;
  message?: string;
  summary?: string;
  device_id?: string | null;
}

const AIOperationsSummary = memo(function AIOperationsSummary() {
  const [insights, setInsights] = useState<{ id: string; severity: string; summary: string; kind: string; devices: string }[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const data = await getAIInsights();
        if (cancelled) return;
        setInsights(data.insights.slice(0, 6).map((insight: RawAIInsight, idx: number) => ({
          id: `${insight.kind ?? insight.type ?? "insight"}-${idx}`,
          severity: insight.severity ?? insight.priority ?? "info",
          summary: insight.message ?? insight.summary ?? "No summary",
          kind: insight.kind ?? insight.type ?? "insight",
          devices: insight.device_id ? "1 device" : "Multiple devices",
        })));
        setLoadError(null);
      } catch (err) {
        // BUG FIX: this previously left `loaded` false forever on any
        // persistent failure, so the panel got stuck on "Loading AI
        // summary…" indefinitely instead of ever showing an error state.
        // `loaded` must flip regardless of outcome; only the error message
        // differs between success and failure.
        if (cancelled) return;
        setLoadError(err instanceof Error ? err.message : "AI insights are unavailable right now.");
      } finally {
        if (!cancelled) setLoaded(true);
      }
    }
    load();
    const timer = window.setInterval(load, REFRESH_MS);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, []);

  const severityColor: Record<string, string> = { critical: "var(--red)", warning: "var(--amber)", info: "var(--cyan)" };

  return (
    <Panel title="AI OPERATIONS SUMMARY" status={insights.some(i => i.severity === "critical") ? "warning" : "healthy"} className="ai-summary">
      {!loaded && (
        <div className="ai-loading">Loading AI summary…</div>
      )}
      {loaded && loadError && (
        <div className="ai-empty">
          <div className="ai-empty-icon">!</div>
          <div>
            <b>Couldn't load AI insights.</b>
            <small>{loadError}</small>
          </div>
        </div>
      )}
      {loaded && !loadError && insights.length === 0 && (
        <div className="ai-empty">
          <div className="ai-empty-icon">✓</div>
          <div>
            <b>No insights at this time.</b>
            <small>AI correlation engine is running normally.</small>
          </div>
        </div>
      )}
      <div className="ai-cards">
        {insights.map((insight) => (
          <div key={insight.id} className="ai-card">
            <div className="ai-card-header">
              <span className="ai-severity" style={{ color: severityColor[insight.severity] || "var(--cyan)" }}>
                {insight.severity.toUpperCase()}
              </span>
              <span className="ai-confidence">{insight.kind.replace(/_/g, " ").toUpperCase()}</span>
            </div>
            <p className="ai-summary">{insight.summary}</p>
            <div className="ai-meta">
              <span><Server size={12} /> Affected devices: {insight.devices}</span>
            </div>
            <div className="ai-impact" style={{ borderColor: severityColor[insight.severity] || "var(--cyan)" }}>
              <strong>Estimated Impact</strong>
              <small>{insight.severity === "critical" ? "High" : insight.severity === "warning" ? "Medium" : "Low"}</small>
            </div>
          </div>
        ))}
      </div>
    </Panel>
  );
});

export default AIOperationsSummary;