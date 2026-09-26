"""
Tenant-scoped Operational SLA policy CRUD (``/api/failover/policies``).

The write side of the ONE policy store the detector evaluates. These cases pin
the properties that make it safe to expose configuration over HTTP:

* a policy that cannot be evaluated honestly is REFUSED (named field, 422) and
  nothing is written - no silent clamping, no stored no-op;
* an ambiguous set is refused (409) - the resolver returns one policy per metric,
  so a duplicate would make the answer depend on row order;
* a ``scope_id`` from another tenant is simply "not in this tenant", so nothing
  about the other tenant is disclosed;
* writes are gated on ``monitoring.update``; reads stay on ``devices.read``;
* a delete is a SOFT delete that immediately stops feeding the detector;
* policy edits actually change what the resolver returns (CRUD is not a
  write-only side show);
* every write emits an audit event.
"""
from __future__ import annotations

import uuid

import httpx

from app.core.auth import create_access_token
from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.operational_sla_policy import (
    METRIC_AVAILABILITY_PCT,
    METRIC_LATENCY_MS,
    METRIC_PACKET_LOSS_PCT,
    SCOPE_ALL_WAN,
    SCOPE_SITE,
    OperationalSLAPolicy,
)
from app.repositories.operational_sla_policy_repository import (
    OperationalSLAPolicyRepository,
)
from app.services.events.event_bus import get_event_bus, set_event_bus
from app.services.failover_intelligence.policies import resolve_policies
from tests.test_failover_intelligence_db import seed_edge

BASE = "/api/failover/policies"
#: What a NOC operator holds: enough to read the surface, not to retune it.
READ_PERMISSIONS = frozenset({"devices.read"})
#: What an administrator holds: the configuration act.
WRITE_PERMISSIONS = frozenset({"devices.read", "monitoring.update"})


def client(tenant_id, permissions=WRITE_PERMISSIONS):
    headers = (
        {
            "Authorization": "Bearer "
            + create_access_token(
                user_id=uuid.uuid4(),
                tenant_id=tenant_id,
                email="policy-crud@hsdeep.local",
                is_superuser=False,
                permissions=frozenset(permissions),
            )
        }
        if tenant_id is not None
        else {}
    )
    return httpx.AsyncClient(app=app, base_url="http://test", headers=headers)


