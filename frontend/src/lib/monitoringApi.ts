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
import {
  apiFetch,
  DEFAULT_TENANT_ID,
  DIAGNOSTIC_TIMEOUT_MS,
  OPERATOR_WRITE_TIMEOUT_MS,
  withTenant,
  type MonitoringSourceOut,
} from "./api";

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
    undefined,
    // The report PROBES every configured source (a transport handshake each),
    // so it is a diagnostic rather than a quick read.
    DIAGNOSTIC_TIMEOUT_MS,
  );
}

export function probeSourceReadiness(
  sourceId: string,
  tenantId: string = DEFAULT_TENANT_ID,
): Promise<ReadinessResultOut> {
  return apiFetch<ReadinessResultOut>(
    `/api/monitoring/sources/${sourceId}/readiness?${withTenant(tenantId).toString()}`,
    { method: "POST" },
    // A real handshake to an unreachable SNMP target outlives the default cap.
    DIAGNOSTIC_TIMEOUT_MS,
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
    // A full collection cycle is a diagnostic, not a quick read.
    DIAGNOSTIC_TIMEOUT_MS,
  );
}

// ---------------------------------------------------------------------------
// Source onboarding (Phase 9.9) - create / edit through the EXISTING
// /api/monitoring/sources endpoints.
//
// Credential handling: `credential` is WRITE-ONLY. It is sent once in the
// request body, encrypted server-side into the source's credential column, and
// never returned by any endpoint - which is why the read type only carries
// `credential_configured`. Nothing here stores a secret client-side.
// ---------------------------------------------------------------------------

export interface MonitoringSourceInput {
  name: string;
  source_type: string;
  hostname?: string | null;
  ip_address?: string | null;
  port?: number | null;
  protocol?: string | null;
  snmp_version?: string | null;
  username?: string | null;
  is_active?: boolean;
  poll_interval_seconds?: number;
  /** Write-only credential material (SNMP v1/v2c community, or v3 auth key;
   *  for cloud sources, the service principal's client secret). */
  credential?: string | null;
  /** Source-specific settings (e.g. Azure tenant_id / subscription_id / client_id). */
  config?: Record<string, string>;
}

/** The collector's credential field for SNMP: the community is sent as
 *  `api_key` (v1/v2c) and the v3 authentication key as `password`. */
function credentialField(sourceType: string, snmpVersion: string | null, credential: string | null) {
  if (!credential) return {};
  if (sourceType === "snmp" && snmpVersion === "v3") return { password: credential };
  return { api_key: credential };
}

export function createMonitoringSource(
  input: MonitoringSourceInput,
  tenantId: string = DEFAULT_TENANT_ID,
): Promise<MonitoringSourceOut> {
  const { credential, ...rest } = input;
  return apiFetch<MonitoringSourceOut>(
    `/api/monitoring/sources?${withTenant(tenantId).toString()}`,
    {
      method: "POST",
      body: JSON.stringify({
        // The API's write schema requires this field, and the route then
        // OVERRIDES it with the authenticated principal's tenant - so sending it
        // here cannot place a source in another tenant.
        tenant_id: tenantId,
        ...rest,
        ...credentialField(input.source_type, input.snmp_version ?? null, credential ?? null),
      }),
    },
    // Encrypting the credential, the entitlement check and the audit writes on
    // a slow collector host outlive the default cap; aborting a save mid-flight
    // could leave a source created while the operator is told it timed out.
    OPERATOR_WRITE_TIMEOUT_MS,
  );
}

export function updateMonitoringSource(
  sourceId: string,
  input: Partial<MonitoringSourceInput>,
  tenantId: string = DEFAULT_TENANT_ID,
): Promise<MonitoringSourceOut> {
  const { credential, ...rest } = input;
  return apiFetch<MonitoringSourceOut>(
    `/api/monitoring/sources/${sourceId}?${withTenant(tenantId).toString()}`,
    {
      method: "PATCH",
      body: JSON.stringify({
        ...rest,
        // An omitted credential means "leave the stored one alone".
        ...credentialField(input.source_type ?? "snmp", input.snmp_version ?? null, credential ?? null),
      }),
    },
    OPERATOR_WRITE_TIMEOUT_MS,
  );
}

