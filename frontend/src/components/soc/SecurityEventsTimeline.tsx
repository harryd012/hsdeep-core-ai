"use client";

/** SecurityEventsTimeline — severity icon, time, badge, title, description. */
import Link from "next/link";
import { ShieldAlert, Bug, Globe, Flame, Laptop } from "lucide-react";
import type { SocEvent } from "@/lib/soc/socModels";
import { sevColor } from "@/lib/soc/socAdapter";

const ICONS = [ShieldAlert, Bug, Globe, Flame, Laptop];

export default function SecurityEventsTimeline({ events, state }: {
  events: SocEvent[];
  state: "loading" | "success" | "empty" | "error";
}) {
  return (
    <div className="soc-panel soc-events">
      <div className="soc-panel__head"><h2>Security Events Timeline</h2></div>
      {state === "loading" && <div className="soc-empty">Loading events…</div>}
      {state === "error" && <div className="soc-empty soc-empty--error">Events unavailable — backend error.</div>}
      {state === "empty" && <div className="soc-empty">No security events recorded.</div>}
      {state === "success" && (
        <ul className="soc-events__list">
          {events.map((e, i) => {
            const Icon = ICONS[i % ICONS.length];
            return (
              <li key={e.id} className="soc-events__item">
                <span className="soc-events__icon" style={{ color: sevColor(e.severity) }}><Icon size={15} /></span>
                <time>{e.time}</time>
                <span className={`soc-badge soc-badge--${e.severity}`}>{e.severity}</span>
                <div className="soc-events__text">
                  <b>{e.title}</b>
                  <small>{e.description}</small>
                </div>
              </li>
            );
          })}
        </ul>
      )}
      <Link href="/alerts" className="soc-panel__link">View All Events →</Link>
    </div>
  );
}
