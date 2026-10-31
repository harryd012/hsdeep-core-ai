import UniversalResourcePanel from "@/components/infrastructure/UniversalResourcePanel";

/**
 * /infrastructure/universal — the universal (provider-neutral) infrastructure
 * view: tenant health, every resource type, and per-resource capability,
 * interface, telemetry and network detail.
 *
 * Server component shell only; all data loading happens inside the client panel
 * through the authenticated apiFetch client.
 */
export default function UniversalInfrastructurePage() {
  return <UniversalResourcePanel />;
}
