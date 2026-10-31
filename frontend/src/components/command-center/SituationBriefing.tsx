"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { AlertOctagon, AlertTriangle, ArrowRight, Info, Sparkles } from "lucide-react";
import { buildBriefing, type BriefingInput, type FindingSeverity } from "@/lib/commandCenter/briefing";

/**
 * Situation Briefing — the dashboard's "what matters right now" panel.
 * Rule-based and explainable (see lib/commandCenter/briefing.ts): every
 * finding shows its evidence, its impact, a link to act, and how many
 * readiness points it costs.
 */

const ICON: Record<FindingSeverity, typeof Info> = { critical: AlertOctagon, warning: AlertTriangle, info: Info };
const VISIBLE = 3;

export default function SituationBriefing({ input, loading }: { input: BriefingInput; loading: boolean }) {
  const briefing = useMemo(() => buildBriefing(input), [input]);
  const [expanded, setExpanded] = useState(false);
  const shown = expanded ? briefing.findings : briefing.findings.slice(0, VISIBLE);
  const score = briefing.score;
  const tone = score === null ? "neutral" : score >= 85 ? "good" : score >= 65 ? "warn" : "bad";
  const R = 34;
  const C = 2 * Math.PI * R;

  return (
    <section className="ecc-card sb" aria-label="Situation briefing">
      <div className="sb__score">
        <svg width={92} height={92} viewBox="0 0 92 92" role="img" aria-label={score === null ? "Readiness not measured" : `Operational readiness ${score} out of 100, ${briefing.grade}`}>
          <circle cx={46} cy={46} r={R} fill="none" stroke="var(--ecc-track)" strokeWidth={8} />
          {score !== null && score > 0 && (
            <circle
              cx={46}
              cy={46}
              r={R}
              fill="none"
              className={`sb__ring sb__ring--${tone}`}
              strokeWidth={8}
              strokeLinecap="round"
              strokeDasharray={`${(score / 100) * C} ${C}`}
              transform="rotate(-90 46 46)"
            />
          )}
          <text x={46} y={50} textAnchor="middle" fontSize={22} fontWeight={700} fill="var(--ecc-text)">
            {loading && score === null ? "…" : score ?? "—"}
          </text>
          <text x={46} y={64} textAnchor="middle" fontSize={9} fill="var(--ecc-text-muted)">
            of 100
          </text>
        </svg>
        <div>
          <div className="sb__eyebrow"><Sparkles size={12} aria-hidden="true" /> Situation briefing</div>
          <div className={`sb__grade sb__grade--${tone}`}>{loading && score === null ? "Assessing…" : briefing.grade}</div>
          <p className="sb__note">
            Operational readiness. {briefing.findings.length
              ? `${briefing.findings.length} finding${briefing.findings.length > 1 ? "s" : ""} from ${briefing.rulesEvaluated} checks — each one shows the points it costs.`
              : briefing.rulesEvaluated
                ? `All ${briefing.rulesEvaluated} checks passed.`
                : "Waiting for data."}
          </p>
        </div>
      </div>

      <ol className="sb__findings">
        {shown.map((f) => {
          const Icon = ICON[f.severity];
          return (
            <li key={f.id} className={`sb__finding sb__finding--${f.severity}`}>
              <span className="sb__icon" aria-hidden="true"><Icon size={15} /></span>
              <div className="sb__body">
                <div className="sb__title">
                  <span>{f.title}</span>
                  <span className="sb__cost" title="Readiness points this finding costs">−{f.weight}</span>
                </div>
                <div className="sb__evidence">
                  {f.evidence.map((e) => <span key={e}>{e}</span>)}
                </div>
                <p className="sb__impact">{f.impact}</p>
              </div>
              <Link className="sb__action" href={f.action.href}>
                {f.action.label} <ArrowRight size={13} aria-hidden="true" />
              </Link>
            </li>
          );
        })}
        {!briefing.findings.length && !loading && (
          <li className="sb__clear">Nothing needs attention right now.</li>
        )}
      </ol>
      {briefing.findings.length > VISIBLE && (
        <button type="button" className="ecc-link sb__more" onClick={() => setExpanded((x) => !x)} style={{ background: "none", border: 0, cursor: "pointer" }}>
          {expanded ? "Show fewer" : `Show all ${briefing.findings.length} findings`}
        </button>
      )}
    </section>
  );
}
