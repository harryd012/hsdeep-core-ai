"""
Task 16.1b: Idempotency unit test for scripts/seed_dev_prtg_source.py.

Invokes seed_dev_prtg_source() twice with the same PRTG_DEV_* values and asserts:
  1. Exactly one MonitoringSource row exists after both runs.
  2. The stored password_encrypted round-trips via SecretCrypto back to the
     configured PRTG_DEV_PASSHASH (credential encryption correctness).

Uses a real PostgreSQL DB (same convention as the rest of the suite). The test
is scoped to the per-test `tenant` fixture so teardown cascades to clean up
any MonitoringSource rows created here.
"""
from __future__ import annotations

import os
import uuid

import pytest

from app.core.db import AsyncSessionLocal
from app.core.security import SecretCrypto
from app.repositories.monitoring_source_repository import MonitoringSourceRepository
from scripts.seed_dev_prtg_source import seed_dev_prtg_source

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Fake PRTG_DEV_* values used throughout this test
# (never the real credentials — these are test fixtures only)
# ---------------------------------------------------------------------------
_FAKE_HOST = f"prtg-test-{uuid.uuid4().hex[:8]}.internal"
_FAKE_USERNAME = "testadmin"
_FAKE_PASSHASH = f"fake-passhash-{uuid.uuid4().hex[:12]}"
_FAKE_PORT = "9443"
_FAKE_VERIFY_SSL = "false"
_FAKE_POLL_INTERVAL = "120"


def _set_fake_env(monkeypatch) -> None:
    monkeypatch.setenv("PRTG_DEV_HOST", _FAKE_HOST)
    monkeypatch.setenv("PRTG_DEV_USERNAME", _FAKE_USERNAME)
    monkeypatch.setenv("PRTG_DEV_PASSHASH", _FAKE_PASSHASH)
    monkeypatch.setenv("PRTG_DEV_PORT", _FAKE_PORT)
    monkeypatch.setenv("PRTG_DEV_VERIFY_SSL", _FAKE_VERIFY_SSL)
    monkeypatch.setenv("PRTG_DEV_POLL_INTERVAL_SECONDS", _FAKE_POLL_INTERVAL)


class TestSeedDevPrtgSourceIdempotency:

    async def test_first_run_creates_exactly_one_row(self, tenant, monkeypatch):
        """Single invocation creates one MonitoringSource."""
        _set_fake_env(monkeypatch)

        # Override the seed's tenant lookup to use the test tenant directly
        # by patching the dev-tenant slug lookup to match our test tenant.
        import scripts.seed_dev_prtg_source as seed_mod
        original_slug = seed_mod.DEV_TENANT_SLUG
        monkeypatch.setattr(seed_mod, "DEV_TENANT_SLUG", tenant.slug)

        async with AsyncSessionLocal() as db:
            result = await seed_dev_prtg_source(db)
            await db.commit()

        assert result is not None
        assert result.hostname == _FAKE_HOST
        assert result.source_type == "prtg"

    async def test_second_run_creates_no_duplicate(self, tenant, monkeypatch):
        """Two consecutive invocations leave exactly one row (idempotency)."""
        _set_fake_env(monkeypatch)

        import scripts.seed_dev_prtg_source as seed_mod
        monkeypatch.setattr(seed_mod, "DEV_TENANT_SLUG", tenant.slug)

        # Run 1
        async with AsyncSessionLocal() as db:
            r1 = await seed_dev_prtg_source(db)
            await db.commit()

        # Run 2
        async with AsyncSessionLocal() as db:
            r2 = await seed_dev_prtg_source(db)
            await db.commit()

        # Both runs must reference the same underlying row
        assert r1 is not None and r2 is not None
        assert r1.id == r2.id

        # DB confirms exactly one row
        async with AsyncSessionLocal() as db:
            repo = MonitoringSourceRepository(db)
            prtg_sources = await repo.list(tenant.id, source_type="prtg")
            prtg_for_host = [s for s in prtg_sources if s.hostname == _FAKE_HOST]
        assert len(prtg_for_host) == 1, (
            f"Expected 1 MonitoringSource row, found {len(prtg_for_host)}"
        )

    async def test_password_encrypted_round_trips_to_passhash(self, tenant, monkeypatch):
        """password_encrypted column decrypts back to PRTG_DEV_PASSHASH — confirms
        SecretCrypto encryption happened correctly (Requirement 9.1)."""
        _set_fake_env(monkeypatch)

        import scripts.seed_dev_prtg_source as seed_mod
        monkeypatch.setattr(seed_mod, "DEV_TENANT_SLUG", tenant.slug)

        async with AsyncSessionLocal() as db:
            source = await seed_dev_prtg_source(db)
            await db.commit()
            source_id = source.id

        # Re-fetch from DB to confirm the persisted encrypted value
        async with AsyncSessionLocal() as db:
            repo = MonitoringSourceRepository(db)
            persisted = await repo.get(source_id, tenant.id)

        assert persisted is not None
        assert persisted.password_encrypted is not None, "password_encrypted should not be None"

        crypto = SecretCrypto()
        decrypted = crypto.decrypt(persisted.password_encrypted)
        assert decrypted == _FAKE_PASSHASH, (
            "Decrypted password does not match PRTG_DEV_PASSHASH — "
            "encryption or decryption key mismatch"
        )

    async def test_missing_required_env_var_returns_none(self, tenant, monkeypatch):
        """When a required PRTG_DEV_* var is absent, seed returns None (no-op)."""
        import scripts.seed_dev_prtg_source as seed_mod
        monkeypatch.setattr(seed_mod, "DEV_TENANT_SLUG", tenant.slug)

        # Only set HOST and USERNAME — omit PRTG_DEV_PASSHASH
        monkeypatch.setenv("PRTG_DEV_HOST", _FAKE_HOST)
        monkeypatch.setenv("PRTG_DEV_USERNAME", _FAKE_USERNAME)
        monkeypatch.delenv("PRTG_DEV_PASSHASH", raising=False)

        async with AsyncSessionLocal() as db:
            result = await seed_dev_prtg_source(db)
            await db.commit()

        assert result is None
