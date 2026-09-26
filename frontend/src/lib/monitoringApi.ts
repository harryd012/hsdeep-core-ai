/**
 * Monitoring collection API -- source readiness and synchronisation.
 *
 * Transport, base URL, error handling and the tenant-id convention come from
 * ./api (apiFetch/withTenant); this module owns only the monitoring surface.
 *
 * Readiness is a READ-ONLY diagnostic: status "ready" means the transport
 * answered and credentials were accepted. It does NOT mean inventory was
 * collected -- reachability and discovery are separate evidence levels.
 *
 * Synchronisation is the real collection path (CollectionOrchestrator) and DOES
 * persist inventory.
 */
import { apiFetch, DEFAULT_TENANT_ID, withTenant } from "./api";

// ---------------------------------------------------------------------------
// Collection readiness (Phase 3B §10) — READ-ONLY diagnostics.
//
// `status === "ready"` means the transport answered AND credentials were
// accepted. It does NOT mean any inventory was collected: reachability and
// discovery are deliberately separate evidence levels, and the UI must keep
// that distinction visible rather than implying a successful discovery.
// ---------------------------------------------------------------------------

export interface ReadinessResultOut {
  status: string;
  stage: string;
  message: string;
  target: string;
  protocol: string;
  safe_details: Record<string, unknown>;
}

export interface ReadinessReportOut {
  sources_checked: number;
  results: ReadinessResultOut[];
  coverage: ReadinessResultOut[];
}

export function getReadinessReport(
  tenantId: string = DEFAULT_TENANT_ID,
): Promise<ReadinessReportOut> {
  return apiFetch<ReadinessReportOut>(
    `/api/monitoring/readiness?${withTenant(tenantId).toString()}`,
  );
}

export function probeSourceReadiness(
  sourceId: string,
  tenantId: string = DEFAULT_TENANT_ID,
): Promise<ReadinessResultOut> {
  return apiFetch<ReadinessResultOut>(
    `/api/monitoring/sources/${sourceId}/readiness?${withTenant(tenantId).toString()}`,
    { method: "POST" },
  );
}

export interface SyncResultOut {
  source_id: string;
  status: string;
  sensors_created: number;
  sensors_updated: number;
  sensors_total: number;
  synced_at: string;
  error: string | null;
}

/** Trigger one collection cycle. This DOES persist inventory - it is the real
 *  collection path (CollectionOrchestrator), not a diagnostic probe. */
export function syncMonitoringSource(
  sourceId: string,
  tenantId: string = DEFAULT_TENANT_ID,
): Promise<SyncResultOut> {
  return apiFetch<SyncResultOut>(
    `/api/monitoring/sources/${sourceId}/sync?${withTenant(tenantId).toString()}`,
    { method: "POST" },
  );
}

