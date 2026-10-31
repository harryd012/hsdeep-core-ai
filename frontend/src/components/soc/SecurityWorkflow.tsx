"use client";

/** SecurityWorkflow — six-stage SOC pipeline with directional connectors. */
import { Bell, Filter, Search, Shield, Zap, RotateCcw, ChevronRight } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { SocWorkflowStage } from "@/lib/soc/socModels";

const ICONS: Record<string, LucideIcon> = {
  alert: Bell, validate: Filter, investigate: Search,
  contain: Shield, respond: Zap, recover: RotateCcw,
};

export default function SecurityWorkflow({ stages }: { stages: SocWorkflowStage[] }) {
  if (stages.length === 0) {
    return (
      <div className="soc-panel soc-workflow">
        <div className="soc-panel__head"><h2>Security Operations Workflow</h2></div>
        <div className="soc-empty">No workflow stages available.</div>
      </div>
    );
  }
  return (
    <div className="soc-panel soc-workflow">
      <div className="soc-panel__head"><h2>Security Operations Workflow</h2></div>
      <ol className="soc-workflow__steps">
        {stages.map((s, i) => {
          const Icon = ICONS[s.key] ?? Bell;
          return (
            <li key={s.key} className="soc-workflow__step">
              <span className="soc-workflow__icon"><Icon size={16} /></span>
              <b>{s.title}</b>
              <small>{s.description}</small>
              {i < stages.length - 1 && <ChevronRight size={14} className="soc-workflow__arrow" />}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
