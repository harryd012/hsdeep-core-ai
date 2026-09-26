"""
Retry types and retry policy engine for ServiceNow integration.

Classifies errors into categories that determine whether they are retryable,
how long to wait before retrying, and whether the retry budget has been exhausted.

Retries are classified as:
- network      : transport timeout or connection refused — always retryable
- authentication : auth error (401) — retry only with token refresh, otherwise fatal
- rate_limit   : HTTP 429 — retry with Retry-After header respect
- server       : HTTP 5xx — retry with exponential backoff + jitter
- client       : HTTP 4xx (not 429) — never retryable
- fatal        : unparseable response, unexpected errors — never retryable

Retry budget: each sync job gets a per-attempt budget. If all retries are exhausted
for this category, the job transitions to dead letter instead of spinning indefinitely.
"""
from __future__ import annotations

import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto

logger = logging.getLogger(__name__)


class RetryCategory(Enum):
    NETWORK = auto()
    AUTHENTICATION = auto()
    RATE_LIMIT = auto()
    SERVER_ERROR = auto()
    CLIENT_ERROR = auto()
    FATAL = auto()


class RetryableResult(Enum):
    RETRY = auto()
    EXHAUSTED = auto()
    SKIPPED = auto()


@dataclass(frozen=True)
class RetryPolicy:
    """Immutable retry policy for a single error classification."""
    category: RetryCategory
    max_retries: int
    base_delay_seconds: float
    max_delay_seconds: float
    jitter_fraction: float = 0.15
    is_retryable: bool = True

    def __post_init__(self) -> None:
        if self.jitter_fraction < 0 or self.jitter_fraction > 1:
            raise ValueError("jitter_fraction must be between 0 and 1")


# Default policies by category — all defined at module level for determinism
DEFAULT_POLICIES = {
    RetryCategory.NETWORK: RetryPolicy(
        category=RetryCategory.NETWORK,
        max_retries=5,
        base_delay_seconds=2.0,
        max_delay_seconds=300.0,
        jitter_fraction=0.15,
    ),
    RetryCategory.AUTHENTICATION: RetryPolicy(
        category=RetryCategory.AUTHENTICATION,
        max_retries=2,  # Refresh token has a limited window — don't retry too aggressively
        base_delay_seconds=5.0,
        max_delay_seconds=60.0,
        jitter_fraction=0.10,
    ),
    RetryCategory.RATE_LIMIT: RetryPolicy(
        category=RetryCategory.RATE_LIMIT,
        max_retries=8,  # ServiceNow rate limits are per-connection; long waits are acceptable
        base_delay_seconds=5.0,
        max_delay_seconds=600.0,
        jitter_fraction=0.15,
    ),
    RetryCategory.SERVER_ERROR: RetryPolicy(
        category=RetryCategory.SERVER_ERROR,
        max_retries=5,
        base_delay_seconds=2.0,
        max_delay_seconds=300.0,
        jitter_fraction=0.15,
    ),
    RetryCategory.CLIENT_ERROR: RetryPolicy(
        category=RetryCategory.CLIENT_ERROR,
        max_retries=0,
        base_delay_seconds=0.0,
        max_delay_seconds=0.0,
        is_retryable=False,
    ),
    RetryCategory.FATAL: RetryPolicy(
        category=RetryCategory.FATAL,
        max_retries=0,
        base_delay_seconds=0.0,
        max_delay_seconds=0.0,
        is_retryable=False,
    ),
}


