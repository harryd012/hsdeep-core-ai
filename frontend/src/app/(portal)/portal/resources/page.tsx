"use client";

/**
 * /portal/resources — Resource Explorer entry point.
 *
 * The resource list is NOT fetched here: ResourceExplorer only loads once a type
 * is selected, so this page performs no data request of its own.
 */

import { PortalShell } from "@/components/portal/PortalShell";
import { ResourceExplorer } from "@/components/portal/ResourceExplorer";

export default function PortalResourcesPage() {
  return (
    <PortalShell
      breadcrumb={["Portal", "Resources"]}
      title="Resources"
      description="Browse HSDEEP resources. Only resource types with a real backend list API are selectable."
    >
      <ResourceExplorer />
    </PortalShell>
  );
}
