"""
Tenant custom-property definition tests (Phase 6.3E §11).

Real DB, real signed tokens; authorization and tenant isolation are never mocked.

Everything is created under a throwaway tenant and deleted afterwards, so the
suite never touches existing tenant data.
"""
from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import delete, text

from app.core.auth import create_access_token
from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.tenant import Tenant
from app.models.tenant_custom_property import TenantCustomProperty
from app.models.user import User

pytestmark = pytest.mark.asyncio

BASE = "http://test"


async def _make_tenant() -> tuple[Tenant, User]:
    async with AsyncSessionLocal() as db:
        tenant = Tenant(name="CustomProp Test", slug=f"cp-{uuid.uuid4().hex[:8]}")
        db.add(tenant)
        await db.commit()
        await db.refresh(tenant)
        user = User(
            tenant_id=tenant.id,
            email=f"cp-{uuid.uuid4().hex[:8]}@example.com",
            password_hash="test-hash",
            full_name="CustomProp Actor",
            is_active=True,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
    return tenant, user


async def _drop(tenant_id: uuid.UUID) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(
            TenantCustomProperty.__table__.delete().where(
                TenantCustomProperty.tenant_id == tenant_id
            )
        )
        await db.execute(User.__table__.delete().where(User.tenant_id == tenant_id))
        await db.execute(Tenant.__table__.delete().where(Tenant.id == tenant_id))
        await db.commit()


def _token(user: User, *, superuser: bool = True) -> str:
    return create_access_token(
        user_id=user.id,
        tenant_id=user.tenant_id,
        email=user.email,
        is_superuser=superuser,
        permissions=frozenset({"devices.write"}) if superuser else frozenset(),
    )


async def _req(method: str, path: str, token: str, body: dict | None = None):
    async with httpx.AsyncClient(app=app, base_url=BASE) as client:
        return await client.request(
            method, path, headers={"Authorization": f"Bearer {token}"}, json=body
        )


def _props_path(tenant_id: uuid.UUID) -> str:
    return f"/api/admin/tenants/{tenant_id}/custom-properties"


def _valid(**overrides) -> dict:
    payload = {
        "key": "account_manager",
        "display_name": "Customer Success Manager",
        "description": "Owner of the account",
        "data_type": "string",
        "required": False,
        "default_value": None,
        "allowed_values": [],
        "validation_rules": {},
        "is_active": True,
    }
    payload.update(overrides)
    return payload


class TestLifecycle:
    async def test_create_and_retrieve(self):
        tenant, admin = await _make_tenant()
        try:
            token = _token(admin)
            created = await _req("POST", _props_path(tenant.id), token, _valid())
            assert created.status_code == 201, created.text
            body = created.json()
            assert body["key"] == "account_manager"
            assert body["display_name"] == "Customer Success Manager"
            assert body["data_type"] == "string"
            assert body["tenant_id"] == str(tenant.id)
            assert body["created_by"] == str(admin.id)
            assert body["is_active"] is True
            # Canonical response contract: timestamps present, and the field is
            # named validation_rules (never `validation`).
            assert "validation_rules" in body
            assert "validation" not in body
            assert body["created_at"] and body["updated_at"]

            listed = await _req("GET", _props_path(tenant.id), token)
            assert listed.status_code == 200
            assert [p["key"] for p in listed.json()] == ["account_manager"]
        finally:
            await _drop(tenant.id)

    async def test_update_display_name_only(self):
        """The label may change without touching the canonical key."""
        tenant, admin = await _make_tenant()
        try:
            token = _token(admin)
            created = (await _req("POST", _props_path(tenant.id), token, _valid())).json()
            resp = await _req(
                "PATCH",
                f"{_props_path(tenant.id)}/{created['id']}",
                token,
                {"display_name": "Regional Owner"},
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["display_name"] == "Regional Owner"
            # Canonical key and type are immutable.
            assert body["key"] == "account_manager"
            assert body["data_type"] == "string"
        finally:
            await _drop(tenant.id)

    async def test_deactivate_removes_from_list_but_preserves_row(self):
        tenant, admin = await _make_tenant()
        try:
            token = _token(admin)
            created = (await _req("POST", _props_path(tenant.id), token, _valid())).json()
            resp = await _req(
                "DELETE", f"{_props_path(tenant.id)}/{created['id']}", token
            )
            assert resp.status_code == 204, resp.text
            listed = await _req("GET", _props_path(tenant.id), token)
            assert listed.json() == []
            async with AsyncSessionLocal() as db:
                row = (
                    await db.execute(
                        text(
                            "SELECT is_deleted, is_active, deleted_at "
                            "FROM tenant_custom_properties WHERE id = :id"
                        ),
                        {"id": created["id"]},
                    )
                ).one()
            # Canonical soft delete: is_deleted + is_active + deleted_at all set.
            assert row[0] is True and row[1] is False and row[2] is not None
        finally:
            await _drop(tenant.id)

    async def test_audit_entries_written_for_mutations(self):
        tenant, admin = await _make_tenant()
        try:
            token = _token(admin)
            created = (await _req("POST", _props_path(tenant.id), token, _valid())).json()
            await _req(
                "PATCH",
                f"{_props_path(tenant.id)}/{created['id']}",
                token,
                {"display_name": "Renamed"},
            )
            async with AsyncSessionLocal() as db:
                actions = [
                    r[0]
                    for r in (
                        await db.execute(
                            text(
                                "SELECT action FROM audit_events WHERE tenant_id = :t "
                                "AND entity_type = 'tenant_custom_property' ORDER BY action"
                            ),
                            {"t": tenant.id},
                        )
                    ).all()
                ]
            assert "custom_property.created" in actions
            assert "custom_property.updated" in actions
        finally:
            await _drop(tenant.id)


class TestValidation:
    async def test_duplicate_key_rejected_within_tenant(self):
        tenant, admin = await _make_tenant()
        try:
            token = _token(admin)
            assert (
                await _req("POST", _props_path(tenant.id), token, _valid())
            ).status_code == 201
            dup = await _req("POST", _props_path(tenant.id), token, _valid())
            assert dup.status_code == 409, dup.text
        finally:
            await _drop(tenant.id)

    async def test_invalid_data_type_rejected(self):
        tenant, admin = await _make_tenant()
        try:
            token = _token(admin)
            resp = await _req(
                "POST", _props_path(tenant.id), token, _valid(data_type="password")
            )
            assert resp.status_code == 422, resp.text
            assert "unsupported data_type" in resp.text
        finally:
            await _drop(tenant.id)

    async def test_invalid_enum_configuration_rejected(self):
        tenant, admin = await _make_tenant()
        try:
            token = _token(admin)
            # enum with no allowed values
            empty = await _req(
                "POST",
                _props_path(tenant.id),
                token,
                _valid(key="tier", data_type="enum", allowed_values=[]),
            )
            assert empty.status_code == 422, empty.text
            # allowed_values on a non-enum type
            wrong = await _req(
                "POST",
                _props_path(tenant.id),
                token,
                _valid(key="cost_center", data_type="string", allowed_values=["a"]),
            )
            assert wrong.status_code == 422, wrong.text
        finally:
            await _drop(tenant.id)

    async def test_required_without_default_rejected(self):
        tenant, admin = await _make_tenant()
        try:
            token = _token(admin)
            resp = await _req(
                "POST",
                _props_path(tenant.id),
                token,
                _valid(required=True, default_value=None),
            )
            assert resp.status_code == 422, resp.text
        finally:
            await _drop(tenant.id)

    async def test_invalid_key_format_rejected(self):
        tenant, admin = await _make_tenant()
        try:
            token = _token(admin)
            for bad_key in ("Account Manager", "1st_field", "drop;table", ""):
                resp = await _req(
                    "POST", _props_path(tenant.id), token, _valid(key=bad_key)
                )
                assert resp.status_code == 422, (bad_key, resp.text)
        finally:
            await _drop(tenant.id)

    async def test_executable_validation_rejected(self):
        """A definition must never carry an expression."""
        tenant, admin = await _make_tenant()
        try:
            token = _token(admin)
            for bad in (
                {"eval": "__import__('os').system('id')"},
                {"expression": "lambda: 1"},
                {"min_length": "10"},
                {"regex": "("},
                {"regex": "(?<=a)b"},
            ):
                resp = await _req(
                    "POST",
                    _props_path(tenant.id),
                    token,
                    _valid(key=f"f_{uuid.uuid4().hex[:6]}", validation_rules=bad),
                )
                assert resp.status_code == 422, (bad, resp.text)
        finally:
            await _drop(tenant.id)

    async def test_invalid_default_rejected(self):
        """A default that contradicts the type/enum/required flag is refused."""
        tenant, admin = await _make_tenant()
        try:
            token = _token(admin)
            # enum default outside allowed_values
            outside = await _req(
                "POST",
                _props_path(tenant.id),
                token,
                _valid(
                    key="tier",
                    data_type="enum",
                    allowed_values=["bronze", "gold"],
                    default_value="platinum",
                ),
            )
            assert outside.status_code == 422, outside.text
            # required + empty default
            empty = await _req(
                "POST",
                _props_path(tenant.id),
                token,
                _valid(key="region", required=True, default_value=""),
            )
            assert empty.status_code == 422, empty.text
            # a valid enum default is still accepted
            ok = await _req(
                "POST",
                _props_path(tenant.id),
                token,
                _valid(
                    key="tier_ok",
                    data_type="enum",
                    allowed_values=["bronze", "gold"],
                    default_value="bronze",
                ),
            )
            assert ok.status_code == 201, ok.text
        finally:
            await _drop(tenant.id)


class TestIsolationAndAuth:
    async def test_get_returns_only_own_properties(self):
        """Each tenant sees exactly its own definitions - never a leakage."""
        tenant_a, admin_a = await _make_tenant()
        tenant_b, admin_b = await _make_tenant()
        try:
            token_a, token_b = _token(admin_a), _token(admin_b)
            await _req(
                "POST",
                _props_path(tenant_a.id),
                token_a,
                _valid(key="only_for_a"),
            )
            await _req(
                "POST",
                _props_path(tenant_b.id),
                token_b,
                _valid(key="only_for_b"),
            )
            keys_a = [
                p["key"] for p in (await _req("GET", _props_path(tenant_a.id), token_a)).json()
            ]
            keys_b = [
                p["key"] for p in (await _req("GET", _props_path(tenant_b.id), token_b)).json()
            ]
            assert keys_a == ["only_for_a"]
            assert keys_b == ["only_for_b"]
        finally:
            await _drop(tenant_a.id)
            await _drop(tenant_b.id)

    async def test_no_credential_fields_exposed(self):
        """A definition response must never carry secret-bearing fields."""
        tenant, admin = await _make_tenant()
        try:
            token = _token(admin)
            body = (await _req("POST", _props_path(tenant.id), token, _valid())).json()
            listed = (await _req("GET", _props_path(tenant.id), token)).json()[0]
            forbidden = {
                "password",
                "password_hash",
                "hashed_password",
                "secret",
                "token",
                "access_token",
                "api_key",
                "private_key",
                "credential",
                "credentials",
            }
            for payload in (body, listed):
                assert not (set(payload) & forbidden), set(payload) & forbidden
            # Types offered must never include a secret-bearing type either.
            from app.models.tenant_custom_property import DATA_TYPES

            assert not (set(DATA_TYPES) & forbidden), set(DATA_TYPES) & forbidden
        finally:
            await _drop(tenant.id)

    async def test_same_key_allowed_in_different_tenants(self):
        tenant_a, admin_a = await _make_tenant()
        tenant_b, admin_b = await _make_tenant()
        try:
            token_a, token_b = _token(admin_a), _token(admin_b)
            assert (
                await _req("POST", _props_path(tenant_a.id), token_a, _valid())
            ).status_code == 201
            # The same key is legitimate in another tenant - no global namespace.
            assert (
                await _req("POST", _props_path(tenant_b.id), token_b, _valid())
            ).status_code == 201
        finally:
            await _drop(tenant_a.id)
            await _drop(tenant_b.id)

    async def test_cross_tenant_read_and_mutation_rejected(self):
        """tenant A's property id must be unusable through tenant B's path."""
        tenant_a, admin_a = await _make_tenant()
        tenant_b, admin_b = await _make_tenant()
        try:
            token = _token(admin_a)
            created = (await _req("POST", _props_path(tenant_a.id), token, _valid())).json()

            # tenant B lists nothing of tenant A's
            listed_b = await _req("GET", _props_path(tenant_b.id), token)
            assert listed_b.status_code == 200
            assert listed_b.json() == []

            # A's property cannot be mutated through B's path
            assert (
                await _req(
                    "PATCH",
                    f"{_props_path(tenant_b.id)}/{created['id']}",
                    token,
                    {"display_name": "hijacked"},
                )
            ).status_code == 404
            assert (
                await _req(
                    "DELETE", f"{_props_path(tenant_b.id)}/{created['id']}", token
                )
            ).status_code == 404

            # ...and A's definition is unchanged
            listed_a = await _req("GET", _props_path(tenant_a.id), token)
            assert listed_a.json()[0]["display_name"] == "Customer Success Manager"
        finally:
            await _drop(tenant_a.id)
            await _drop(tenant_b.id)

    async def test_non_superuser_is_forbidden(self):
        tenant, user = await _make_tenant()
        try:
            mortal = _token(user, superuser=False)
            assert (await _req("GET", _props_path(tenant.id), mortal)).status_code == 403
            assert (
                await _req("POST", _props_path(tenant.id), mortal, _valid())
            ).status_code == 403
        finally:
            await _drop(tenant.id)

    async def test_unknown_tenant_is_404(self):
        tenant, admin = await _make_tenant()
        try:
            token = _token(admin)
            unknown = uuid.uuid4()
            assert (await _req("GET", _props_path(unknown), token)).status_code == 404
            assert (
                await _req("POST", _props_path(unknown), token, _valid())
            ).status_code == 404
        finally:
            await _drop(tenant.id)


class TestModelIntegrity:
    async def test_table_has_expected_shape(self):
        """Guards the migration: columns, unique key and the type CHECK."""
        async with AsyncSessionLocal() as db:
            columns = {
                r[0]
                for r in (
                    await db.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name = 'tenant_custom_properties'"
                        )
                    )
                ).all()
            }
            for expected in (
                "id",
                "tenant_id",
                "key",
                "display_name",
                "description",
                "data_type",
                "required",
                "default_value",
                "allowed_values",
                "validation_rules",
                "is_active",
                "created_by",
                "created_at",
                "updated_at",
            ):
                assert expected in columns, expected

            constraints = {
                r[0]
                for r in (
                    await db.execute(
                        text(
                            "SELECT conname FROM pg_constraint "
                            "WHERE conrelid = 'tenant_custom_properties'::regclass"
                        )
                    )
                ).all()
            }
            assert "uq_tenant_custom_properties_tenant_id_key" in constraints
            assert "ck_tenant_custom_properties_data_type" in constraints
            assert "fk_tenant_custom_properties_tenant_id_tenants" in constraints

    async def test_check_constraint_blocks_unknown_type(self):
        """Even a raw insert cannot introduce an unsupported data type."""
        tenant, _admin = await _make_tenant()
        try:
            async with AsyncSessionLocal() as db:
                with pytest.raises(Exception):
                    await db.execute(
                        text(
                            "INSERT INTO tenant_custom_properties "
                            "(id, tenant_id, key, display_name, data_type, required, "
                            " allowed_values, validation_rules, is_active, is_deleted, "
                            " created_at, updated_at) VALUES "
                            "(:id, :t, 'bad_type', 'Bad', 'password', false, '[]'::jsonb, "
                            " '{}'::jsonb, true, false, now(), now())"
                        ),
                        {"id": uuid.uuid4(), "t": tenant.id},
                    )
                    await db.commit()
                await db.rollback()
        finally:
            await _drop(tenant.id)