def latency_policy(**overrides) -> dict:
    payload = {
        "name": "MPLS latency",
        "metric": METRIC_LATENCY_MS,
        "scope": SCOPE_ALL_WAN,
        "warning_threshold": 60,
        "critical_threshold": 120,
        "clear_threshold": 45,
        "min_consecutive_samples": 2,
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------
async def test_create_persists_and_returns_the_stored_contract(tenant, db):
    async with client(tenant.id) as api:
        response = await api.post(BASE, json=latency_policy())
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "MPLS latency"
    assert body["metric"] == METRIC_LATENCY_MS
    assert body["scope"] == SCOPE_ALL_WAN
    assert body["scope_id"] is None
    assert body["warning_threshold"] == 60.0
    assert body["critical_threshold"] == 120.0
    assert body["clear_threshold"] == 45.0
    assert body["is_active"] is True
    assert body["attributes"] == {}
    assert body["created_at"] and body["updated_at"]

    stored = await OperationalSLAPolicyRepository(db).get(
        uuid.UUID(body["id"]), tenant.id
    )
    assert stored is not None and stored.name == "MPLS latency"


async def test_create_accepts_each_metric_direction_correctly(tenant):
    """Availability is lower-is-worse; the same numbers the other way round fail."""
    async with client(tenant.id) as api:
        good = await api.post(
            BASE,
            json={
                "name": "Availability floor",
                "metric": METRIC_AVAILABILITY_PCT,
                "warning_threshold": 99.5,
                "critical_threshold": 99.0,
                "clear_threshold": 99.9,
            },
        )
        bad = await api.post(
            BASE,
            json={
                "name": "Availability inverted",
                "metric": METRIC_AVAILABILITY_PCT,
                "warning_threshold": 99.0,
                "critical_threshold": 99.5,
            },
        )
    assert good.status_code == 201, good.text
    assert bad.status_code == 422
    detail = bad.json()["detail"]
    assert detail["field"] == "critical_threshold"
    assert "BELOW warning_threshold" in detail["message"]


async def test_create_rejects_policies_that_cannot_be_evaluated(tenant):
    """Each refused policy names the field that makes it unevaluatable."""
    cases = [
        (
            latency_policy(warning_threshold=None, critical_threshold=None),
            "critical_threshold",
        ),
        (latency_policy(warning_threshold=120, critical_threshold=60),
         "critical_threshold"),
        (latency_policy(warning_threshold=60, critical_threshold=60),
         "critical_threshold"),
        # clear ABOVE the entry level would never clear -> the detector latches.
        (latency_policy(clear_threshold=200), "clear_threshold"),
        # percentages are bounded 0..100
        (
            latency_policy(
                metric=METRIC_PACKET_LOSS_PCT,
                warning_threshold=5,
                critical_threshold=150,
            ),
            "critical_threshold",
        ),
        (latency_policy(warning_threshold=-1), "warning_threshold"),
        (latency_policy(min_consecutive_samples=0), "min_consecutive_samples"),
        (latency_policy(min_consecutive_samples=5000), "min_consecutive_samples"),
        (latency_policy(metric="cpu_temperature_c"), "metric"),
        (latency_policy(name="   "), "name"),
    ]
    async with client(tenant.id) as api:
        for payload, expected_field in cases:
            response = await api.post(BASE, json=payload)
            assert response.status_code == 422, (payload, response.text)
            assert response.json()["detail"]["field"] == expected_field, payload


async def test_failed_validation_writes_nothing(tenant, db):
    async with client(tenant.id) as api:
        before = await OperationalSLAPolicyRepository(db).count(tenant.id)
        response = await api.post(
            BASE, json=latency_policy(warning_threshold=200, critical_threshold=10)
        )
    assert response.status_code == 422
    assert await OperationalSLAPolicyRepository(db).count(tenant.id) == before


async def test_create_rejects_scope_mismatches(tenant):
    async with client(tenant.id) as api:
        all_wan_with_id = await api.post(
            BASE, json=latency_policy(scope_id=str(uuid.uuid4()))
        )
        site_without_id = await api.post(BASE, json=latency_policy(scope=SCOPE_SITE))
        unknown_scope = await api.post(BASE, json=latency_policy(scope="datacenter"))
    assert all_wan_with_id.status_code == 422
    assert all_wan_with_id.json()["detail"]["field"] == "scope_id"
    assert site_without_id.status_code == 422
    assert site_without_id.json()["detail"]["field"] == "scope_id"
    assert unknown_scope.status_code == 422
    assert unknown_scope.json()["detail"]["field"] == "scope"


# ---------------------------------------------------------------------------
# Scope targets and tenant isolation
# ---------------------------------------------------------------------------
async def seed_committed_edge(tenant_id):
    async with AsyncSessionLocal() as session:
        seeded = await seed_edge(session, tenant_id)
        site_id = seeded["site"].id
        link_ids = [link.id for link in seeded["links"]]
        await session.commit()
        return site_id, link_ids


async def test_create_rejects_unknown_or_foreign_scope_id(tenant):
    from sqlalchemy import delete

    from app.models.tenant import Tenant

    # The foreign tenant and its topology must be COMMITTED before the API (which
    # opens its own session) can be asked about them.
    other_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(Tenant(id=other_id, name="Other", slug=f"other-{other_id.hex[:8]}"))
        await session.flush()
        foreign = await seed_edge(session, other_id)
        foreign_site_id = foreign["site"].id
        foreign_link_id = foreign["links"][0].id
        await session.commit()
    try:
        async with client(tenant.id) as api:
            unknown = await api.post(
                BASE,
                json=latency_policy(scope=SCOPE_SITE, scope_id=str(uuid.uuid4())),
            )
            foreign_site = await api.post(
                BASE,
                json=latency_policy(scope=SCOPE_SITE, scope_id=str(foreign_site_id)),
            )
            foreign_link = await api.post(
                BASE,
                json=latency_policy(
                    name="Foreign link",
                    scope="link",
                    scope_id=str(foreign_link_id),
                ),
            )
        for response in (unknown, foreign_site, foreign_link):
            assert response.status_code == 422, response.text
            detail = response.json()["detail"]
            assert detail["field"] == "scope_id"
            assert "does not exist in this tenant" in detail["message"]
        # A foreign id is treated EXACTLY like an unknown one - same status, same
        # field, same wording - so the response cannot be used to probe whether
        # another tenant owns a given site or link.
        assert unknown.json()["detail"]["message"].split()[-4:] == (
            foreign_site.json()["detail"]["message"].split()[-4:]
        )
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(delete(Tenant).where(Tenant.id == other_id))
            await session.commit()


async def test_create_allows_a_site_scoped_policy_when_the_site_exists(tenant):
    site_id, _link_ids = await seed_committed_edge(tenant.id)
    async with client(tenant.id) as api:
        response = await api.post(
            BASE,
            json=latency_policy(
                name="Branch latency", scope=SCOPE_SITE, scope_id=str(site_id)
            ),
        )
    assert response.status_code == 201, response.text
    assert response.json()["scope"] == SCOPE_SITE
    assert response.json()["scope_id"] == str(site_id)


# ---------------------------------------------------------------------------
# Ambiguity
# ---------------------------------------------------------------------------
async def test_duplicate_name_and_overlapping_scope_are_conflicts(tenant):
    async with client(tenant.id) as api:
        first = await api.post(BASE, json=latency_policy())
        same_name = await api.post(
            BASE, json=latency_policy(name="mpls LATENCY", metric="jitter_ms")
        )
        same_scope_metric = await api.post(
            BASE, json=latency_policy(name="Another latency")
        )
    assert first.status_code == 201, first.text
    assert same_name.status_code == 409
    assert same_name.json()["detail"]["field"] == "name"
    assert same_scope_metric.status_code == 409
    assert same_scope_metric.json()["detail"]["field"] == "metric"
    assert "already covers metric" in same_scope_metric.json()["detail"]["message"]


async def test_the_same_metric_at_a_different_scope_is_not_a_conflict(tenant):
    site_id, _link_ids = await seed_committed_edge(tenant.id)
    async with client(tenant.id) as api:
        global_policy = await api.post(BASE, json=latency_policy())
        tighter = await api.post(
            BASE,
            json=latency_policy(
                name="MPLS latency (branch)",
                scope=SCOPE_SITE,
                scope_id=str(site_id),
                warning_threshold=40,
                critical_threshold=80,
                clear_threshold=30,
            ),
        )
    assert global_policy.status_code == 201, global_policy.text
    assert tighter.status_code == 201, tighter.text


# ---------------------------------------------------------------------------
# Authorisation
# ---------------------------------------------------------------------------
async def test_write_requires_monitoring_update_and_read_stays_on_devices_read(
    tenant,
):
    async with client(tenant.id) as api:
        created = await api.post(BASE, json=latency_policy())
    policy_id = created.json()["id"]

    # Unauthenticated: 401, never 404.
    async with client(None) as anon:
        assert (await anon.post(BASE, json=latency_policy())).status_code == 401
        assert (await anon.get(BASE)).status_code == 401

    # A read-only principal may read, but may not retune the thresholds.
    async with client(tenant.id, READ_PERMISSIONS) as reader:
        assert (await reader.get(BASE)).status_code == 200
        assert (await reader.get(f"{BASE}/{policy_id}")).status_code == 200
        assert (await reader.post(BASE, json=latency_policy())).status_code == 403
        assert (
            await reader.patch(f"{BASE}/{policy_id}", json={"description": "x"})
        ).status_code == 403
        assert (await reader.delete(f"{BASE}/{policy_id}")).status_code == 403


async def test_policy_writes_are_tenant_scoped(tenant, db):
    from sqlalchemy import delete

    from app.models.tenant import Tenant

    async with client(tenant.id) as api:
        created = await api.post(BASE, json=latency_policy())
    policy_id = created.json()["id"]

    other = Tenant(name="Other", slug=f"other-{uuid.uuid4().hex[:8]}")
    db.add(other)
    await db.flush()
    try:
        async with client(other.id) as foreign:
            assert (await foreign.get(f"{BASE}/{policy_id}")).status_code == 404
            assert (
                await foreign.patch(
                    f"{BASE}/{policy_id}", json={"description": "hijack"}
                )
            ).status_code == 404
            assert (await foreign.delete(f"{BASE}/{policy_id}")).status_code == 404
            listed = await foreign.get(BASE)
            assert listed.json()["policies"] == []
            assert listed.json()["total"] == 0
    finally:
        await db.execute(delete(Tenant).where(Tenant.id == other.id))
        await db.flush()


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------
async def test_patch_is_partial_and_revalidates_the_merged_policy(tenant, db):
    async with client(tenant.id) as api:
        created = await api.post(BASE, json=latency_policy())
        policy_id = created.json()["id"]

        # Moving only the critical level must be checked against the STORED
        # warning level, or the pair could be left contradictory.
        broken = await api.patch(
            f"{BASE}/{policy_id}", json={"critical_threshold": 10}
        )
        unchanged = await api.get(f"{BASE}/{policy_id}")

        description_only = await api.patch(
            f"{BASE}/{policy_id}", json={"description": "tighter branch SLA"}
        )
        cleared = await api.patch(
            f"{BASE}/{policy_id}", json={"clear_threshold": None}
        )

    assert broken.status_code == 422
    assert broken.json()["detail"]["field"] == "critical_threshold"
    assert unchanged.json()["critical_threshold"] == 120.0  # nothing was written
    assert description_only.status_code == 200
    assert description_only.json()["description"] == "tighter branch SLA"
    # A targeted PATCH left the other levels exactly as they were.
    assert description_only.json()["warning_threshold"] == 60.0
    assert description_only.json()["critical_threshold"] == 120.0
    assert description_only.json()["clear_threshold"] == 45.0
    # An explicit null clears a level; omitting it keeps it.
    assert cleared.status_code == 200
    assert cleared.json()["clear_threshold"] is None

    stored = await OperationalSLAPolicyRepository(db).get(
        uuid.UUID(policy_id), tenant.id
    )
    assert stored is not None and stored.clear_threshold is None


async def test_patch_rejects_an_unknown_field_only_body(tenant):
    async with client(tenant.id) as api:
        created = await api.post(BASE, json=latency_policy())
        policy_id = created.json()["id"]
        response = await api.patch(f"{BASE}/{policy_id}", json={"bogus": 1})
    assert response.status_code == 422
    assert response.json()["detail"]["field"] == "policy"


async def test_patch_can_disable_without_deleting(tenant, db):
    async with client(tenant.id) as api:
        created = await api.post(BASE, json=latency_policy())
        policy_id = uuid.UUID(created.json()["id"])
        disabled = await api.patch(
            f"{BASE}/{policy_id}", json={"is_active": False}
        )
        still_listed = await api.get(f"{BASE}/{policy_id}")

    assert disabled.status_code == 200
    assert disabled.json()["is_active"] is False
    # Disabling is not deleting: the policy stays visible for the operator.
    assert still_listed.status_code == 200
    repo = OperationalSLAPolicyRepository(db)
    assert all(row.id != policy_id for row in await repo.list_active(tenant.id))
    assert any(row.id == policy_id for row in await repo.list(tenant.id))


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------
async def test_delete_is_soft_and_stops_feeding_the_detector(tenant, db):
    async with client(tenant.id) as api:
        created = await api.post(BASE, json=latency_policy())
        policy_id = uuid.UUID(created.json()["id"])
        deleted = await api.delete(f"{BASE}/{policy_id}")
        gone = await api.get(f"{BASE}/{policy_id}")
        listed = await api.get(BASE)

    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert deleted.json()["policy"]["id"] == str(policy_id)
    assert gone.status_code == 404
    assert listed.json()["total"] == 0
    assert all(row["id"] != str(policy_id) for row in listed.json()["policies"])

    repo = OperationalSLAPolicyRepository(db)
    # Soft, not destroyed: the row survives as an audit record but is invisible.
    assert await repo.get(policy_id, tenant.id) is None
    assert await repo.count(tenant.id) == 0
    assert all(row.id != policy_id for row in await repo.list_active(tenant.id))
    raw = await db.execute(
        OperationalSLAPolicy.__table__.select().where(
            OperationalSLAPolicy.__table__.c.id == policy_id
        )
    )
    rows = list(raw.mappings())
    assert len(rows) == 1 and rows[0]["is_deleted"] is True


# ---------------------------------------------------------------------------
# CRUD -> detector
# ---------------------------------------------------------------------------
async def test_policy_crud_changes_what_the_resolver_returns(tenant, db):
    """The point of CRUD: edits must change detection, not just a table."""
    site_id, link_ids = await seed_committed_edge(tenant.id)
    repo = OperationalSLAPolicyRepository(db)

    async with client(tenant.id) as api:
        created = await api.post(BASE, json=latency_policy())
        policy_id = uuid.UUID(created.json()["id"])

        active = await repo.list_active(tenant.id)
        resolved = resolve_policies(active, link_id=link_ids[0], site_id=site_id)
        assert resolved[METRIC_LATENCY_MS].id == policy_id

        # A site-scoped override wins for that site's links, and only there.
        override = await api.post(
            BASE,
            json=latency_policy(
                name="MPLS latency (branch)",
                scope=SCOPE_SITE,
                scope_id=str(site_id),
                warning_threshold=40,
                critical_threshold=80,
                clear_threshold=30,
            ),
        )
        assert override.status_code == 201, override.text
        override_id = uuid.UUID(override.json()["id"])

        active = await repo.list_active(tenant.id)
        here = resolve_policies(active, link_id=link_ids[0], site_id=site_id)
        elsewhere = resolve_policies(
            active, link_id=uuid.uuid4(), site_id=uuid.uuid4()
        )
        assert here[METRIC_LATENCY_MS].id == override_id
        assert elsewhere[METRIC_LATENCY_MS].id == policy_id

        # Deleting the override restores the tenant-wide default.
        assert (await api.delete(f"{BASE}/{override_id}")).status_code == 200
        active = await repo.list_active(tenant.id)
        restored = resolve_policies(active, link_id=link_ids[0], site_id=site_id)
        assert restored[METRIC_LATENCY_MS].id == policy_id


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------
class _CapturingBus:
    """Minimal stand-in for the process event bus."""

    def __init__(self) -> None:
        self.events: list = []

    async def publish(self, event) -> None:
        self.events.append(event)


async def test_every_write_emits_an_audit_event(tenant):
    from app.services.events.event_bus import EventType

    previous = get_event_bus()
    bus = _CapturingBus()
    set_event_bus(bus)
    try:
        async with client(tenant.id) as api:
            created = await api.post(BASE, json=latency_policy())
            policy_id = created.json()["id"]
            await api.patch(f"{BASE}/{policy_id}", json={"description": "audited"})
            await api.delete(f"{BASE}/{policy_id}")
        # A refused write is NOT an event: only real changes are audited.
        async with client(tenant.id) as api:
            await api.post(
                BASE,
                json=latency_policy(warning_threshold=None, critical_threshold=None),
            )
    finally:
        set_event_bus(previous)

    types = [event.event_type for event in bus.events]
    assert types == [
        EventType.WAN_SLA_POLICY_CREATED,
        EventType.WAN_SLA_POLICY_UPDATED,
        EventType.WAN_SLA_POLICY_DELETED,
    ]
    assert all(event.tenant_id == tenant.id for event in bus.events)
    assert bus.events[0].payload["policy_id"] == policy_id
    assert bus.events[0].payload["metric"] == METRIC_LATENCY_MS
