"""
Focused DiscoveryProfile tests (Phase 9.4 §1, §2, §10 A+B).

A profile is CONFIGURATION ONLY. These tests pin the three properties that
matter operationally:

  * the five declared profiles exist, are deterministic, and are the whole set;
  * every declared profile is inside what a DiscoveryScope can store - protocols
    this platform implements, limits the API schema accepts - so a profile can
    never widen a tenant's authority;
  * composition puts the caller's own values first and applies a profile only
    when one is actually named.

Scope integration is tested against the real database and the real router,
because "a profile composes the EXISTING DiscoveryScope fields" is only
meaningful end to end (and because the point of the feature is that the existing
scope runner executes it, with no second engine).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.auth import create_access_token
from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.discovery_scope import ALLOWED_PROTOCOLS
from app.models.tenant import Tenant
from app.repositories.discovery_scope_repository import DiscoveryScopeRepository
from app.schemas.discovery_scope import DiscoveryScopeCreate
from app.services.discovery.profiles import (
    ALLOWED_PROFILE_FIELDS,
    DEFAULT_PROFILE,
    PROFILES,
    PROFILE_BALANCED,
    PROFILE_BASIC,
    PROFILE_CLOUD,
    PROFILE_DEEP,
    PROFILE_NAMES,
    PROFILE_TOPOLOGY,
    DiscoveryProfile,
    UnknownDiscoveryProfile,
    apply_profile,
    profile_catalog,
    profile_names,
    resolve_profile,
    validate_profile,
)
from app.services.discovery.scope_runner import protocols_for_scope
from app.services.infrastructure_intelligence import vocabulary as V


class TestProfileCatalogue:
    """§10 A - the catalogue is complete, named and deterministic."""

    def test_all_five_profiles_are_declared(self):
        assert set(PROFILES) == {
            PROFILE_BASIC,
            PROFILE_BALANCED,
            PROFILE_DEEP,
            PROFILE_CLOUD,
            PROFILE_TOPOLOGY,
        }
        assert len(PROFILES) == 5

    def test_profile_names_are_deterministic_and_lowercase(self):
        assert profile_names() == PROFILE_NAMES
        assert [p["name"] for p in profile_catalog()] == list(PROFILE_NAMES)
        for name in PROFILE_NAMES:
            assert name == name.lower()
            assert name == name.strip()

    def test_catalogue_is_repeatable(self):
        assert profile_catalog() == profile_catalog()

    def test_default_profile_is_balanced_and_unique(self):
        assert DEFAULT_PROFILE == PROFILE_BALANCED
        assert resolve_profile(None).name == PROFILE_BALANCED
        assert resolve_profile("").name == PROFILE_BALANCED
        defaulted = [p["name"] for p in profile_catalog() if p["is_default"]]
        assert defaulted == [PROFILE_BALANCED]

    def test_every_profile_describes_its_intent(self):
        for entry in profile_catalog():
            assert entry["description"].strip()
            assert entry["metadata"].get("intent")


class TestProfileValidity:
    """§1/§10 A - a profile that could not be stored must not be declared."""

    def test_every_declared_profile_passes_validation(self):
        for name in PROFILE_NAMES:
            validate_profile(PROFILES[name])

    def test_profiles_only_use_implemented_protocols(self):
        for name in PROFILE_NAMES:
            protocols = set(PROFILES[name].allowed_protocols)
            assert protocols, name
            assert protocols <= ALLOWED_PROTOCOLS, name

    def test_profile_limits_are_accepted_by_the_scope_schema(self):
        """The API schema is the authority: it validates every profile's fields."""
        for name in PROFILE_NAMES:
            payload = DiscoveryScopeCreate(
                name="schema-check",
                scope_type="private_cidr",
                target="10.0.0.0/24",
                **PROFILES[name].scope_defaults(),
            )
            assert payload.profile is None

    def test_balanced_limits_match_the_scope_model_defaults(self):
        """The default profile and a default scope can never disagree."""
        fields = DiscoveryScopeCreate.model_fields
        profile = PROFILES[PROFILE_BALANCED]
        assert profile.max_depth == fields["max_depth"].default
        assert profile.max_hosts == fields["max_hosts"].default
        assert profile.max_concurrency == fields["max_concurrency"].default
        assert profile.timeout == fields["timeout"].default

    def test_a_profile_can_never_carry_an_execution_field(self):
        from dataclasses import fields as dataclass_fields

        for name in PROFILE_NAMES:
            declared = {spec.name for spec in dataclass_fields(PROFILES[name])}
            assert declared <= ALLOWED_PROFILE_FIELDS, name
        for forbidden in ("command", "argv", "shell", "script", "executable"):
            assert forbidden not in ALLOWED_PROFILE_FIELDS

    def test_validation_rejects_a_non_configuration_field(self):
        @dataclass(frozen=True)
        class ProfileWithCommand(DiscoveryProfile):
            command: str = ""

        profile = ProfileWithCommand(
            name=PROFILE_BASIC,
            description="smuggles a command",
            allowed_protocols=("icmp",),
            max_depth=1,
            max_hosts=8,
            max_concurrency=1,
            timeout=1.0,
        )
        with pytest.raises(ValueError) as excinfo:
            validate_profile(profile)
        assert "command" in str(excinfo.value)

    def test_validation_rejects_an_unsupported_protocol(self):
        profile = DiscoveryProfile(
            name=PROFILE_BASIC,
            description="names a protocol this platform does not implement",
            allowed_protocols=("icmp", "masscan"),
            max_depth=1,
            max_hosts=8,
            max_concurrency=1,
            timeout=1.0,
        )
        with pytest.raises(ValueError) as excinfo:
            validate_profile(profile)
        assert "masscan" in str(excinfo.value)

    def test_validation_rejects_limits_outside_the_schema(self):
        profile = DiscoveryProfile(
            name=PROFILE_BASIC,
            description="too many hosts",
            allowed_protocols=("icmp",),
            max_depth=1,
            max_hosts=999_999,
            max_concurrency=1,
            timeout=1.0,
        )
        with pytest.raises(ValueError) as excinfo:
            validate_profile(profile)
        assert "max_hosts" in str(excinfo.value)

    def test_validation_rejects_an_empty_protocol_set(self):
        profile = DiscoveryProfile(
            name=PROFILE_BASIC,
            description="discovers nothing",
            allowed_protocols=(),
            max_depth=1,
            max_hosts=8,
            max_concurrency=1,
            timeout=1.0,
        )
        with pytest.raises(ValueError):
            validate_profile(profile)


