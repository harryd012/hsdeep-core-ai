"""
Canonical RBAC catalog (Auth + RBAC sprint, P1).

Single source of truth for:
  * PERMISSION_CATALOG - the system-wide `permissions` rows (permissions are
    NOT tenant-scoped; see app/models/permission.py). Seeded once by the
    Alembic data migration.
  * DEFAULT_ROLES - the baseline roles provisioned PER TENANT (roles ARE
    tenant-scoped). Created for a tenant by
    app.services.auth_service.provision_tenant_rbac(), invoked at tenant
    creation and by tests/seed scripts.

Keeping both here (rather than duplicating literals in the migration, the
service, and the tests) is the "avoid duplicate business logic" rule: adding
a permission means editing this file only. Codes follow the existing
`<resource>.<action>` convention already used in app/models/permission.py
("devices.read", "users.create").

A role granted the sentinel ALL_PERMISSIONS gets every catalog permission
(used for the tenant Owner role) - this is resolved at provisioning time
into concrete permission rows, so the stored grants stay explicit and
auditable rather than relying on a magic wildcard at check time.
"""
from __future__ import annotations

from typing import Final

# (code, human description)
PERMISSION_CATALOG: Final[list[tuple[str, str]]] = [
    # Monitoring sources / collectors
    ("monitoring.read", "View monitoring sources and collector runs"),
    ("monitoring.create", "Create monitoring sources"),
    ("monitoring.update", "Update monitoring sources"),
    ("monitoring.delete", "Delete monitoring sources"),
    # Infrastructure inventory
    ("sites.read", "View sites"),
    ("sites.write", "Create/update/delete sites"),
    ("devices.read", "View devices, interfaces, sensors and metrics"),
    ("devices.write", "Create/update/delete devices"),
    ("backup.read", "View backup vaults, protected items and backup jobs"),
    ("business_services.read", "View business services"),
    ("business_services.write", "Create/update/delete business services"),
    # Alerts / incidents
    ("alerts.read", "View alerts"),
    ("alerts.acknowledge", "Acknowledge alerts"),
    ("alerts.resolve", "Resolve alerts"),
    ("incidents.read", "View incidents"),
    ("incidents.write", "Update/close incidents"),
    ("priorities.read", "View priority scoring and overrides"),
    ("priorities.write", "Apply manual priority overrides"),
    # SOP / runbook / workflow automation
    ("sops.read", "View SOPs"),
    ("sops.write", "Create/update/delete SOPs"),
    ("sops.approve", "Approve SOP versions for use"),
    ("runbooks.read", "View runbooks and executions"),
    ("runbooks.write", "Create runbooks"),
    ("runbooks.execute", "Execute runbooks"),
    ("runbooks.approve", "Approve or reject runbook executions"),
    ("workflows.read", "View workflows"),
    ("workflows.write", "Create/version/delete workflows"),
    ("workflows.execute", "Execute workflows"),
    ("workflows.approve", "Approve workflow versions and executions"),
    ("remediation.execute", "Trigger auto-remediation actions"),
    # Device Action & Infrastructure Control Plane
    ("device_actions.read", "View device action catalog, requests and results"),
    ("device_actions.execute", "Request device actions (read-only diagnostics auto-run; state-changing gated by policy)"),
    ("device_actions.approve", "Approve/deny/modify state-changing device actions"),
    # ITSM
    ("servicenow.read", "View ServiceNow integration configuration/state"),
    ("servicenow.write", "Configure ServiceNow integration"),
    ("itsm.write", "Configure ITSM connections"),
    ("tickets.write", "Create/update/resolve/close tickets"),
    ("tickets.execute", "Assign tickets and approve/execute agent task steps"),
    ("integrations.write", "Create/update integrations"),
    # Operational records
    ("noc.write", "Create/update/acknowledge/resolve NOC and normalized events"),
    ("topology.write", "Create/update/delete topology links and relationships"),
    ("tasks.write", "Create and progress agent tasks"),
    ("agents.write", "Register and update agents"),
    ("changes.write", "Create/update change requests"),
    # Dashboard
    ("dashboard.read", "View dashboards and analytics"),
    # Usage metering & billing
    ("usage.read", "View tenant usage data and consumption metrics"),
    ("billing.read", "View the tenant subscription, entitlements and onboarding status"),
    ("billing.write", "Select, change or cancel the tenant subscription"),
    # Administration (tenant-local IAM)
    ("users.read", "View users"),
    ("users.create", "Create users"),
    ("users.update", "Update users"),
    ("users.delete", "Delete users"),
    ("roles.read", "View roles and permission assignments"),
    ("roles.write", "Create/update/delete roles and assign permissions"),
]

ALL_PERMISSION_CODES: Final[frozenset[str]] = frozenset(code for code, _ in PERMISSION_CATALOG)

# Sentinel meaning "every catalog permission", expanded at provisioning time.
ALL_PERMISSIONS: Final[str] = "*"

# role name -> (description, granted permission codes | ALL_PERMISSIONS)
DEFAULT_ROLES: Final[dict[str, tuple[str, object]]] = {
    "Owner": ("Full administrative control of the tenant", ALL_PERMISSIONS),
    "Admin": (
        "Manage infrastructure, integrations and users (no role editing)",
        [
            c
            for c in ALL_PERMISSION_CODES
            if not c.startswith("roles.")
        ],
    ),
    "Operator": (
        "NOC/SOC operator: act on alerts/incidents and run remediation",
        [
            "monitoring.read", "sites.read", "devices.read", "backup.read", "business_services.read",
            "alerts.read", "alerts.acknowledge", "alerts.resolve",
            "incidents.read", "incidents.write", "priorities.read",
            "sops.read", "runbooks.read", "runbooks.execute",
            "workflows.read", "workflows.execute", "remediation.execute",
            "device_actions.read", "device_actions.execute",
            "servicenow.read", "dashboard.read",
            "noc.write", "tasks.write", "changes.write",
            "tickets.write", "tickets.execute",
        ],
    ),
    "Viewer": (
        "Read-only access to monitoring, alerts and dashboards",
        [c for c in ALL_PERMISSION_CODES if c.endswith(".read")],
    ),
}


def resolve_role_permissions(codes: object) -> frozenset[str]:
    """Expand a DEFAULT_ROLES grant spec into concrete permission codes.
    Accepts the ALL_PERMISSIONS sentinel or an explicit iterable of codes and
    always returns codes that actually exist in PERMISSION_CATALOG (guards
    against a typo silently granting nothing)."""
    if codes == ALL_PERMISSIONS:
        return ALL_PERMISSION_CODES
    resolved = frozenset(codes)  # type: ignore[arg-type]
    unknown = resolved - ALL_PERMISSION_CODES
    if unknown:
        raise ValueError(f"Unknown permission codes in role spec: {sorted(unknown)}")
    return resolved
