import Link from "next/link";

import InvestigationWorkspace from "@/components/investigation/InvestigationWorkspace";

/**
 * /incidents/{id} — the Incident Investigation Workspace (PHASE 3).
 *
 * A thin server page: it resolves the route param and mounts the client-side
 * workspace, which owns the data loading. The page itself asserts nothing about
 * the incident, so a missing or unauthorised incident cannot render as a
 * plausible-looking empty investigation.
 *
 * `params` is a Promise in this Next version, so it is awaited.
 */
export const dynamic = "force-dynamic";

export default async function IncidentInvestigationPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  return (
    <div className="dashboard" style={{ paddingTop: "clamp(84px,8.5vh,104px)" }}>
      <div className="overview" style={{ gridTemplateColumns: "1fr", gap: 16 }}>
        <div style={{ marginBottom: 12 }}>
          <Link href="/incidents" style={{ fontSize: 12, color: "var(--cyan)", textDecoration: "none" }}>
            &larr; Back to incident intelligence
          </Link>
        </div>
        <InvestigationWorkspace incidentId={id} />
      </div>
    </div>
  );
}
