"use client";

/**
 * Audit log integrity (enterprise security programme S4): verifies the
 * tenant's tamper-evident hash chain on demand and shows the chain head, the
 * checkpoint to keep outside the platform (a ticket, a vault, a signed email).
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Copy, ShieldAlert, ShieldCheck } from "lucide-react";
import { Panel, Pill, ago } from "@/components/ui/PageKit";
import { getAuditIntegrity } from "@/lib/security";

const PROBLEM: Record<string, string> = {
  event_modified: "event changed after it was recorded",
  event_deleted: "event deleted",
  broken_link: "chain link altered",
  missing_link: "chain link missing",
};

export default function AuditIntegrityPanel() {
  const [full, setFull] = useState(false);
  const check = useQuery({ queryKey: ["audit", "integrity", full], queryFn: () => getAuditIntegrity(full), staleTime: 60_000 });
  const [copied, setCopied] = useState(false);
  const r = check.data;
  const tone = !r ? "neutral" : r.status === "broken" ? "bad" : r.status === "intact" ? "good" : "neutral";

  return (
    <Panel
      title="Integrity"
      info="Every audit event is chained to the one before it with a SHA-256 hash, so changing or deleting a recorded event shows up here."
      state={r ? "ready" : check.isError ? "error" : "loading"}
      onRetry={() => check.refetch()}
      skeletonHeight={90}
      aside={
        <span style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
          {r && <Pill tone={tone}>{r.status === "intact" ? "Intact" : r.status === "broken" ? "Tampering detected" : "No events yet"}</Pill>}
          <button type="button" className="pk-button" style={{ height: 26 }} disabled={check.isFetching} onClick={() => check.refetch()}>
            {check.isFetching ? "Checking…" : "Verify now"}
          </button>
          {!full && r?.scope === "recent" && (
            <button type="button" className="pk-button" style={{ height: 26 }} disabled={check.isFetching} onClick={() => setFull(true)}>
              Verify full history
            </button>
          )}
        </span>
      }
    >
      {r && (
        <div className="ai-integrity">
          <p className="sec-help" style={{ display: "flex", gap: 8, alignItems: "flex-start", margin: 0 }}>
            {r.status === "broken" ? <ShieldAlert size={16} color="var(--ecc-bad)" aria-hidden="true" /> : <ShieldCheck size={16} color="var(--ecc-good, #10b981)" aria-hidden="true" />}
            <span>
              {r.links_checked.toLocaleString("en-US")} {r.scope === "recent" ? "most recent " : ""}event{r.links_checked === 1 ? "" : "s"} verified
              {r.scope === "full" ? " (full history)" : ` (from #${r.from_seq.toLocaleString("en-US")})`}
              {r.unchained_events > 0 && <> · {r.unchained_events.toLocaleString("en-US")} recent event{r.unchained_events === 1 ? "" : "s"} waiting to be sealed (within 5 minutes)</>}
              .
            </span>
          </p>
          {r.problems.length > 0 && (
            <ul className="ai-integrity__problems">
              {r.problems.map((p) => (
                <li key={`${p.seq}-${p.problem}`}>
                  <b>#{p.seq}</b> {PROBLEM[p.problem] ?? p.problem} <code>{p.event_id}</code>
                </li>
              ))}
            </ul>
          )}
          {r.head && (
            <div className="ai-integrity__head">
              <span className="sec-help" style={{ margin: 0 }}>Chain head #{r.head.seq} · {ago(r.head.linked_at)}</span>
              <code title={r.head.hash}>{r.head.hash}</code>
              <button type="button" className="pk-button" style={{ height: 26 }}
                onClick={() => { void navigator.clipboard?.writeText(`${r.head!.seq}:${r.head!.hash}`); setCopied(true); }}>
                <Copy size={13} aria-hidden="true" /> {copied ? "Copied" : "Copy checkpoint"}
              </button>
            </div>
          )}
        </div>
      )}
    </Panel>
  );
}