@dataclass
class RetryState:
    """Mutable state for tracking retry progress within a single job execution."""

    category: RetryCategory
    policy: RetryPolicy
    attempt: int = 0
    total_delay_accumulated_ms: float = 0.0
    started_at_ns: int = field(default=0, repr=False)
    last_error: str | None = None
    _policy_map: dict[RetryCategory, RetryPolicy] = field(
        default_factory=lambda: DEFAULT_POLICIES,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if self.started_at_ns == 0:
            self.started_at_ns = time.perf_counter_ns()

    @classmethod
    def from_error(cls, error: Exception, **kwargs) -> "RetryState":
        """Create a RetryState by classifying the error."""
        category = classify_error(error)
        policy = DEFAULT_POLICIES.get(category, DEFAULT_POLICIES[RetryCategory.FATAL])
        return cls(category=category, policy=policy, **kwargs)

    @property
    def is_exhausted(self) -> bool:
        if not self.policy.is_retryable:
            return True
        return self.attempt >= self.policy.max_retries

    @property
    def has_remaining_attempts(self) -> bool:
        if not self.policy.is_retryable:
            return False
        return self.attempt < self.policy.max_retries

    def should_retry(self) -> RetryableResult:
        """Determine whether to retry based on current state."""
        if self.policy.is_retryable and self.has_remaining_attempts:
            # Check overall budget — don't spend more than 30 minutes total on retries
            elapsed_s = (time.perf_counter_ns() - self.started_at_ns) / 1e9
            if self.total_delay_accumulated_ms + (elapsed_s * 1000) > 1_800_000:
                return RetryableResult.EXHAUSTED
            return RetryableResult.RETRY
        return RetryableResult.EXHAUSTED

    def record_failure(self, error: Exception | None = None) -> RetryableResult:
        """Record a failure and determine next action. Returns RETRY or EXHAUSTED."""
        self.attempt += 1
        self.last_error = str(error) if error else "unknown"

        result = self.should_retry()
        if result == RetryableResult.RETRY:
            delay_s = self._compute_delay()
            logger.info(
                "[retry] category=%s attempt=%d/%d delay=%.1fs",
                self.category.name, self.attempt, self.policy.max_retries, delay_s,
            )
            return result
        else:
            logger.warning(
                "[retry exhausted] category=%s attempts=%d last_error=%s",
                self.category.name, self.attempt, self.last_error,
            )
            return RetryableResult.EXHAUSTED

    def record_success(self) -> None:
        """Record a successful attempt — resets state for next use."""
        logger.info(
            "[retry succeeded] category=%s attempts_used=%d",
            self.category.name, self.attempt,
        )

    def _compute_delay(self) -> float:
        """Exponential backoff with jitter. Delays capped at policy max."""
        delay = min(
            self.policy.base_delay_seconds * (2 ** (self.attempt - 1)),
            self.policy.max_delay_seconds,
        )
        # Add random jitter within jitter_fraction of the delay
        jitter = delay * self.policy.jitter_fraction * (2 * random.random() - 1)
        return max(0.5, delay + jitter)


def classify_error(error: Exception) -> RetryCategory:
    """Classify a ServiceNow error to determine retry strategy."""
    # Network errors
    if isinstance(error, (TimeoutError, ConnectionError, OSError)):
        return RetryCategory.NETWORK

    # Authentication errors from our own auth module
    from app.services.servicenow.client import ServiceNowAuthError
    if isinstance(error, ServiceNowAuthError):
        return RetryCategory.AUTHENTICATION

    # HTTP 429 rate limiting (from ServiceNowHTTPError)
    from app.services.servicenow.client import ServiceNowHTTPError
    if isinstance(error, ServiceNowHTTPError):
        msg = str(error).lower()
        if "rate-limited" in msg or "429" in msg:
            return RetryCategory.RATE_LIMIT
        if "5" in msg[:10] and ("error" in msg or "server" in msg):
            return RetryCategory.SERVER_ERROR

    # Check for HTTP status codes embedded in message
    error_msg = str(error)
    if "429" in error_msg:
        return RetryCategory.RATE_LIMIT
    if any(f"s {status}" in error_msg or f"{status} " in error_msg for status in ("500", "502", "503", "504")):
        return RetryCategory.SERVER_ERROR

    # Client errors (4xx) are fatal — don't retry
    if any(f"s {code}" in error_msg or f"{code} " in error_msg for code in ("400", "401", "403", "404", "422")):
        return RetryCategory.CLIENT_ERROR

    # Default to fatal for unknown errors
    return RetryCategory.FATAL


class RetryBudgetExceeded(Exception):
    """All retry attempts exhausted for this error category."""
