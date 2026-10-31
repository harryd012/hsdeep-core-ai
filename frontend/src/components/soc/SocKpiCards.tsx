"use client";

/** SocKpiCards — six severity-accented KPI cards in one desktop row. */
import { ArrowDownRight, ArrowUpRight, ShieldAlert, Flame, ShieldCheck, Laptop, Fingerprint, Bug } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { SocKpi } from "@/lib/soc/socModels";

const ICONS: Record<string, LucideIcon> = {
  critical: ShieldAlert,
  high: Flame,
  good: ShieldCheck,
  warn: Laptop,
  info: Fingerprint,
  violet: Bug,
};

export default function SocKpiCards({ kpis }: { kpis: SocKpi[] }) {
  return (
    <div className="soc-kpis">
      {kpis.map((k) => {
        const Icon = ICONS[k.tone] ?? ShieldAlert;
        return (
          <div key={k.key} className={`soc-kpi soc-kpi--${k.tone}`}>
            <div className="soc-kpi__head">
              <span className="soc-kpi__icon"><Icon size={16} /></span>
              <small>{k.label}</small>
              {k.change && (
                <span className={`soc-kpi__change ${k.change.startsWith("+") ? "up" : "down"}`}>
                  {k.change.startsWith("+") ? <ArrowUpRight size={11} /> : <ArrowDownRight size={11} />}
                  {k.change}
                </span>
              )}
            </div>
            <div className="soc-kpi__value">{k.value === null ? "—" : k.value.toLocaleString()}</div>
            <div className="soc-kpi__foot">
              <small>
                {k.source === "live" ? k.description
                  : k.source === "unavailable" ? "Unavailable · no data from backend"
                    : "Unavailable · source not configured"}
              </small>
            </div>
          </div>
        );
      })}
    </div>
  );
}
