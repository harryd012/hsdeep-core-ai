"use client";

import SocAnalystRoster from "@/components/noc/SocAnalystRoster";
import { PageShell } from "@/components/ui/PageKit";

export default function SocAnalystRosterPage() {
  return (
    <PageShell
      title="SOC Analyst Roster"
      subtitle="Who is on the security operations team, what they are working on, and how they are performing."
      crumbs={[{ label: "SOC Analysis", href: "/soc-analysis" }, { label: "Roster" }]}
    >
      <SocAnalystRoster />
    </PageShell>
  );
}
