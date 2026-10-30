"use client";

/** AttackVectorsChart — horizontal bar rows (label, bar, numeric value). */
import type { SocAttackVector } from "@/lib/soc/socModels";

export default function AttackVectorsChart({ rows, source }: { rows: SocAttackVector[]; source: "live" | "unavailable" }) {
  if (rows.length === 0) {
    return (
      <div className="soc-panel soc-vectors">
        <div className="soc-panel__head"><h2>Top Attack Vectors</h2></div>
        <div className="soc-empty">No attack-vector data available.</div>
        {source === "unavailable" && <small className="soc-demo-note">demo data — attack-vector API pending</small>}
      </div>
    );
  }
  const max = Math.max(1, ...rows.map((r) => r.count));
  return (
    <div className="soc-panel soc-vectors">
      <div className="soc-panel__head"><h2>Top Attack Vectors</h2></div>
      <div className="soc-vectors__rows">
        {rows.map((r, i) => (
          <div key={r.label} className="soc-vectors__row">
            <small>{r.label}</small>
            <div className="soc-vectors__track">
              <div
                className="soc-vectors__bar"
                style={{ width: `${(r.count / max) * 100}%`, background: `hsl(${190 + i * 14} 85% 55%)` }}
              />
            </div>
            <b>{r.count}</b>
          </div>
        ))}
      </div>
      {source === "unavailable" && <small className="soc-demo-note">demo data — attack-vector API pending</small>}
    </div>
  );
}
