"use client";

import { useEffect, useState } from "react";
import { Shield } from "lucide-react";
import DataStateView from "@/components/shared/DataStateView";
import {
  createPaExecution,
  fetchPaRunbooks,
  fetchPaWorkspace,
  matchPaRunbooks,
  PaRunbook,
  PaRunbookMatch,
  PaWorkspace,
} from "@/lib/paloalto/client";

/**
 * Firewall Diagnostic � /firewall-diagnostic landing page.
 *
 * Shows the Palo Alto diagnostic workspace: device facts (with honest
 * data-quality markers, never fabricated attributes), matched runbooks,
 * and recommended next steps.
 */
export default function FirewallDiagnosticPage() {
  const [ws, setWs] = useState<PaWorkspace | null>(null);
  const [runbooks, setRunbooks] = useState<PaRunbook[]>([]);
  const [matches, setMatches] = useState<PaRunbookMatch[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [symptoms, setSymptoms] = useState("traffic denied");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        setLoading(true);
        const [w, rbs] = await Promise.all([fetchPaWorkspace(), fetchPaRunbooks()]);
        if (!cancelled) {
          setWs(w);
          setRunbooks(rbs);
          setError(null);
        }
      } catch (e: unknown) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load diagnostic workspace");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const onMatch = async () => {
    try {
      const ms = await matchPaRunbooks(symptoms.split(",").map((s) => s.trim()).filter(Boolean));
      setMatches(ms);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Runbook match failed");
    }
  };

  const onStart = async () => {
    try {
      await createPaExecution({});
      const w = await fetchPaWorkspace();
      setWs(w);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to start execution");
    }
  };

  return (
    <div className="hs-dashboard">
      <header className="hs-noc__masthead">
        <div className="hs-noc__masthead-brand">
          <div className="hs-noc__masthead-logo">
            <Shield size={40} style={{ color: "var(--cyan)" }} />
          </div>
          <div className="hs-noc__masthead-id">
            <h1 style={{ margin: 0, fontSize: "1.125rem", fontWeight: 700, letterSpacing: "0.06em", color: "#fff" }}>
              Firewall Diagnostic
            </h1>
            <p style={{ margin: "2px 0 0", fontSize: "0.625rem", letterSpacing: "0.22em", color: "var(--muted)", textTransform: "uppercase" }}>
              Palo Alto PAN-OS Operations
            </p>
          </div>
        </div>
      </header>

      <DataStateView
        loading={loading}
        error={error}
        data={ws}
      >
        <section aria-label="Device facts">
          <h2>Device</h2>
          <dl>
            <dt>Vendor</dt><dd>{ws?.vendor ?? "unknown"}</dd>
            <dt>Health</dt><dd>{ws?.health ?? "unknown"}</dd>
            <dt>Data quality</dt>
            <dd>{Object.entries(ws?.data_quality ?? {}).map(([k, v]) => `${k}: ${v}`).join("; ") || "pending"}</dd>
          </dl>
          <button type="button" onClick={onStart}>Start diagnostic execution</button>
        </section>

        <section aria-label="Runbook match">
          <h2>Runbooks ({runbooks.length})</h2>
          <input
            aria-label="symptoms"
            value={symptoms}
            onChange={(e) => setSymptoms(e.target.value)}
            placeholder="symptoms, comma-separated"
          />
          <button type="button" onClick={onMatch}>Match runbooks</button>
          <ul>
            {(matches.length ? matches.map((m) => m.runbook) : runbooks.slice(0, 5)).map((r) => (
              <li key={r.id}>
                <strong>{r.title}</strong> <span>v{r.version}</span>
              </li>
            ))}
          </ul>
        </section>
      </DataStateView>
    </div>
  );
}
