"use client";

/**
 * /portal/resources/[resourceType] — Resource Explorer for one fixed type.
 *
 * `useParams` is used rather than the page `params` prop so this works on both
 * Next 14 (plain object) and Next 15 (promise) without version-specific typing.
 */

import { useParams } from "next/navigation";

import { PortalShell } from "@/components/portal/PortalShell";
import { ResourceExplorer } from "@/components/portal/ResourceExplorer";
import { getResourceType } from "@/lib/resources/resource-registry";

export default function PortalResourceTypePage() {
  const params = useParams<{ resourceType: string }>();
  const key = String(params?.resourceType ?? "");
  const def = getResourceType(key);

  return (
    <PortalShell
      breadcrumb={["Portal", "Resources", def?.label ?? key]}
      title={def?.label ?? "Resource"}
      description={def?.description ?? "Unknown resource type."}
    >
      <ResourceExplorer fixedType={key} />
    </PortalShell>
  );
}