class TestProfileResolution:
    """§2 - one deterministic resolver; unknown names fail safely."""

    def test_unknown_profile_raises_and_lists_the_valid_names(self):
        with pytest.raises(UnknownDiscoveryProfile) as excinfo:
            resolve_profile("aggressive")
        message = str(excinfo.value)
        assert "aggressive" in message
        for name in PROFILE_NAMES:
            assert name in message

    def test_unknown_profile_is_a_value_error(self):
        assert issubclass(UnknownDiscoveryProfile, ValueError)

    def test_matching_is_exact_so_one_string_has_one_answer(self):
        assert resolve_profile(" balanced ").name == PROFILE_BALANCED
        with pytest.raises(UnknownDiscoveryProfile):
            resolve_profile("BALANCED")

    def test_resolution_is_stable(self):
        assert resolve_profile("deep") is resolve_profile("deep")

    def test_resolution_never_falls_back_to_a_broader_profile(self):
        """A typo must not silently upgrade to a deeper scan."""
        for name in ("Basci", "basicc", "", " "):
            if name in ("", " "):
                continue
            with pytest.raises(UnknownDiscoveryProfile):
                resolve_profile(name)


class TestProfileComposition:
    """§2 - the caller's own values always win; no profile means no change."""

    def test_no_profile_leaves_the_fields_untouched(self):
        fields = {"allowed_protocols": [], "max_depth": 4}
        composed = apply_profile(None, fields)
        assert composed == fields
        assert composed is not fields

    def test_profile_fills_only_the_unstated_fields(self):
        composed = apply_profile("basic", {"max_depth": 2}, explicit={"max_depth"})
        assert composed["max_depth"] == 2
        assert composed["allowed_protocols"] == ["icmp"]
        assert composed["max_hosts"] == PROFILES[PROFILE_BASIC].max_hosts

    def test_explicit_protocols_win_over_the_profile(self):
        composed = apply_profile(
            "cloud", {"allowed_protocols": ["icmp"]}, explicit={"allowed_protocols"}
        )
        assert composed["allowed_protocols"] == ["icmp"]
        assert composed["max_depth"] == PROFILES[PROFILE_CLOUD].max_depth

    def test_composed_protocols_are_lists_so_they_can_be_persisted(self):
        composed = apply_profile("balanced", {})
        assert isinstance(composed["allowed_protocols"], list)
        assert isinstance(composed["excluded_targets"], list)

    def test_unknown_profile_in_composition_raises(self):
        with pytest.raises(UnknownDiscoveryProfile):
            apply_profile("nonexistent", {})

    def test_scope_defaults_do_not_alias_the_profile(self):
        composed = apply_profile("balanced", {})
        composed["allowed_protocols"].append("winrm")
        assert "winrm" not in PROFILES[PROFILE_BALANCED].allowed_protocols


