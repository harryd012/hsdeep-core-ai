"""
Every mutating /api route enforces a catalog permission (acceptance E:
no unauthorized actions within a tenant), and approvals are attributed to
the authenticated user rather than a client-supplied id.

Before this guard, 89 POST/PUT/PATCH/DELETE routes checked only the tenant:
a Viewer could resolve incidents, delete monitoring sources, approve runbook
executions or approve SOP versions, and the approver id came from the body.
"""
from __future__ import annotations

import uuid

import httpx
import pytest
from fastapi.routing import APIRoute

from app.core import auth
from app.core.auth import create_access_token
from app.core.permissions import ALL_PERMISSION_CODES, DEFAULT_ROLES, resolve_role_permissions
from app.main import app
from app.models.user import User

pytestmark = pytest.mark.usefixtures("seeded_subscription")

# Authenticated by something other than a user permission: credentials,
# signatures or enrollment tokens, or public by design.
PUBLIC_WRITE_ROUTES = {
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/refresh"),
    ("POST", "/api/auth/logout"),
    # Signed-in self-service: ends only the caller's own sessions.
    ("POST", "/api/auth/logout-all"),
    # Account self-service: authorised by a single-use emailed token, or
    # (forgot) answered identically for any email; rate limited per IP.
    ("POST", "/api/auth/password/forgot"),
    ("POST", "/api/auth/password/reset"),
    ("POST", "/api/auth/email/verify"),
    ("POST", "/api/auth/invite/accept"),
    ("POST", "/api/billing/signup"),
    ("POST", "/api/billing/webhook"),
    ("POST", "/api/monitoring/collectors/register"),
    ("POST", "/api/servicenow/webhooks/incoming/{connection_id}"),
    ("POST", "/api/v1/ingest/metrics"),
    ("POST", "/api/v1/ingest/events"),
}

VIEWER = resolve_role_permissions(DEFAULT_ROLES["Viewer"][1])
OPERATOR = resolve_role_permissions(DEFAULT_ROLES["Operator"][1])


def _required_permissions(dependant) -> set[str]:
    """Permission codes required anywhere in a route's dependency tree."""
    codes: set[str] = set()
    for sub in dependant.dependencies:
        if "require_permission" in getattr(sub.call, "__qualname__", ""):
            cells = {c.cell_contents for c in (sub.call.__closure__ or ())}
            codes |= {c for c in cells if isinstance(c, str)}
        elif sub.call is auth.require_admin:
            codes.add("<admin>")
        codes |= _required_permissions(sub)
    return codes


def _write_routes():
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path.startswith("/api"):
            for method in route.methods & {"POST", "PUT", "PATCH", "DELETE"}:
                yield method, route


def _client(tenant_id: uuid.UUID, permissions, *, user_id: uuid.UUID | None = None) -> httpx.AsyncClient:
    token = create_access_token(
        user_id=user_id or uuid.uuid4(),
        tenant_id=tenant_id,
        email="rbac-test@hsdeep.local",
        is_superuser=False,
        permissions=frozenset(permissions),
    )
    return httpx.AsyncClient(app=app, base_url="http://test", headers={"Authorization": f"Bearer {token}"})


# ---------------------------------------------------------------------------
# Structural guard over the whole route table
# ---------------------------------------------------------------------------


def test_every_mutating_route_requires_a_permission():
    unguarded = sorted(
        f"{method} {route.path}"
        for method, route in _write_routes()
        if (method, route.path) not in PUBLIC_WRITE_ROUTES and not _required_permissions(route.dependant)
    )
    assert unguarded == [], f"write routes without a permission check: {unguarded}"


def test_required_codes_exist_in_the_catalog():
    """A code missing from the catalog can never be granted to a role."""
    used = set()
    for route in app.routes:
        if isinstance(route, APIRoute):
            used |= _required_permissions(route.dependant)
    unknown = used - ALL_PERMISSION_CODES - {"<admin>"}
    assert unknown == set()


def test_viewer_cannot_reach_any_guarded_write_route():
    reachable = sorted(
        f"{method} {route.path}"
        for method, route in _write_routes()
        if (codes := _required_permissions(route.dependant)) and codes <= VIEWER
    )
    # Read-only computations that are POSTs by shape (a request body, no
    # writes). Everything else must be closed to Viewer.
    assert reachable == [
        "POST /api/copilot/chat",
        "POST /api/failover/evaluate",
        "POST /api/failover/simulate",
        "POST /api/infrastructure/intelligence/correlation/context",
        "POST /api/network-intelligence/evaluate",
        "POST /api/paloalto/rca/assess",
        "POST /api/paloalto/runbooks/match",
    ]


@pytest.mark.parametrize("approval_code", ["runbooks.approve", "workflows.approve", "sops.approve"])
def test_operator_cannot_approve(approval_code):
    assert approval_code not in OPERATOR


