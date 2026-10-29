"""Managed Postgres URLs (Render, Heroku, Railway) are rewritten to asyncpg."""
from app.core.config import Settings, normalize_database_url


def test_bare_schemes_get_the_asyncpg_driver():
    assert normalize_database_url("postgres://u:p@h:5432/db") == "postgresql+asyncpg://u:p@h:5432/db"
    assert normalize_database_url("postgresql://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"


def test_urls_naming_a_driver_are_unchanged():
    for url in ("postgresql+asyncpg://u:p@h/db", "postgresql+psycopg://u:p@h/db", "sqlite:///x.db"):
        assert normalize_database_url(url) == url
    assert normalize_database_url(None) is None


def test_settings_normalizes_both_urls(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@db.internal/hsdeep")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://t:t@db.internal/hsdeep_test")
    settings = Settings(_env_file=None, secret_encryption_key="test-only-key")
    assert settings.database_url == "postgresql+asyncpg://u:p@db.internal/hsdeep"
    assert settings.test_database_url == "postgresql+asyncpg://t:t@db.internal/hsdeep_test"
