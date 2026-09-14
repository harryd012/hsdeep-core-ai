"use client";

import { memo, useEffect, useState } from "react";
import { AlertTriangle, X, ChevronDown } from "lucide-react";
import { getAlertSummary, getAIInsights, listAlerts, AIInsight, Alert } from "@/lib/api";
import { groupAlertsByFingerprint, timeAgo, type GroupedAlert } from "@/lib/alert-dedup";

interface Issue {
  id: string;
  type: "alert" | "ai";
  severity: string;
  title: string;
  description: string;
  occurrenceCount?: number;
  firstSeen?: string;
  lastSeen?: string;
  occurrences?: Alert[];
}

const IssueBadge = memo(function IssueBadge() {
  const [issues, setIssues] = useState<Issue[]>([]);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [expandedIssue, setExpandedIssue] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [summary, ai, alerts] = await Promise.all([
          getAlertSummary().catch(() => null),
          getAIInsights().catch(() => ({ insights: [] })),
          listAlerts({ status: "open", limit: 100 }).catch(() => []),
        ]);
        if (cancelled) return;

        const list: Issue[] = [];

        // Group alerts by fingerprint for the System Issues drawer
        const groupedAlerts = groupAlertsByFingerprint(alerts);
        const criticalAlerts = groupedAlerts.filter((a) => a.severity === "critical");

        if (criticalAlerts.length > 0) {
          criticalAlerts.forEach((ga) => {
            list.push({
              id: `alert-${ga.fingerprint}`,
              type: "alert",
              severity: "critical",
              title: ga.title,
              description: `${ga.occurrenceCount > 1 ? `${ga.occurrenceCount} occurrences` : "1 occurrence"} · Last seen ${timeAgo(ga.lastSeen)}`,
              occurrenceCount: ga.occurrenceCount,
              firstSeen: ga.firstSeen,
              lastSeen: ga.lastSeen,
              occurrences: ga.alerts,
            });
          });
        }

        // Critical AI insights
        const criticalInsights = ai?.insights.filter((i: AIInsight) => i.severity === "critical") ?? [];
        criticalInsights.forEach((insight: AIInsight, idx: number) => {
          list.push({
            id: `ai-${idx}`,
            type: "ai",
            severity: "critical",
            title: insight.message,
            description: `AI correlation: ${insight.kind}`,
          });
        });

        setIssues(list);
        setLoaded(true);
      } catch {
        if (!cancelled) setLoaded(true);
      }
    }
    load();
    const timer = setInterval(load, 30000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  if (!loaded || issues.length === 0) {
    return null;
  }

  return (
    <>
      {/* Badge — bottom-left, above chat bar, not clipped */}
      <div
        className="issue-badge"
        role="button"
        tabIndex={0}
        aria-label={`${issues.length} issue(s) — click to view details`}
        onClick={() => setDrawerOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setDrawerOpen(true);
          }
        }}
      >
        <AlertTriangle size={14} />
        <span>{issues.length} Issue{issues.length !== 1 ? "s" : ""}</span>
      </div>

      {/* Drawer overlay */}
      {drawerOpen && (
        <>
          <div
            className="issue-drawer-scrim"
            onClick={() => setDrawerOpen(false)}
            aria-label="Close issues drawer"
          />
          <aside className="issue-drawer" role="dialog" aria-label="System Issues">
            <div className="issue-drawer__header">
              <h3>System Issues ({issues.length})</h3>
              <button
                className="issue-drawer__close"
                onClick={() => setDrawerOpen(false)}
                aria-label="Close"
              >
                <X size={16} />
              </button>
            </div>
            <div className="issue-drawer__body">
              {issues.map((issue) => (
                <div key={issue.id} className={`issue-drawer__item issue-drawer__item--${issue.severity}`}>
                  <span className="issue-drawer__icon">
                    <AlertTriangle size={16} />
                  </span>
                  <div className="issue-drawer__content">
                    <strong>{issue.title}</strong>
                    <small>{issue.description}</small>
                  </div>
                  <span className="issue-drawer__type">{issue.type}</span>
                  {issue.occurrenceCount && issue.occurrenceCount > 1 && (
                    <span
                      className="issue-drawer__occurrence-badge"
                      style={{
                        display: "inline-block",
                        padding: "2px 6px",
                        fontSize: 10,
                        fontWeight: 600,
                        backgroundColor: "var(--amber)",
                        color: "#000",
                        borderRadius: 4,
                        marginLeft: 8,
                      }}
                    >
                      {issue.occurrenceCount}×
                    </span>
                  )}
                  {issue.occurrences && issue.occurrences.length > 1 && (
                    <button
                      className="issue-drawer__expand"
                      onClick={() => setExpandedIssue(expandedIssue === issue.id ? null : issue.id)}
                      aria-label="Expand occurrences"
                      aria-expanded={expandedIssue === issue.id}
                      style={{
                        background: "none",
                        border: "none",
                        cursor: "pointer",
                        padding: 4,
                        marginLeft: 8,
                      }}
                    >
                      <ChevronDown
                        size={14}
                        style={{
                          transition: "transform 0.2s",
                          transform: expandedIssue === issue.id ? "rotate(180deg)" : "rotate(0)",
                        }}
                      />
                    </button>
                  )}
                </div>
              ))}

              {/* Drill-down: individual occurrence timestamps */}
              {expandedIssue &&
                issues.find((i) => i.id === expandedIssue)?.occurrences &&
                issues.find((i) => i.id === expandedIssue)!.occurrences!.map((a) => (
                  <div
                    key={`occ-${a.id}`}
                    className="issue-drawer__occurrence"
                    style={{
                      display: "flex",
                      gap: 6,
                      alignItems: "center",
                      fontSize: 11,
                      color: "#75909b",
                      padding: "4px 12px 4px 32px",
                    }}
                  >
                    <span style={{ marginLeft: 24 }}>{a.title}</span>
                    <time>{timeAgo(a.last_occurred_at)}</time>
                    <span style={{ marginLeft: "auto" }}>occurrence #{a.occurrence_count}</span>
                  </div>
                ))}
            </div>
          </aside>
        </>
      )}
    </>
  );
});

export default IssueBadge;