class TestProfileEvidenceIntent:
    """§3/§4 - a profile's protocol set determines the evidence it can produce."""

    def test_basic_profile_yields_active_evidence_only(self):
        assert PROFILES[PROFILE_BASIC].evidence_classifications == (V.EVIDENCE_ACTIVE,)

    def test_cloud_profile_yields_api_evidence_only(self):
        assert PROFILES[PROFILE_CLOUD].evidence_classifications == (V.EVIDENCE_API,)

    def test_topology_profile_is_active_because_it_probes(self):
        assert PROFILES[PROFILE_TOPOLOGY].evidence_classifications == (
            V.EVIDENCE_ACTIVE,
        )

    def test_every_profile_protocol_classifies(self):
        for name in PROFILE_NAMES:
            profile = PROFILES[name]
            assert profile.evidence_classifications, name
            for classification in profile.evidence_classifications:
                assert classification in V.EVIDENCE_CLASSIFICATIONS


# ---------------------------------------------------------------------------
# §10 B - Scope integration: real router, real database, EXISTING scope runner.
# ---------------------------------------------------------------------------
SCOPES_URL = "/api/infrastructure/discovery/scopes"
PROFILES_URL = "/api/infrastructure/discovery/profiles"


def _make_token(tenant_id: uuid.UUID) -> str:
    """Short-lived JWT pinned to ``tenant_id`` (superuser: bypasses RBAC)."""
    return create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        email="phase94@example.com",
        is_superuser=True,
        permissions=frozenset(),
    )


