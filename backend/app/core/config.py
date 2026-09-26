"""
Application configuration loaded from environment variables (.env supported).
"""
from functools import lru_cache
from typing import ClassVar
import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    app_name: str = "hsdeep-core-ai-backend"
    app_env: str = "development"
    debug: bool = False
    log_level: str = "INFO"

    # HTTP surface (Phase 8B). Both were previously outside Settings: CORS was
    # read with a raw os.getenv in main.py and FORCE_HTTPS was documented in
    # .env.example but never modelled. Production still requires an explicit
    # non-wildcard allow-list (enforced at startup in main.py).
    cors_origins: str = ""  # comma-separated allow-list; "" = dev localhost default
    force_https: bool = False

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/hsdeep"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Security
    secret_encryption_key: str

    # Billing / Stripe webhooks. Empty means "webhook endpoint disabled"
    # (POST /api/stripe/webhook returns 501) - see stripe_webhook.py.
    stripe_webhook_secret: str = ""
    stripe_secret_key: str = ""

    # Auth / RBAC (P1)
    # HS256 single-secret JWTs for now (RS256 deferred to when auth becomes a
    # separately-deployed service - see audit-master-roadmap). jwt_secret_key
    # has no default on purpose in production: if left unset it falls back to
    # secret_encryption_key so a dev box still boots, but see the validator.
    jwt_secret_key: str | None = None
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30
    # Master switch for the phased auth cutover. While False (default), the
    # `require_permission`/`get_current_user` dependencies still authenticate
    # a presented token but routes that have NOT yet been migrated keep their
    # interim tenant_id query-param contract. Flip to True only once every
    # route derives tenancy from the principal. See core/auth.py.
    auth_enforced: bool = True

    # Collectors / Scheduler
    max_concurrent_collections: int = 10
    collector_run_stale_minutes: int = 10

    # --- Phase 9A deployment contract ------------------------------------
    # Runtime role for this process. One image, several roles:
    #   all       - local development: HTTP + scheduler + RCA consumers
    #               (exactly the pre-9A behaviour, so nothing changes locally)
    #   api       - HTTP only: no scheduler jobs, no in-process RCA consumers
    #   worker    - background: scheduler ticks + RCA/EventBus consumers
    #   scheduler - background: scheduler ticks only
    # The scheduler's own Postgres advisory lock (app/scheduler/jobs.py) already
    # guarantees a single owner per tick across replicas; this setting stops API
    # replicas from even attempting it.
    runtime_mode: str = "all"

    # Bounded shutdown drain budget (seconds) for queued background work, so a
    # slow/hung handler can never block a rolling deploy indefinitely.
    shutdown_drain_seconds: float = 15.0

    # Redis counts toward /health/ready by default (pre-9A behaviour:
    # ready = database AND redis). A deployment that deliberately runs without
    # Redis (the API is designed to serve without it) sets this false so pods
    # become ready on the database alone.
    readiness_require_redis: bool = True

    # Log output format: "text" (human-readable, default) or "json" (one JSON
    # object per line, for centralized log shipping).
    log_format: str = "text"

    # Per-source Redis rate limiting (app/services/monitoring/rate_limiter.py).
    # 0 = DISABLED (default) which preserves the pre-9A behaviour of polling
    # every due source. When > 0 the EXISTING SourceRateLimiter enforces a
    # sliding window per source across ALL worker processes, so the same source
    # cannot be polled concurrently by several replicas. The TTL only bounds
    # leftover Redis keys after a crash. Redis failures fail OPEN.
    source_rate_limit_rpm: int = 0
    source_rate_limit_ttl_seconds: int = 120

    # Local LLM (Ollama) — used by the AI Copilot for intent classification
    # and context summarization only. Defaults suit a local dev Ollama
    # instance; override OLLAMA_HOST / OLLAMA_MODEL in the environment or
    # .env (e.g. a remote Ollama box or a different tagged model).
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "qwen3.6:latest"
    # Per-call timeouts (seconds). Intent classification is a tiny JSON task
    # (short); summarization gets more because its context payload can be a
    # few KB even after the Copilot bounds it. Both are deliberately kept
    # BELOW the frontend's 3-minute client deadline so a genuinely slow-but-
    # reachable Ollama produces a controlled backend fallback/error rather
    # than hanging until the browser aborts with "took too long to respond".
    ollama_timeout_seconds: float = 60.0
    ollama_summary_timeout_seconds: float = 150.0

    # Optional per-tier AI model routing for the existing Ollama calls
    # (AI_FAST_MODEL / AI_STANDARD_MODEL / AI_DEEP_MODEL). All are OPTIONAL
    # and default to empty, which means "not configured" — existing
    # installations that set nothing keep using `ollama_model` (the deep /
    # default capability, e.g. qwen3.6:latest) for every request, exactly as
    # before. When AI_FAST_MODEL is configured, simple utility calls
    # (classification, concise summaries) use it and automatically fall back
    # to `ollama_model` if that model is unavailable on the server.
    ai_fast_model: str = ""
    ai_standard_model: str = ""
    ai_deep_model: str = ""
    # Optional Ollama keep_alive for the request (Ollama duration syntax, e.g.
    # "5m", "10m", "-1"). Empty = omit the field entirely so Ollama applies
    # its own default unload policy (5m) — never an aggressive permanent pin.
    ollama_keep_alive: str = ""

    @property
    def effective_jwt_secret(self) -> str:
        """The signing secret actually used for JWTs. Falls back to
        `secret_encryption_key` when `jwt_secret_key` is unset so a dev
        environment needs only one secret configured; production should set
        a distinct `JWT_SECRET_KEY` so rotating token signing never forces a
        re-encryption of stored credentials."""
        return self.jwt_secret_key or self.secret_encryption_key

    #: Valid deployment roles (see `runtime_mode`).
    VALID_RUNTIME_MODES: ClassVar[tuple[str, ...]] = ("all", "api", "worker", "scheduler")
    #: Valid log formats (see `log_format`).
    VALID_LOG_FORMATS: ClassVar[tuple[str, ...]] = ("text", "json")

    def validate_for_production(self) -> list[str]:
        """Return human-readable problems that make this config unsafe to
        run with APP_ENV=production. Empty list = the known footguns below
        are absent (not a full security audit). Called from app.main's
        startup event; a non-empty result raises RuntimeError there rather
        than booting silently-insecure.

        Always-validated (any environment) first: a typo in RUNTIME_MODE would
        otherwise silently start no scheduler at all, or a bad LOG_FORMAT would
        silently fall back to text.
        """
        problems: list[str] = []

        # --- always validated (not production-specific) -------------------
        if self.runtime_mode not in self.VALID_RUNTIME_MODES:
            problems.append(
                f"RUNTIME_MODE={self.runtime_mode!r} is not one of "
                f"{', '.join(self.VALID_RUNTIME_MODES)}. Refusing to guess the "
                "role of this process."
            )
        if self.log_format not in self.VALID_LOG_FORMATS:
            problems.append(
                f"LOG_FORMAT={self.log_format!r} is not one of "
                f"{', '.join(self.VALID_LOG_FORMATS)}."
            )

        if self.app_env != "production":
            return problems

        # --- production-only contract (Phase 9A) --------------------------
        if not self.force_https:
            problems.append(
                "FORCE_HTTPS must be true in production - plaintext requests "
                "would otherwise be served (see app/core/request_logging.py "
                "ForceHttpsMiddleware)."
            )

        if "LOG_LEVEL" not in os.environ:
            problems.append(
                "LOG_LEVEL must be set explicitly in production (the default "
                "'INFO' is a development convenience, not a production "
                "decision)."
            )

        _prtg_tls = os.getenv("PRTG_DEV_VERIFY_SSL")
        if _prtg_tls is not None and _prtg_tls.strip().lower() not in {
            "1",
            "true",
            "yes",
            "on",
        }:
            problems.append(
                "PRTG_DEV_VERIFY_SSL must be true in production - disabling "
                "TLS verification would accept any certificate for PRTG "
                "credentials sent over the network."
            )

        if not self.jwt_secret_key:
            problems.append(
                "JWT_SECRET_KEY is unset - production would silently sign tokens "
                "with SECRET_ENCRYPTION_KEY (a value that also protects stored "
                "vendor credentials). Set a distinct JWT_SECRET_KEY."
            )
        elif self.jwt_secret_key == self.secret_encryption_key:
            problems.append(
                "JWT_SECRET_KEY must not equal SECRET_ENCRYPTION_KEY in production - "
                "a leak of one would expose the other."
            )

        _placeholder_markers = ("changeme", "dev-secret", "your-", "example", "replace-me")
        if any(m in self.secret_encryption_key.lower() for m in _placeholder_markers):
            problems.append(
                "SECRET_ENCRYPTION_KEY looks like a placeholder/dev value. Generate "
                "a real key, e.g.: python -c \"from cryptography.fernet import "
                "Fernet; print(Fernet.generate_key().decode())\""
            )

        if "localhost" in self.database_url or "postgres:postgres@" in self.database_url:
            problems.append(
                "DATABASE_URL still points at a local dev Postgres (localhost "
                "and/or default postgres:postgres credentials). Point production "
                "at a real managed database with a unique password."
            )

        if self.debug:
            problems.append("DEBUG=true in production can leak internals in error responses. Set DEBUG=false.")

        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()
