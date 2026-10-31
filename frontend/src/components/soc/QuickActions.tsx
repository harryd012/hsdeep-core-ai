"use client";

/** QuickActions — SOC quick action buttons (routes that already exist). */
import Link from "next/link";
import { Search, Globe2, FileSearch, ScanSearch, Siren } from "lucide-react";

const ACTIONS = [
  { label: "Search in Threat Intelligence", href: "/reports", icon: Search },
  { label: "Check IP / Domain Reputation", href: "/infrastructure", icon: Globe2 },
  { label: "Analyze Suspicious File", href: "/ai-copilot", icon: FileSearch },
  { label: "Run IOC Search", href: "/noc-event-log", icon: ScanSearch },
  { label: "Create Security Incident", href: "/incidents", icon: Siren },
];

export default function QuickActions() {
  return (
    <div className="soc-panel soc-quick">
      <div className="soc-panel__head"><h2>Quick Actions</h2></div>
      <div className="soc-quick__grid">
        {ACTIONS.map((a) => (
          <Link key={a.label} href={a.href} className="soc-quick__btn">
            <a.icon size={15} />
            {a.label}
          </Link>
        ))}
      </div>
    </div>
  );
}
