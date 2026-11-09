"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpenCheck, Check, X } from "lucide-react";

import { useAuth } from "@/lib/auth-context";
import {
  ApiError,
  decideIncidentSopRecommendation,
  getIncidentSopRecommendations,
  type IncidentSopRecommendation,
  type SopDecision,
} from "@/lib/api";

/**
 * Recommended SOPs for one incident (GET /api/incidents/{id}/sop-recommendations).
 *
 * The backend scores approved SOPs against the incident; this panel shows the
 * best three, why each matched, and lets an operator with incidents.write
 * accept or dismiss one. Accepting records the choice only: nothing runs from
 * here. A dismissed SOP drops out and the next best takes its place.
 */

const MUTED = { color: "var(--muted)" } as const;
const CARD: React.CSSProperties = {
  border: "1px solid var(--line)",
  borderRadius: 8,
  padding: 12,
  display: "flex",
  flexDirection: "column",
  gap: 8,
  minWidth: 0,
};
const BUTTON: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 4,
  padding: "4px 10px",
  borderRadius: 6,
  border: "1px solid var(--line-strong, var(--line))",
  background: "transparent",
  color: "var(--fg, #e8eef5)",
  fontSize: 12,
  cursor: "pointer",
};

const CRITERIA_LABEL: Record<string, string> = {
  category: "Category",
  vendor: "Vendor",
  platform: "Platform",
  device_type: "Device type",
  site_id: "Site",
  business_service_id: "Business service",
  region: "Region",
  min_priority: "Priority",
};

function confidenceTone(score: number): string {
  return score >= 70 ? "var(--green)" : score >= 45 ? "var(--amber)" : "var(--muted)";
}

function SopCard({
  rec,
  canDecide,
  busy,
  onDecide,
}: {
  rec: IncidentSopRecommendation;
  canDecide: boolean;
  busy: boolean;
  onDecide: (decision: SopDecision, reason?: string) => void;
}) {
  const [dismissing, setDismissing] = useState(false);
  const [reason, setReason] = useState("");
  const matched = Object.entries(rec.matched_criteria).filter(([, v]) => v === true).map(([k]) => CRITERIA_LABEL[k] ?? k);
  const accepted = rec.decision?.decision === "accepted";

  return (
    <article style={{ ...CARD, borderColor: accepted ? "var(--green)" : "var(--line)" }} aria-label={`${rec.sop_number} ${rec.name}`}>
      <header style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "baseline" }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ ...MUTED, fontSize: 11 }}>{rec.sop_number} · {rec.category}</div>
          <div style={{ fontSize: 13, fontWeight: 600 }}>{rec.name}</div>
        </div>
        <span style={{ color: confidenceTone(rec.confidence_score), fontSize: 18, fontWeight: 700, whiteSpace: "nowrap" }} title="Match confidence">
          {Math.round(rec.confidence_score)}%
        </span>
      </header>

      <dl style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "3px 10px", margin: 0, fontSize: 12 }}>
        <dt style={MUTED}>Matched on</dt>
        <dd style={{ margin: 0 }}>{matched.length ? matched.join(", ") : <span style={MUTED}>Category only</span>}</dd>
        <dt style={MUTED}>Estimated time</dt>
        <dd style={{ margin: 0 }}>
          {rec.estimated_resolution_minutes !== null ? `${rec.estimated_resolution_minutes} min` : <span style={MUTED}>N/A</span>}
        </dd>
        <dt style={MUTED}>Skills</dt>
        <dd style={{ margin: 0 }}>{rec.required_skills.length ? rec.required_skills.join(", ") : <span style={MUTED}>None listed</span>}</dd>
        <dt style={MUTED}>Approval</dt>
        <dd style={{ margin: 0 }}>{rec.requires_approval ? "Required before running" : "Not required"}</dd>
        <dt style={MUTED}>Rollback</dt>
        <dd style={{ margin: 0 }}>{rec.rollback_procedure ?? <span style={MUTED}>None documented</span>}</dd>
      </dl>

      <footer style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center", marginTop: "auto" }}>
        {accepted ? (
          <span style={{ color: "var(--green)", fontSize: 12, display: "inline-flex", alignItems: "center", gap: 4 }}>
            <Check size={13} aria-hidden /> Accepted
          </span>
        ) : null}
        {canDecide && !dismissing ? (
          <>
            {!accepted && (
              <button type="button" style={BUTTON} disabled={busy} onClick={() => onDecide("accepted")}>
                <Check size={13} aria-hidden /> Accept
              </button>
            )}
            <button type="button" style={BUTTON} disabled={busy} onClick={() => setDismissing(true)}>
              <X size={13} aria-hidden /> Dismiss
            </button>
          </>
        ) : null}
        {dismissing ? (
          <form
            style={{ display: "flex", gap: 6, width: "100%" }}
            onSubmit={(e) => {
              e.preventDefault();
              onDecide("dismissed", reason.trim() || undefined);
            }}
          >
            <input
              aria-label="Reason for dismissing (optional)"
              placeholder="Reason (optional)"
              value={reason}
              maxLength={1000}
              onChange={(e) => setReason(e.target.value)}
              style={{ flex: 1, minWidth: 0, fontSize: 12, padding: "4px 8px", borderRadius: 6, border: "1px solid var(--line)", background: "transparent", color: "inherit" }}
            />
            <button type="submit" style={BUTTON} disabled={busy}>Dismiss</button>
            <button type="button" style={BUTTON} onClick={() => setDismissing(false)}>Cancel</button>
          </form>
        ) : null}
        <Link href="/sop-engine" style={{ fontSize: 12, color: "var(--cyan)", marginLeft: "auto", textDecoration: "none" }}>
          Open SOP library
        </Link>
      </footer>
    </article>
  );
}

