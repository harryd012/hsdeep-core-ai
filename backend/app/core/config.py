"""
Application configuration loaded from environment variables (.env supported).
"""
from functools import lru_cache
from typing import ClassVar
import os

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: URL schemes managed Postgres providers hand out (Render, Heroku, Railway...).
#: The app runs on SQLAlchemy's async engine, which needs the asyncpg driver
#: named explicitly; a bare scheme would select psycopg2, which is not installed.
_SYNC_POSTGRES_SCHEMES = ("postgres://", "postgresql://")


def normalize_database_url(url: str | None) -> str | None:
    """``postgres://`` / ``postgresql://`` -> ``postgresql+asyncpg://``.

    URLs that already name a driver are returned unchanged.
    """
    if not url:
        return url
    for scheme in _SYNC_POSTGRES_SCHEMES:
        if url.startswith(scheme):
            return "postgresql+asyncpg://" + url[len(scheme):]
    return url


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
    # Prometheus /metrics. When set, scrapers must send
    # `Authorization: Bearer <token>`. When unset, /metrics is open outside
    # production and answers 404 in production (not published by default).
    metrics_bearer_token: str = ""
    # POST /api/billing/signup. Off for an invite-only pilot: tenants are then
    # created by an operator (scripts/seed_admin.py), never by the public.
    public_signup_enabled: bool = True
    # Per-account lockout: this many consecutive failed logins lock the
    # account for login_lockout_minutes (0 attempts disables the lockout).
    login_max_failed_attempts: int = 5
    login_lockout_minutes: int = 15
    # Automated remediation is only "resolved" after fresh telemetry confirms
    # recovery (app/services/events/recovery_validator.py): this many
    # consecutive recovered checks, re-checked every interval, at most
    # `attempts` times before escalating.
    recovery_validation_attempts: int = 10
    recovery_validation_interval_seconds: float = 60.0
    recovery_validation_consecutive_passes: int = 2
    # Outbound email (app/services/email). "smtp" sends through SMTP_*;
    # "log" logs the message metadata instead of sending (development only,
    # refused in production because reset links would silently vanish);
    # "none" disables email: password reset and invitations then answer 503.
    email_transport: str = "log"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = True
    email_from: str = ""
    # Public web origin used to build links in emails (never the request Host).
    app_public_url: str = "http://localhost:3000"

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/hsdeep"

    # Tests: when set, the DB engine and test fixtures bind to THIS database
    # instead of `database_url`, so DB-writing tests never mutate the dev DB.
    # The test suite refuses to run (see tests/conftest.py) when this is unset.
    test_database_url: str | None = None

    @field_validator("database_url", "test_database_url", mode="before")
    @classmethod
    def _asyncpg_driver(cls, value):
        return normalize_database_url(value) if isinstance(value, str) else value

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
    # How long one process trusts a positive "session is live" check. Bounds
    # how long a revoked session's access token can still be used on another
    # process. 0 checks the database on every request.
    session_check_cache_seconds: int = 10
    # Break-glass support access (R1.2): longest grant a superuser may open
    # on another tenant, and the default when none is requested.
    support_grant_max_minutes: int = 480
    support_grant_default_minutes: int = 60
    # Master switch for the phased auth cutover. While False (default), the
    # `require_permission`/`get_current_user` dependencies still authenticate
    # a presented token but routes that have NOT yet been migrated keep their
    # interim tenant_id query-param contract. Flip to True only once every
    # route derives tenancy from the principal. See core/auth.py.
    auth_enforced: bool = True

    # Collectors / Scheduler
    max_concurrent_collections: int = 10
    collector_run_stale_minutes: int = 10
    # L3 topology: dynamic (BGP/OSPF/other) routes kept per device per poll.
    # Connected and static routes are always kept; a full BGP table is not.
    l3_max_dynamic_routes_per_device: int = 500
    # Firewall gateway alert thresholds (percent, and days before licence
    # expiry). Applied to what the device itself reports; a value the device
    # did not report never alerts.
    firewall_session_warning_pct: float = 80.0
    firewall_session_critical_pct: float = 95.0
    firewall_resource_warning_pct: float = 85.0
    firewall_resource_critical_pct: float = 95.0
    firewall_licence_warning_days: int = 30
    # Backup monitoring: a protected item with no successful backup for longer
    # than this recovery-point objective alerts (hours). Job history read per
    # poll covers this many hours back.
    backup_rpo_hours: int = 24
    backup_job_lookback_hours: int = 72

    # Correlation: how long an open incident may be quiet before a new alert
    # starts a fresh incident instead of reopening it. Replaces the hardcoded
    # 30-minute CORRELATION_WINDOW in CorrelationEngine; kept configurable so
    # operators can match their poll cadence.
    correlation_inactivity_minutes: int = 30

    # Incident lifecycle: an open/acknowledged incident that receives no new
    # alert for this long is auto-closed by the stale-close job. Also how often
    # that job runs.
    incident_stale_minutes: int = 1440  # 24h
    incident_stale_close_interval_seconds: int = 3600  # hourly

    # Source health state machine (SourceHealthManager). These drive when a
    # failing MonitoringSource transitions HEALTHY -> DEGRADED and when a
    # recovering one transitions back, plus the exponential backoff used to
    # schedule the next attempt after a failure.
    health_degraded_threshold: int = 3
    health_recovery_successes: int = 2
    retry_max_attempts: int = 3
    retry_backoff_base_seconds: float = 30.0
    retry_max_delay_seconds: float = 300.0
    retry_jitter: bool = True

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

    # Per-user fair-use cap for session-JWT (web UI) requests, per minute
    # (app/main.py _RateLimiter). Session traffic is never metered against the
    # plan's API allowance, so this is what stops scripting against a session
    # token. Over the cap -> 429 + Retry-After. Nonpositive -> built-in 600.
    session_rpm_per_user: int = 600

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

    # -- Human-controlled agent orchestration (Phase 9.12) -------------------
    # The default production behaviour is that AGENTS DO NOTHING ON THEIR OWN:
    # a human creates a task and a human assigns it. This flag is the single,
    # explicit, administrator-controlled opt-in that allows the scheduler to
    # auto-route queued tasks to an available agent. It is OFF unless an operator
    # deliberately turns it on, so the historical "created -> assigned ->
    # system" behaviour is no longer an implicit default.
    #
    # OFF (default): tasks stay in `created` (unassigned) until a human assigns
    # them, and the dispatcher reports that honestly instead of routing.
    autonomous_task_assignment: bool = False

    # -- PNETLab topology provider (Phase 9.7B) ------------------------------
    # Externalised configuration for the PNETLab topology provider. A PNETLab
    # lab is the authoritative source of its own topology, so the provider reads
    # it over PNETLab's native JSON API instead of inferring anything.
    #
    # SECRETS: `pnetlab_password` is read from the environment only (never
    # committed, never logged, never returned by an API). Empty means "not
    # configured", and the provider then reports configuration-missing rather
    # than pretending to have collected a topology.
    pnetlab_base_url: str = ""
    pnetlab_username: str = ""
    pnetlab_password: str = ""
    #: Optional lab path ("/lab.unl"). Empty = discover every lab the account
    #: can see; never a hard-coded lab id (Phase 9.7B step 3).
    pnetlab_lab_path: str = ""
    #: PNETLab commonly serves a self-signed certificate on the lab host.
    pnetlab_verify_ssl: bool = False
    pnetlab_timeout_seconds: float = 15.0

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
        if self.email_transport == "log":
            problems.append(
                "EMAIL_TRANSPORT=log only logs messages; in production password "
                "reset and invitation links would never arrive. Use smtp, or "
                "none to switch email off."
            )
        if self.email_transport == "smtp" and not (self.smtp_host and self.email_from):
            problems.append("EMAIL_TRANSPORT=smtp needs SMTP_HOST and EMAIL_FROM.")

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
