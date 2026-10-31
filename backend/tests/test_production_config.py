"""
Unit tests for Settings.validate_for_production() (app.core.config).

Pure Python, no database/network required — Settings is instantiated
directly with explicit kwargs, which take precedence over any .env file per
pydantic-settings' precedence order (init kwargs > env vars > env file >
field default). This lets us test the production-safety guard added for
PRODUCTION_READINESS.md item P0-2 without touching the real dev .env.

NOTE: verified by static/code review only in this session — I do not have
execution access to this repository's environment, so this file has not
been run through pytest here. Run `pytest tests/test_production_config.py -v`
to confirm before relying on it.
"""
from __future__ import annotations

from app.core.config import Settings


def _base_kwargs(**overrides):
    kwargs = dict(
        app_env="production",
        # Phase 9A production contract (app/core/config.py::validate_for_production):
        # HTTPS enforcement is required, and the runtime role / log format must be
        # recognised values.
        force_https=True,
        log_format="json",
        runtime_mode="worker",
        secret_encryption_key="a-real-looking-32-byte-fernet-key-AAAAAAAAAAAAAAAA=",
        jwt_secret_key="a-completely-different-real-jwt-signing-secret",
        database_url="postgresql+asyncpg://produser:realpassword@prod-db.internal:5432/hsdeep",
        debug=False,
        # Production must choose email explicitly ("log" is refused).
        email_transport="none",
    )
    kwargs.update(overrides)
    return kwargs


def test_non_production_env_skips_all_checks():
    s = Settings(
        app_env="development",
        secret_encryption_key="dev-secret-anything",
        debug=True,
    )
    assert s.validate_for_production() == []


def test_healthy_production_config_has_no_problems(monkeypatch):
    # Phase 9A added two environment-level production requirements: LOG_LEVEL
    # must be set explicitly, and PRTG TLS verification must not be disabled.
    # The assertion below is unchanged - a compliant production config is clean.
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("PRTG_DEV_VERIFY_SSL", "true")
    s = Settings(**_base_kwargs())
    assert s.validate_for_production() == []


def test_missing_jwt_secret_key_is_flagged():
    s = Settings(**_base_kwargs(jwt_secret_key=None))
    problems = s.validate_for_production()
    assert any("JWT_SECRET_KEY is unset" in p for p in problems)


def test_jwt_secret_equal_to_encryption_key_is_flagged():
    shared = "same-value-used-for-both-purposes-0123456789"
    s = Settings(**_base_kwargs(secret_encryption_key=shared, jwt_secret_key=shared))
    problems = s.validate_for_production()
    assert any("must not equal" in p for p in problems)


def test_placeholder_encryption_key_is_flagged():
    s = Settings(**_base_kwargs(secret_encryption_key="changeme-generate-a-fernet-key"))
    problems = s.validate_for_production()
    assert any("placeholder/dev value" in p for p in problems)


def test_dev_database_url_is_flagged():
    s = Settings(**_base_kwargs(
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/hsdeep",
    ))
    problems = s.validate_for_production()
    assert any("local dev Postgres" in p for p in problems)


def test_debug_true_in_production_is_flagged():
    s = Settings(**_base_kwargs(debug=True))
    problems = s.validate_for_production()
    assert any("DEBUG=true" in p for p in problems)


def test_multiple_problems_are_all_reported_not_just_the_first():
    s = Settings(**_base_kwargs(
        jwt_secret_key=None,
        secret_encryption_key="changeme-generate-a-fernet-key",
        debug=True,
    ))
    problems = s.validate_for_production()
    assert len(problems) >= 3
