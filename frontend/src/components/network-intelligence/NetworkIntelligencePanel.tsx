"use client";

import { useState } from "react";
import { AlertTriangle, ChevronDown, ChevronRight, Route, ShieldCheck } from "lucide-react";
import { useNetworkIntelligence } from "@/hooks/useNetworkIntelligence";
import type { NetworkEvaluateResult, ScoredWanPath } from "@/lib/api";
import {
  DECISION_FLOW,
  deriveVerdict,
  pathTone,
  readable,
  saseTone,
  type Tone,
} from "./status";

/**
 * NetworkIntelligencePanel — the /infrastructure operator surface for the
 * existing SD-WAN + SASE evaluation API.
 *
 * Presentation only: it reuses the existing hud-panel visual language, the
 * existing apiFetch client, and the existing evaluation response. It NEVER
 * implies that AI changed the network — the recommendation is advisory and
 * execution stays on the Action Plane.
 */

function toneClass(prefix: string, tone: Tone): string {
  return `${prefix} ${prefix}--${tone}`;
}

function formatMetric(value: number | undefined, unit: string): string {
  if (value === undefined || value === null) return "—";
  return `${value} ${unit}`;
}

function SdwanCard({ result }: { result: NetworkEvaluateResult }) {
  const { sdwan } = result;
  const selected =
    sdwan.paths.find((p) => p.name === sdwan.selected_path) ?? null;

  return (
    <div className="ni-card">
      <div className="ni-card__head">
        <span className="ni-card__title">
          <Route size={14} /> SD-WAN PATH INTELLIGENCE
        </span>
        <span className={toneClass("ni-pill", selected ? pathTone(selected.health) : "warn")}>
          {readable(sdwan.decision)}
        </span>
      </div>

      <div className="ni-kv">
        <div>
          <dt>Selected Path</dt>
          <dd>{sdwan.selected_path ?? "—"}</dd>
        </div>
        <div>
          <dt>Status</dt>
          <dd className={toneClass("ni-text", selected ? pathTone(selected.health) : "neutral")}>
            {selected ? readable(selected.health) : "—"}
          </dd>
        </div>
        <div>
          <dt>Score</dt>
          <dd>{sdwan.score}</dd>
        </div>
        <div>
          <dt>Latency</dt>
          <dd>{formatMetric(selected?.latency_ms, "ms")}</dd>
        </div>
        <div>
          <dt>Jitter</dt>
          <dd>{formatMetric(selected?.jitter_ms, "ms")}</dd>
        </div>
        <div>
          <dt>Packet Loss</dt>
          <dd>{selected ? `${selected.packet_loss_pct} %` : "—"}</dd>
        </div>
        <div>
          <dt>Bandwidth</dt>
          <dd>{formatMetric(selected?.bandwidth_mbps, "Mbps")}</dd>
        </div>
        <div>
          <dt>Availability</dt>
          <dd>
            {selected
              ? selected.available
                ? "AVAILABLE"
                : "UNAVAILABLE"
              : "—"}
          </dd>
        </div>
      </div>

      {sdwan.paths.length > 0 && (
        <>
          <div className="ni-subhead">PATH COMPARISON</div>
          <table className="ni-compare">
            <thead>
              <tr>
                <th>Path</th>
                <th>Health</th>
                <th>Score</th>
                <th>Latency</th>
                <th>Loss</th>
                <th>Role</th>
              </tr>
            </thead>
            <tbody>
              {sdwan.paths.map((p: ScoredWanPath) => (
                <tr key={p.name}>
                  <td>{p.name}</td>
                  <td className={toneClass("ni-text", pathTone(p.health))}>
                    {readable(p.health)}
                  </td>
                  <td>{p.score}</td>
                  <td>{formatMetric(p.latency_ms, "ms")}</td>
                  <td>{p.packet_loss_pct} %</td>
                  <td>
                    {p.name === sdwan.selected_path
                      ? "SELECTED"
                      : p.available
                        ? "STANDBY"
                        : "FAILOVER"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <p className="ni-reason">{sdwan.reason}</p>
    </div>
  );
}

function SaseCard({ result }: { result: NetworkEvaluateResult }) {
  const { sase } = result;
  const stages = sase.pipeline.filter((s) => s.stage !== "Final Decision");

  return (
    <div className="ni-card">
      <div className="ni-card__head">
        <span className="ni-card__title">
          <ShieldCheck size={14} /> SASE SECURITY PIPELINE
        </span>
        <span className={toneClass("ni-pill", saseTone(sase.decision))}>
          {readable(sase.decision)}
        </span>
      </div>

      <ul className="ni-pipeline">
        {stages.map((stage) => (
          <li
            key={stage.stage}
            className={toneClass("ni-pipeline__row", saseTone(stage.decision))}
          >
            <span className="ni-pipeline__stage">{stage.stage}</span>
            <span className="ni-pipeline__decision">{readable(stage.decision)}</span>
            {stage.reason && <span className="ni-pipeline__reason">{stage.reason}</span>}
          </li>
        ))}
      </ul>

      <div className={toneClass("ni-final", saseTone(sase.decision))}>
        <span>FINAL DECISION</span>
        <strong>{readable(sase.decision)}</strong>
      </div>

      {sase.decision !== "ALLOW" && (
        <div className="ni-why">
          <span className="ni-subhead">WHY BLOCKED?</span>
          <p>{sase.reason}</p>
        </div>
      )}
    </div>
  );
}

function RecommendationCard({
  result,
  verdict,
}: {
  result: NetworkEvaluateResult;
  verdict: NonNullable<ReturnType<typeof deriveVerdict>>;
}) {
  const rec = result.recommendation;
  const wanGuidance = verdict.selectedPath
    ? `${verdict.selectedPath.name} remains the best available WAN path.`
    : result.sdwan.reason;

  return (
    <div className="ni-card">
      <div className="ni-card__head">
        <span className="ni-card__title">AI RECOMMENDATION</span>
        <span className={toneClass("ni-pill", verdict.recommendationTone)}>
          {verdict.recommendationLabel}
        </span>
      </div>

      {rec ? (
        <>
          <div className="ni-rec__title">{rec.title}</div>
          <div className="ni-kv ni-kv--tight">
            <div>
              <dt>Decision</dt>
              <dd>{readable(rec.status)}</dd>
            </div>
            <div>
              <dt>Execution</dt>
              <dd className="ni-text ni-text--neutral">NO AUTOMATIC ACTION</dd>
            </div>
            <div>
              <dt>Action Plane</dt>
              <dd className="ni-text ni-text--warn">Awaiting human decision</dd>
            </div>
          </div>
          <div className="ni-why">
            <span className="ni-subhead">REASON</span>
            <p>{rec.reason}</p>
          </div>
          <div className="ni-why">
            <span className="ni-subhead">WAN GUIDANCE</span>
            <p>{wanGuidance}</p>
          </div>
        </>
      ) : (
        <p className="ni-reason">
          No action needed — the path is healthy and the flow is allowed by policy.
        </p>
      )}

      <p className="ni-safety">
        AI recommends · Human decides · Action Plane executes
      </p>
    </div>
  );
}

function FlowContextCard({ result }: { result: NetworkEvaluateResult }) {
  const flow = result.flow as Record<string, unknown>;
  const compliance = flow.device_compliance;
  const category = typeof flow.url_category === "string" ? flow.url_category : "";
  const malicious = ["malware", "phishing", "c2", "command-and-control", "botnet", "ransomware"].includes(
    category.toLowerCase(),
  );

  return (
    <div className="ni-card ni-card--wide">
      <div className="ni-card__head">
        <span className="ni-card__title">FLOW CONTEXT</span>
        <span className={toneClass("ni-pill", saseTone(result.sase.decision))}>
          {readable(result.sase.decision)}
        </span>
      </div>
      <div className="ni-kv">
        <div>
          <dt>Flow</dt>
          <dd>{result.flow_id}</dd>
        </div>
        <div>
          <dt>Identity</dt>
          <dd>{String(flow.user_identity ?? "—")}</dd>
        </div>
        <div>
          <dt>Device</dt>
          <dd>{String(flow.device_identity ?? "—")}</dd>
        </div>
        <div>
          <dt>Device Posture</dt>
          <dd className={toneClass("ni-text", compliance === false ? "bad" : "ok")}>
            {compliance === false ? "NON-COMPLIANT" : "COMPLIANT"}
          </dd>
        </div>
        <div>
          <dt>Destination</dt>
          <dd className={toneClass("ni-text", malicious ? "bad" : "neutral")}>
            {String(flow.destination ?? "—")}
          </dd>
        </div>
        <div>
          <dt>URL Category</dt>
          <dd>{readable(category)}</dd>
        </div>
        <div>
          <dt>Application</dt>
          <dd>{readable(String(flow.application ?? ""))}</dd>
        </div>
        <div>
          <dt>Policy Result</dt>
          <dd className={toneClass("ni-text", saseTone(result.sase.decision))}>
            {readable(result.sase.decision)}
          </dd>
        </div>
      </div>
    </div>
  );
}

export default function NetworkIntelligencePanel() {
  const {
    scenarios,
    scenario,
    setScenario,
    result,
    evaluating,
    error,
    evaluate,
    clearError,
  } = useNetworkIntelligence();

  const verdict = deriveVerdict(result);

  return (
    <section className="hud-panel ni-panel" aria-labelledby="ni-title">
      <div className="ni-header">
        <div>
          <h2 id="ni-title">NETWORK INTELLIGENCE</h2>
          <p className="ni-header__sub">SD-WAN + SASE</p>
        </div>
        <div className="ni-controls">
          <label className="ni-controls__label" htmlFor="ni-scenario">
            Scenario
          </label>
          <select
            id="ni-scenario"
            className="ni-select"
            value={scenario}
            onChange={(e) => setScenario(e.target.value)}
          >
            {scenarios.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="ni-button"
            onClick={() => evaluate(scenario)}
            disabled={evaluating}
          >
            {evaluating ? "Evaluating…" : "Run evaluation"}
          </button>
        </div>
      </div>

      {/* DETECT → … → AUDIT: the existing HSDEEP workflow, visualised. */}
      <ol className="ni-flow" aria-label="Decision flow">
        {DECISION_FLOW.map((step, i) => (
          <li key={step} className="ni-flow__step">
            <span className="ni-flow__label">{step}</span>
            {i < DECISION_FLOW.length - 1 && (
              <span className="ni-flow__arrow" aria-hidden="true">
                →
              </span>
            )}
          </li>
        ))}
      </ol>

      {error && (
        <div className="ni-problem" role="status">
          <AlertTriangle size={14} />
          <div>
            <strong className="ni-problem__label">{error.label}</strong>
            <p className="ni-problem__detail">{error.detail}</p>
          </div>
          <button
            type="button"
            className="ni-toggle"
            disabled={evaluating}
            onClick={() => {
              clearError();
              evaluate(scenario);
            }}
          >
            RETRY
          </button>
        </div>
      )}

      {!verdict && !evaluating && !error && (
        <p className="ni-empty">
          STATUS · READY — no evaluation executed yet. Choose a scenario and run
          an evaluation to see the SD-WAN path decision, the SASE policy
          pipeline, and the human-in-the-loop recommendation.
        </p>
      )}

      {verdict && result && (
        <>
          {/* EXECUTIVE STATUS HEADER — every value from the API response. */}
          <div className="ni-status">
            <div className={toneClass("ni-status__tile", verdict.overallTone)}>
              <span className="ni-status__label">OVERALL STATUS</span>
              <strong>{verdict.overall}</strong>
            </div>
            <div className={toneClass("ni-status__tile", verdict.wanTone)}>
              <span className="ni-status__label">WAN PATH</span>
              <strong>{verdict.wanLabel}</strong>
            </div>
            <div className={toneClass("ni-status__tile", verdict.saseTone)}>
              <span className="ni-status__label">SASE DECISION</span>
              <strong>{verdict.saseLabel}</strong>
            </div>
            <div className={toneClass("ni-status__tile", verdict.recommendationTone)}>
              <span className="ni-status__label">RECOMMENDATION</span>
              <strong>{verdict.recommendationLabel}</strong>
            </div>
          </div>

          {/* NETWORK DECISION — the situation in one block. */}
          <div className="ni-decision">
            <div className="ni-decision__head">
              <span className="ni-subhead">NETWORK DECISION</span>
              <span className="ni-decision__flow">Traffic: {result.flow_id}</span>
            </div>
            <div className="ni-decision__body">
              <div>
                <span className="ni-status__label">WAN</span>
                <p>
                  <b>{result.sdwan.selected_path ?? "—"}</b> selected
                  {verdict.selectedPath
                    ? ` · ${verdict.selectedPath.latency_ms} ms latency · ${verdict.selectedPath.jitter_ms} ms jitter · ${verdict.selectedPath.packet_loss_pct}% packet loss`
                    : ""}
                </p>
              </div>
              <div>
                <span className="ni-status__label">SECURITY</span>
                <p className={toneClass("ni-text", verdict.saseTone)}>
                  {verdict.saseLabel}
                </p>
              </div>
              <div className="ni-decision__reason">
                <span className="ni-status__label">REASON</span>
                <p>{result.sase.reason}</p>
              </div>
            </div>
          </div>

          <div className="ni-grid">
            <SdwanCard result={result} />
            <SaseCard result={result} />
            <RecommendationCard result={result} verdict={verdict} />
            <FlowContextCard result={result} />
            <AuditCard result={result} />
          </div>
        </>
      )}
    </section>
  );
}

function AuditCard({ result }: { result: NetworkEvaluateResult }) {
  const [expanded, setExpanded] = useState(false);
  const rows = result.audit ?? [];

  return (
    <div className="ni-card ni-card--wide">
      <div className="ni-card__head">
        <span className="ni-card__title">AUDIT TRAIL · {rows.length} EVENTS</span>
        {rows.length > 0 && (
          <button
            type="button"
            className="ni-toggle"
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={expanded}
          >
            {expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            {expanded ? "Hide details" : "Show details"}
          </button>
        )}
      </div>

      {rows.length === 0 ? (
        <p className="ni-reason">No audit events were emitted for this flow.</p>
      ) : (
        <ul className="ni-audit">
          {rows.map((row, i) => (
            <li key={`${row.stage}-${i}`} className="ni-audit__row">
              <span className="ni-audit__engine">{row.engine}</span>
              <span className="ni-audit__stage">{row.stage}</span>
              <span className={toneClass("ni-audit__decision", saseTone(row.decision) === "neutral" ? "neutral" : saseTone(row.decision))}>
                {readable(row.decision)}
              </span>
              {expanded && (
                <span className="ni-audit__meta">
                  {new Date(row.timestamp).toLocaleString()} · flow {row.flow_id}
                  {row.device ? ` · device ${row.device}` : ""}
                  {row.reason ? ` · ${row.reason}` : ""}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
