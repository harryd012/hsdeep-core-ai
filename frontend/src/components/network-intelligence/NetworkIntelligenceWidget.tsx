"use client";

import Link from "next/link";
import { ArrowUpRight, Route, ShieldCheck } from "lucide-react";
import { useStoredNetworkEvaluation } from "@/hooks/useNetworkIntelligence";
import { deriveVerdict } from "./status";


/**
 * NetworkIntelligenceWidget — the compact Network Intelligence summary for the
 * MAIN /dashboard.
 *
 * It reads the last REAL evaluation persisted by the /infrastructure panel and
 * never triggers an evaluation itself (no aggressive polling, advisory only).
 * A malformed persisted payload is treated as "no evaluation" rather than being
 * rendered, so the widget can never display fabricated network state.
 */
export default function NetworkIntelligenceWidget() {
  const result = useStoredNetworkEvaluation();
  const verdict = deriveVerdict(result);

  return (
    <section className="ni-widget" aria-labelledby="ni-widget-title">
      <header className="ni-widget__head">
        <h2 id="ni-widget-title" className="ni-widget__title">
          NETWORK INTELLIGENCE
        </h2>
        <span className="ni-widget__sub">SD-WAN + SASE</span>
      </header>

      {verdict && result ? (
        <>
          <div className="ni-widget__grid">
            <div className={`ni-widget__tile ni-widget__tile--${verdict.saseTone}`}>
              <span className="ni-widget__label">SECURITY</span>
              <strong>{verdict.saseLabel}</strong>
            </div>
            <div className={`ni-widget__tile ni-widget__tile--${verdict.wanTone}`}>
              <span className="ni-widget__label">WAN PATH</span>
              <strong>{verdict.wanLabel}</strong>
              {verdict.selectedPath && (
                <span className="ni-widget__meta">
                  <Route size={11} /> score {verdict.selectedPath.score}
                </span>
              )}
            </div>
            <div className={`ni-widget__tile ni-widget__tile--${verdict.recommendationTone}`}>
              <span className="ni-widget__label">RECOMMENDATION</span>
              <strong>{verdict.recommendationLabel}</strong>
              <span className="ni-widget__meta">
                <ShieldCheck size={11} />
                {verdict.needsHumanDecision ? "Action Plane: awaiting human" : "no action required"}
              </span>
            </div>
          </div>

          <div className="ni-widget__foot">
            {/* Provenance: this is the last REAL evaluation, not a live feed.
                The widget never polls, so it must never imply live state. */}
            <span className="ni-widget__provenance">LAST SUCCESSFUL EVALUATION</span>
            <span>Flow: {result.flow_id}</span>
            <span>Audit: {result.audit.length} events</span>
          </div>
        </>
      ) : (
        <div className="ni-widget__ready">
          <span className="ni-widget__label">STATUS</span>
          <strong>READY</strong>
          <p>No evaluation executed yet.</p>
        </div>
      )}

      <Link className="ni-widget__link" href="/infrastructure">
        VIEW NETWORK INTELLIGENCE <ArrowUpRight size={13} />
      </Link>
    </section>
  );
}
