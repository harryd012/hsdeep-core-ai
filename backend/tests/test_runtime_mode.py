"""Phase 9A: runtime roles gate background work, and the production
configuration contract fails closed on unsafe values.
"""
from __future__ import annotations

import pytest

import app.main as main
from app.core.config import Settings


async def _drive_lifespan(monkeypatch, mode: str) -> dict[str, int]:
    """Run the REAL lifespan with DB/DI work stubbed, recording what started."""
    calls = {"scheduler": 0, "consumers": 0, "consumers_stopped": 0}
    monkeypatch.setattr(main.settings, "runtime_mode", mode)

    async def _noop_async(*args, **kwargs):
        return None

    monkeypatch.setattr(main, "_reconcile_stale_runs", _noop_async)
    monkeypatch.setattr(main, "init_di", lambda: None)
    monkeypatch.setattr(
        main, "start_scheduler", lambda app: calls.__setitem__("scheduler", 1)
    )
    monkeypatch.setattr(main, "stop_scheduler", lambda app: None)

    async def _start_consumers(app):
        calls["consumers"] = 1

    async def _stop_consumers(app):
        calls["consumers_stopped"] += 1

    monkeypatch.setattr(main, "_start_rca_consumers", _start_consumers)
    monkeypatch.setattr(main, "_stop_rca_consumers", _stop_consumers)

    async with main.lifespan(main.app):
        pass
    return calls


@pytest.mark.parametrize(
    "mode,expect_scheduler,expect_consumers",
    [
        ("api", False, False),
        ("worker", True, True),
        ("scheduler", True, False),
        ("all", True, True),
    ],
)
async def test_runtime_mode_gates_background_work(
    monkeypatch, mode, expect_scheduler, expect_consumers
):
    calls = await _drive_lifespan(monkeypatch, mode)
    assert calls["scheduler"] == (1 if expect_scheduler else 0), (
        f"mode {mode}: scheduler start mismatch"
    )
    assert calls["consumers"] == (1 if expect_consumers else 0), (
        f"mode {mode}: event-consumer start mismatch"
    )
    assert calls["consumers_stopped"] == 1, "shutdown must always stop consumers"


# ---------------------------------------------------------------------------
# Production configuration contract
# ---------------------------------------------------------------------------

def _prod_settings(**overrides) -> Settings:
    base: dict = dict(
        _env_file=None,
        app_env="production",
        force_https=True,
        log_level="INFO",
        cors_origins="https://app.example.com",
        jwt_secret_key="kR7pQ2mVxT9wLz4BdY6nJc1HsF8gAe3UoP5iN0rCqEW",
        secret_encryption_key="Zq3Vt9Lm5Xc8Nb1Hs4Jd7Fg0Pa2Ye6Ru8Ki3Ow5Qm=",
        database_url="postgresql+asyncpg://hsdeep_app:pw@db.prod.internal:5432/hsdeep",
        debug=False,
        runtime_mode="worker",
        log_format="json",
        # Production must choose email explicitly ("log" is refused).
        email_transport="none",
    )
    base.update(overrides)
    return Settings(**base)


def _healthy_env(monkeypatch) -> None:
    """Environment values the contract expects a deployment to set explicitly."""
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("PRTG_DEV_VERIFY_SSL", "true")


def test_valid_production_config_reports_no_problems(monkeypatch):
    _healthy_env(monkeypatch)
    assert _prod_settings().validate_for_production() == []


def test_force_https_is_required(monkeypatch):
    _healthy_env(monkeypatch)
    problems = _prod_settings(force_https=False).validate_for_production()
    assert any("FORCE_HTTPS" in p for p in problems)


def test_log_level_must_be_set_explicitly(monkeypatch):
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.setenv("PRTG_DEV_VERIFY_SSL", "true")
    problems = _prod_settings().validate_for_production()
    assert any("LOG_LEVEL" in p for p in problems)


def test_prtg_tls_verification_is_required(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("PRTG_DEV_VERIFY_SSL", "false")
    problems = _prod_settings().validate_for_production()
    assert any("PRTG_DEV_VERIFY_SSL" in p for p in problems)


def test_shared_jwt_and_encryption_secret_is_rejected(monkeypatch):
    _healthy_env(monkeypatch)
    shared = "Zq3Vt9Lm5Xc8Nb1Hs4Jd7Fg0Pa2Ye6Ru8Ki3Ow5Qm="
    problems = _prod_settings(jwt_secret_key=shared).validate_for_production()
    assert any("JWT_SECRET_KEY must not equal" in p for p in problems)


def test_placeholder_secret_is_rejected(monkeypatch):
    _healthy_env(monkeypatch)
    problems = _prod_settings(
        secret_encryption_key="changeme-generate-a-fernet-key"
    ).validate_for_production()
    assert any("placeholder" in p for p in problems)


def test_localhost_database_is_rejected(monkeypatch):
    _healthy_env(monkeypatch)
    problems = _prod_settings(
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/hsdeep"
    ).validate_for_production()
    assert any("local dev Postgres" in p for p in problems)


def test_debug_in_production_is_rejected(monkeypatch):
    _healthy_env(monkeypatch)
    problems = _prod_settings(debug=True).validate_for_production()
    assert any("DEBUG=true" in p for p in problems)


def test_invalid_runtime_mode_is_refused_in_any_environment():
    settings = Settings(_env_file=None, secret_encryption_key="x", runtime_mode="bogus")
    assert any("RUNTIME_MODE" in p for p in settings.validate_for_production())


def test_invalid_log_format_is_refused_in_any_environment():
    settings = Settings(_env_file=None, secret_encryption_key="x", log_format="xml")
    assert any("LOG_FORMAT" in p for p in settings.validate_for_production())


def test_runtime_defaults_preserve_local_development():
    settings = Settings(_env_file=None, secret_encryption_key="x")
    assert settings.runtime_mode == "all"
    assert settings.log_format == "text"
    assert settings.readiness_require_redis is True
    assert 0 < settings.shutdown_drain_seconds <= 60
