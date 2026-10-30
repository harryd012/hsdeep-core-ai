/**
 * deploymentReadinessData.ts — evidence PROVISION for the deployment
 * readiness panel.
 *
 * Every record below is a REAL, MANUALLY EXECUTED result captured by the
 * release engineer outside the application:
 *   helm version --short     -> v4.3.0+gbec5b06
 *   helm lint deployment/helm/hsdeep  -> exit 0
 *   helm template <chart> --dry-run    -> exit 0
 *   helm lint chart icon advisory      -> WARNING (non-failing)
 *   Kubernetes connectivity            -> NOT CHECKED
 *   Kubernetes authentication          -> NOT CHECKED
 *   Application deployment             -> NOT DEPLOYED (sprint does not
 *                                         deploy; no cluster evidence)
 *   Live pod health                    -> NOT AVAILABLE (nothing deployed)
 *   Production configuration validation-> NOT CHECKED (not executed)
 *
 * This field is deliberately a manifest of observed evidence, not a
 * narrative of health: unknown Kubernetes / deployment state is never
 * presented as PASS.
 *
 * NOTE: no verification timestamp is fabricated. Anything not
 * independently established is `verificationTime: null` and the panel
 * renders "not recorded".
 */

import type { ReadinessEvidenceRecord } from "./deploymentReadinessModels";

export const READINESS_EVIDENCE: readonly ReadinessEvidenceRecord[] = [
  {
    id: "HELM-001",
    title: "Helm version",
    status: "PASS",
    provenance: "MANUAL",
    source: "helm version --short (executed by release engineer)",
    detail: "Helm v4.3.0+gbec5b06 available on PATH for local validation.",
    environment: "local windows workstation",
    version: "4.3.0+gbec5b06",
    exitCode: null,
    verificationTime: null,
  },
  {
    id: "HELM-002",
    title: "Helm lint (chart validity)",
    status: "PASS",
    provenance: "MANUAL",
    source: "helm lint deployment/helm/hsdeep (exit code 0; 1 chart linted, 0 failed)",
    detail: "Chart packaging is valid under the project's helm lint rules.",
    environment: "local windows workstation",
    version: "v4.3.0+gbec5b06",
    exitCode: 0,
    verificationTime: null,
  },
  {
    id: "HELM-003",
    title: "Helm template rendering",
    status: "PASS",
    provenance: "MANUAL",
    source:
      "helm template deployment/helm/hsdeep --dry-run (exit code 0)",
    detail: "12 manifests rendered with 0 errors; default values are self-consistent.",
    environment: "local windows workstation",
    version: "v4.3.0+gbec5b06",
    exitCode: 0,
    verificationTime: null,
  },
  {
    id: "HELM-004",
    title: "Chart icon recommendation",
    status: "WARNING",
    provenance: "MANUAL",
    source: "helm lint chart metadata advisory (helm lint exit 0)",
    detail: "helm lint informational warning: chart icon recommendation (non-failing).",
    environment: "local windows workstation",
    version: "v4.3.0+gbec5b06",
    exitCode: null,
    verificationTime: null,
  },
  {
    id: "K8S-001",
    title: "Kubernetes connectivity",
    status: "NOT_CHECKED",
    provenance: "MANUAL",
    source: "no check executed",
    detail: "Kubernetes connectivity NOT CHECKED - cluster reachability was not probed in this program.",
    environment: "local windows workstation",
    version: null,
    exitCode: null,
    verificationTime: null,
  },
  {
    id: "K8S-002",
    title: "Kubernetes authentication",
    status: "NOT_CHECKED",
    provenance: "MANUAL",
    source: "no check executed",
    detail: "Kubernetes authentication NOT CHECKED - no cluster credentials available to the program.",
    environment: "local windows workstation",
    version: null,
    exitCode: null,
    verificationTime: null,
  },
  {
    id: "APP-DEPLOY-001",
    title: "Application deployment",
    status: "NOT_DEPLOYED",
    provenance: "MANUAL",
    source: "no deployment performed in this program",
    detail: "The application is NOT deployed (deployment is out of scope; no cluster resources exist to verify).",
    environment: "local windows workstation",
    version: null,
    exitCode: null,
    verificationTime: null,
  },
  {
    id: "POD-HEALTH-001",
    title: "Live pod health",
    status: "NOT_AVAILABLE",
    provenance: "MANUAL",
    source: "no live pods to probe",
    detail: "Live pod health NOT AVAILABLE - no containers are deployed, so a readiness/liveness probe has nothing to read.",
    environment: "local windows workstation",
    version: null,
    exitCode: null,
    verificationTime: null,
  },
  {
    id: "PROD-CONFIG-001",
    title: "Production configuration validation",
    status: "NOT_CHECKED",
    provenance: "MANUAL",
    source: "not executed",
    detail: "Production configuration validation NOT CHECKED - not executed in this program.",
    environment: "local windows workstation",
    version: null,
    exitCode: null,
    verificationTime: null,
  },
];

/** Policy notice rendered beside the section (never data, never a claim). */
export const readinessPolicyNote =
  "Local helm PASS (HELM-001..003) proves the toolchain and chart only. " +
  "DEPLOYED/PROD-READY requires K8S connectivity + authentication, a real deployment and live pod health; " +
  "unknown Kubernetes state is never PASS.";
