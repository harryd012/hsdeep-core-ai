"use client";

/**
 * Shared full-bleed scene for the sign-in and sign-up pages.
 *
 * The "Digital Sphere" backdrop (globe + particle wave, public/branding) with
 * the platform's capabilities orbiting the globe, the brand and operating
 * pillars on the left, and the page's own card (children) on the right. Every
 * capability named here is a shipped module. Styles: app/styles/login.css.
 */

import { useSyncExternalStore, type ReactNode } from "react";
import {
  Activity,
  BarChart3,
  BrainCircuit,
  Crosshair,
  Network,
  Rocket,
  Settings,
  ShieldCheck,
  Siren,
  Workflow,
} from "lucide-react";
import "@/app/styles/login.css";
import AuthBackdrop from "@/components/auth/AuthBackdrop";

/**
 * False on the server and during hydration, true once React runs in the
 * browser. Auth forms keep their submit button disabled until then, so a click
 * before JavaScript loads can never fall back to a native submit.
 */
export function useHydrated(): boolean {
  return useSyncExternalStore(
    () => () => {},
    () => true,
    () => false,
  );
}

/** Infinity brand mark. */
function BrandMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 48 24" fill="none" xmlns="http://www.w3.org/2000/svg" className={className} aria-hidden="true">
      <defs>
        <linearGradient id="hsdeep-infinity" x1="0" y1="0" x2="48" y2="24" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="#22d3ee" />
          <stop offset="55%" stopColor="#0ea5e9" />
          <stop offset="100%" stopColor="#6366f1" />
        </linearGradient>
      </defs>
      <path
        d="M24 12C20 4 8 4 8 12C8 20 20 20 24 12C28 4 40 4 40 12C40 20 28 20 24 12Z"
        stroke="url(#hsdeep-infinity)"
        strokeWidth="3.2"
        strokeLinecap="round"
      />
    </svg>
  );
}

function Brand() {
  return (
    <div className="lx-brand">
      <BrandMark className="lx-brand__mark" />
      <span className="lx-brand__rule" aria-hidden="true" />
      <div>
        <div className="lx-brand__name">
          HSDEEP CORE <span>AI</span>
        </div>
        <div className="lx-brand__tag">AI-Native IT Operations</div>
      </div>
    </div>
  );
}

/** The operating loop, left to right. */
const PILLARS = [
  { icon: Crosshair, title: "Observe", text: "Unified visibility" },
  { icon: BarChart3, title: "Analyze", text: "AI-driven insight" },
  { icon: Settings, title: "Automate", text: "Runbooks & SOPs" },
  { icon: Siren, title: "Respond", text: "Faster resolution" },
  { icon: Rocket, title: "Scale", text: "Multi-tenant ready" },
];

/** Capabilities orbiting the globe (positions are % of the backdrop stage). */
const ORBIT = [
  { icon: Activity, label: ["Unified", "observability"], side: "left", x: 37.5, y: 22 },
  { icon: BrainCircuit, label: ["AI root-cause", "analysis"], side: "left", x: 33.5, y: 46 },
  { icon: Siren, label: ["Real-time", "alerting"], side: "left", x: 37, y: 70 },
  { icon: Network, label: ["Topology &", "dependency maps"], side: "right", x: 59.5, y: 22 },
  { icon: Workflow, label: ["Runbook", "automation"], side: "right", x: 62.5, y: 46 },
  { icon: ShieldCheck, label: ["Tenant-isolated", "security"], side: "right", x: 59.5, y: 70 },
] as const;

export default function AuthScene({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="lx">
      <h1 className="lx-sr-only">{title}</h1>

      {/* Backdrop stage: sized like object-fit: cover, so the orbit nodes stay on the globe at any window size. */}
      <div className="lx-stage" aria-hidden="true">
        <AuthBackdrop />
        <ul className="lx-orbit">
          {ORBIT.map(({ icon: Icon, label, side, x, y }) => (
            <li key={label.join(" ")} className={`lx-node lx-node--${side}`} style={{ left: `${x}%`, top: `${y}%` }}>
              <span className="lx-node__ring">
                <Icon size={22} />
              </span>
              <span className="lx-node__label">
                {label[0]}
                <br />
                {label[1]}
              </span>
            </li>
          ))}
        </ul>
      </div>
      <div className="lx-scrim" aria-hidden="true" />

      <div className="lx-layout">
        <header className="lx-top">
          <Brand />
          <p className="lx-top__claims">
            Secure <i>|</i> Scalable <i>|</i> Intelligent
          </p>
        </header>

        <div className="lx-hero">
          <h2 className="lx-hero__title">
            Intelligence.
            <br />
            Automation.
            <br />
            <span>Resilient Operations.</span>
          </h2>
          <span className="lx-hero__rule" aria-hidden="true" />
          <ul className="lx-pillars">
            {PILLARS.map(({ icon: Icon, title: name, text }) => (
              <li key={name}>
                <Icon size={24} aria-hidden="true" />
                <b>{name}</b>
                <span>{text}</span>
              </li>
            ))}
          </ul>
        </div>

        <main className="lx-main">{children}</main>

        <footer className="lx-foot">
          <span className="lx-foot__built">Built for what&apos;s next</span>
          <span className="lx-foot__tagline">Enterprise IT. Intelligently operated.</span>
          <span className="lx-foot__copy">© {new Date().getFullYear()} HSDEEP CORE AI</span>
        </footer>
      </div>
    </div>
  );
}
