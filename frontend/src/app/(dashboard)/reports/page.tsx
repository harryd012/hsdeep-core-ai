"use client";

import Link from "next/link";
import { Activity, BellRing, BookOpenCheck, Server } from "lucide-react";
import { PageShell } from "@/components/ui/PageKit";

/** Every report has its own page; this index only routes to them. */
const REPORTS = [
  {
    title: "Infrastructure Summary",
    desc: "Sites, devices and sensor inventory, with health by type and site.",
    href: "/reports/infrastructure-summary",
    icon: Server,
  },
  {
    title: "Alert Analysis",
    desc: "Alert volume trends, severity breakdown and time to resolve.",
    href: "/reports/alert-analysis",
    icon: BellRing,
  },
  {
    title: "Collector Performance",
    desc: "Collection success rate, run duration and data lag per source.",
    href: "/reports/collector-performance",
    icon: Activity,
  },
  {
    title: "SOP Execution Log",
    desc: "History of automated procedure runs and their outcomes.",
    href: "/reports/sop-execution-log",
    icon: BookOpenCheck,
  },
];

export default function ReportsPage() {
  return (
    <PageShell title="Reports" subtitle="Operational reports built from this tenant's collected data.">
      <div className="pk-tiles">
        {REPORTS.map(({ title, desc, href, icon: Icon }) => (
          <Link key={href} href={href} className="pk-tile">
            <span className="pk-tile__icon" aria-hidden="true">
              <Icon size={16} />
            </span>
            <h2 className="pk-tile__title">{title}</h2>
            <p className="pk-tile__desc">{desc}</p>
            <span className="pk-tile__cta">Open report →</span>
          </Link>
        ))}
      </div>
    </PageShell>
  );
}