export default function SopRecommendations({ incidentId }: { incidentId: string }) {
  const { user } = useAuth();
  const canDecide = !!user && (user.is_superuser || user.permissions.includes("incidents.write"));
  const queryClient = useQueryClient();
  const key = ["incident", incidentId, "sop-recommendations"];
  const query = useQuery<IncidentSopRecommendation[]>({
    queryKey: key,
    queryFn: () => getIncidentSopRecommendations(incidentId),
    enabled: !!user,
    staleTime: 60_000,
    retry: false,
  });
  const [notice, setNotice] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: (v: { sopId: string; decision: SopDecision; reason?: string }) =>
      decideIncidentSopRecommendation(incidentId, v.sopId, v.decision, v.reason),
    onSuccess: (_, v) => {
      setNotice(v.decision === "accepted" ? "SOP accepted for this incident." : "SOP dismissed for this incident.");
      void queryClient.invalidateQueries({ queryKey: key });
    },
    onError: (e) => setNotice(e instanceof Error ? `Could not save: ${e.message}` : "Could not save the decision."),
  });

  const recs = query.data ?? null;
  const error = query.isError
    ? query.error instanceof ApiError && query.error.status === 403
      ? "You need the sops.read permission to see recommended SOPs."
      : query.error instanceof Error ? query.error.message : "Recommendations could not be loaded."
    : null;
  const busy = mutation.isPending ? mutation.variables?.sopId ?? null : null;

  function decide(sopId: string, decision: SopDecision, reason?: string) {
    setNotice(null);
    mutation.mutate({ sopId, decision, reason });
  }

  return (
    <section className="hud-panel" style={{ marginBottom: 16, padding: 16 }} aria-label="Recommended SOPs">
      <header style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 4 }}>
        <BookOpenCheck size={13} style={{ color: "var(--cyan)" }} aria-hidden />
        <h2 style={{ fontSize: 13, letterSpacing: 1, margin: 0 }}>RECOMMENDED SOPs</h2>
      </header>
      <p style={{ ...MUTED, fontSize: 11, margin: "0 0 10px", lineHeight: 1.5 }}>
        Approved SOPs matched to this incident&apos;s alerts, device and site, best first. Accepting records the
        procedure you are following; nothing is run from this page.
      </p>

      {error ? <p style={{ color: "var(--red)", fontSize: 13 }}>{error}</p> : null}
      {!error && recs === null ? <p style={{ ...MUTED, fontSize: 13 }}>Matching SOPs…</p> : null}
      {recs !== null && recs.length === 0 ? (
        <p style={{ ...MUTED, fontSize: 13 }}>
          No approved SOP matches this incident closely enough. Add or approve one in the{" "}
          <Link href="/sop-engine" style={{ color: "var(--cyan)" }}>SOP library</Link>.
        </p>
      ) : null}
      {recs && recs.length > 0 ? (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 10 }}>
          {recs.map((rec) => (
            <SopCard
              key={rec.sop_id}
              rec={rec}
              canDecide={canDecide}
              busy={busy === rec.sop_id}
              onDecide={(decision, reason) => decide(rec.sop_id, decision, reason)}
            />
          ))}
        </div>
      ) : null}
      {notice ? <p role="status" style={{ ...MUTED, fontSize: 12, margin: "8px 0 0" }}>{notice}</p> : null}
    </section>
  );
}