# ---------------------------------------------------------------------------
# HTTP behaviour
# ---------------------------------------------------------------------------

DENIED_FOR_VIEWER = [
    ("post", "/api/incidents/{id}/resolve", "incidents.write", {}),
    ("post", "/api/alerts/{id}/acknowledge", "alerts.acknowledge", {"acknowledged_by": str(uuid.uuid4())}),
    ("delete", "/api/monitoring/sources/{id}", "monitoring.delete", None),
    ("post", "/api/runbooks/executions/{id}/approve", "runbooks.approve", {}),
    ("post", "/api/workflows/executions/{id}/approve", "workflows.approve", {"node_id": "n1"}),
    ("post", "/api/sops/{id}/versions/{id}/approve", "sops.approve", {}),
    ("patch", "/api/tickets/{id}", "tickets.write", {}),
    ("post", "/api/paloalto/executions/{id}/approve", "runbooks.approve", {"step_id": "session_packet_capture"}),
]


@pytest.mark.parametrize("method,path,code,body", DENIED_FOR_VIEWER)
async def test_viewer_gets_403_and_permitted_user_passes_the_guard(tenant, method, path, code, body):
    url = path.replace("{id}", str(uuid.uuid4()))
    kwargs = {"json": body} if body is not None else {}

    async with _client(tenant.id, VIEWER) as client:
        denied = await client.request(method.upper(), url, **kwargs)
    assert denied.status_code == 403, denied.text
    assert code in denied.json()["detail"]

    # With the code granted the request gets past RBAC and reaches the
    # handler, which answers 404 for the random id.
    async with _client(tenant.id, VIEWER | {code}) as client:
        allowed = await client.request(method.upper(), url, **kwargs)
    assert allowed.status_code == 404, allowed.text


async def test_paloalto_step_role_is_not_client_supplied(tenant):
    """?actor=admin must not skip the approval an approval-gated step needs:
    the safety role is fixed server-side, and approval comes only from the
    /approve route (runbooks.approve)."""
    async with _client(tenant.id, OPERATOR | {"runbooks.execute"}) as client:
        created = await client.post("/api/paloalto/executions", json={})
        assert created.status_code == 201, created.text
        ex_id = created.json()["id"]
        for step in ("incident_intake", "traffic_flow_map", "traffic_log_investigation"):
            ran = await client.post(f"/api/paloalto/executions/{ex_id}/steps/{step}/run")
            assert ran.status_code == 200, ran.text

        gated = await client.post(
            f"/api/paloalto/executions/{ex_id}/steps/session_packet_capture/run",
            params={"actor": "admin"},
        )
    assert gated.status_code == 200, gated.text
    step = next(s for s in gated.json()["steps"] if s["id"] == "session_packet_capture")
    assert step["status"] == "blocked", step


async def test_sop_approval_is_attributed_to_the_signed_in_user(tenant, db):
    user = User(tenant_id=tenant.id, email=f"approver-{uuid.uuid4().hex[:6]}@hsdeep.local")
    db.add(user)
    await db.commit()
    approver = user.id
    async with _client(tenant.id, {"sops.read", "sops.write"}) as author:
        sop = await author.post("/api/sops", json={
            "sop_number": f"SOP-{uuid.uuid4().hex[:6]}",
            "name": "Device unreachable",
            "category": "availability",
        })
        assert sop.status_code == 201, sop.text
        sop_id = sop.json()["id"]
        version = await author.post(f"/api/sops/{sop_id}/versions", json={"steps": [{"title": "Ping"}]})
        assert version.status_code == 201, version.text
        version_id = version.json()["id"]

        # The author lacks sops.approve.
        self_approve = await author.post(f"/api/sops/{sop_id}/versions/{version_id}/approve", json={})
        assert self_approve.status_code == 403

    spoofed = uuid.uuid4()
    async with _client(tenant.id, {"sops.read", "sops.approve"}, user_id=approver) as client:
        approved = await client.post(
            f"/api/sops/{sop_id}/versions/{version_id}/approve", json={"approved_by": str(spoofed)}
        )
        assert approved.status_code == 200, approved.text
        detail = await client.get(f"/api/sops/{sop_id}")
    versions = {v["id"]: v for v in detail.json()["versions"]}
    assert versions[version_id]["approved_by"] == str(approver)


async def test_api_key_principal_cannot_approve():
    principal = auth.Principal(
        user_id=uuid.UUID(int=0),
        tenant_id=uuid.uuid4(),
        email="api-key",
        is_superuser=False,
        permissions=frozenset({"sops.approve"}),
    )
    with pytest.raises(auth.HTTPException) as exc:
        auth.get_acting_user_id(principal)
    assert exc.value.status_code == 403