def _auth_headers(tenant_id: uuid.UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {_make_token(tenant_id)}"}


async def _ensure_tenant(tenant_id: uuid.UUID) -> None:
    async with AsyncSessionLocal() as db:
        if await db.get(Tenant, tenant_id) is None:
            db.add(Tenant(
                id=tenant_id,
                name=f"p94-{tenant_id.hex[:8]}",
                slug=f"p94-{tenant_id.hex[:8]}",
            ))
            await db.commit()


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


async def _create_scope(client: AsyncClient, tenant: Tenant, **body) -> dict:
    payload = {
        "name": f"p94-{uuid.uuid4().hex[:8]}",
        "scope_type": "private_cidr",
        "target": "10.20.30.0/24",
    }
    payload.update(body)
    response = await client.post(
        SCOPES_URL, headers=_auth_headers(tenant.id), json=payload
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_scope_created_with_a_profile_is_used_by_the_existing_runner(
    client, tenant, seeded_subscription
):
    """Profiles compose EXISTING scope fields - so the existing runner drives it."""
    created = await _create_scope(client, tenant, profile="topology")
    profile = PROFILES[PROFILE_TOPOLOGY]
    assert created["allowed_protocols"] == list(profile.allowed_protocols)
    assert created["max_depth"] == profile.max_depth
    assert created["max_hosts"] == profile.max_hosts
    assert created["max_concurrency"] == profile.max_concurrency
    assert created["timeout"] == profile.timeout

    async with AsyncSessionLocal() as db:
        scope = await DiscoveryScopeRepository(db).get(
            uuid.UUID(created["id"]), tenant.id
        )
    assert scope is not None
    # The EXISTING scope runner derives its TCP probe map from that policy.
    assert protocols_for_scope(scope) == {"ssh": 22}


@pytest.mark.asyncio
async def test_an_icmp_only_profile_opens_no_tcp_socket(
    client, tenant, seeded_subscription
):
    created = await _create_scope(client, tenant, profile="basic")
    assert created["allowed_protocols"] == ["icmp"]
    async with AsyncSessionLocal() as db:
        scope = await DiscoveryScopeRepository(db).get(
            uuid.UUID(created["id"]), tenant.id
        )
    assert protocols_for_scope(scope) == {}


@pytest.mark.asyncio
async def test_explicit_request_values_win_over_the_profile(
    client, tenant, seeded_subscription
):
    created = await _create_scope(
        client,
        tenant,
        profile="basic",
        allowed_protocols=["icmp", "snmp"],
        max_hosts=7,
    )
    assert created["allowed_protocols"] == ["icmp", "snmp"]
    assert created["max_hosts"] == 7
    # The profile still supplied what the request did not state.
    assert created["max_depth"] == PROFILES[PROFILE_BASIC].max_depth
    assert created["timeout"] == PROFILES[PROFILE_BASIC].timeout


@pytest.mark.asyncio
async def test_no_profile_leaves_todays_defaults_untouched(
    client, tenant, seeded_subscription
):
    created = await _create_scope(client, tenant)
    assert created["allowed_protocols"] == []
    assert created["max_depth"] == 1
    assert created["max_hosts"] == 256
    assert created["max_concurrency"] == 32
    assert created["timeout"] == 1.0


@pytest.mark.asyncio
async def test_unknown_profile_is_rejected_with_the_valid_names(
    client, tenant, seeded_subscription
):
    response = await client.post(
        SCOPES_URL,
        headers=_auth_headers(tenant.id),
        json={
            "name": f"p94-{uuid.uuid4().hex[:8]}",
            "scope_type": "private_cidr",
            "target": "10.20.30.0/24",
            "profile": "aggressive",
        },
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    for name in PROFILE_NAMES:
        assert name in detail


@pytest.mark.asyncio
async def test_re_profiling_an_existing_scope(client, tenant, seeded_subscription):
    created = await _create_scope(client, tenant, profile="basic")
    response = await client.patch(
        f"{SCOPES_URL}/{created['id']}",
        headers=_auth_headers(tenant.id),
        json={"profile": "deep"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["allowed_protocols"] == list(PROFILES[PROFILE_DEEP].allowed_protocols)
    assert body["max_hosts"] == PROFILES[PROFILE_DEEP].max_hosts


@pytest.mark.asyncio
async def test_profile_application_cannot_cross_tenants(
    client, tenant, seeded_subscription
):
    created = await _create_scope(client, tenant, profile="basic")
    other = uuid.uuid4()
    await _ensure_tenant(other)
    response = await client.patch(
        f"{SCOPES_URL}/{created['id']}",
        headers=_auth_headers(other),
        json={"profile": "deep"},
    )
    assert response.status_code == 404
    async with AsyncSessionLocal() as db:
        scope = await DiscoveryScopeRepository(db).get(
            uuid.UUID(created["id"]), tenant.id
        )
    assert scope is not None
    assert scope.allowed_protocols == ["icmp"]


@pytest.mark.asyncio
async def test_profiles_endpoint_lists_every_profile(client, tenant):
    response = await client.get(PROFILES_URL, headers=_auth_headers(tenant.id))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["default_profile"] == DEFAULT_PROFILE
    assert [p["name"] for p in body["profiles"]] == list(PROFILE_NAMES)
    for entry in body["profiles"]:
        assert entry["allowed_protocols"]
        assert entry["evidence_classifications"]


@pytest.mark.asyncio
async def test_single_profile_endpoint(client, tenant):
    found = await client.get(f"{PROFILES_URL}/deep", headers=_auth_headers(tenant.id))
    assert found.status_code == 200
    assert found.json()["name"] == PROFILE_DEEP

    missing = await client.get(
        f"{PROFILES_URL}/aggressive", headers=_auth_headers(tenant.id)
    )
    assert missing.status_code == 404
    assert "aggressive" in missing.json()["detail"]


@pytest.mark.asyncio
async def test_profiles_require_authentication(client):
    """Configuration is readable only by an authenticated principal."""
    assert (await client.get(PROFILES_URL)).status_code in (401, 403)
