"""
Retry-with-backoff utility for the collection scheduler.

Computes exponential backoff delays (base * 2^(attempt-1), capped, with
optional ±25% jitter) used by CollectionOrchestrator to schedule the next
run after a transient failure.  Also provides a helper to compute the
next `next_run_at` timestamp after a successful run (simply the poll
interval).
"""
from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class RetryConfig:
    """Immutable retry/backoff configuration sourced from Settings."""

    max_attempts: int
    base_delay_seconds: float
    max_delay_seconds: float
    jitter: bool


def compute_backoff_delay(
    attempt: int,
    config: RetryConfig,
) -> float:
    """Compute the delay (in seconds) before retry *attempt*.

    Attempts are 1-indexed: ``attempt=1`` is the first retry after the
    initial failure.

    Formula:  ``base * 2^(attempt-1)``  capped at ``max_delay_seconds``,
    with ±25% uniform jitter applied if ``config.jitter`` is enabled.

    Parameters
    ----------
    attempt:
        1-indexed retry attempt number (1 = first retry).
    config:
        Retry configuration (base delay, max delay, jitter toggle).
    """
    if attempt < 1:
        attempt = 1

    raw = config.base_delay_seconds * (2 ** (attempt - 1))
    raw = min(raw, config.max_delay_seconds)

    if config.jitter:
        # ±25% jitter: multiply by uniform(0.75, 1.25)
        raw = raw * random.uniform(0.75, 1.25)

    return raw


def compute_next_run_after_success(
    poll_interval_seconds: int,
    jitter_seconds: int = 5,
) -> float:
    """Compute the delay (in seconds) until the next scheduled run after a
    *successful* collection.  A small random jitter is added to spread
    sources that share the same poll interval, avoiding thundering-herd
    on the next tick."""
    return float(poll_interval_seconds) + random.uniform(0, float(jitter_seconds))


def compute_next_run_after_failure(
    consecutive_failures: int,
    config: RetryConfig,
    poll_interval_seconds: int,
) -> float:
    """Compute the delay (in seconds) until the next scheduled run after a
    *failed* collection.

    On the first few failures, the backoff delay (which grows
    exponentially) is used instead of the normal poll interval, so the
    scheduler backs off from a struggling source.  Once
    ``config.max_attempts`` failures accumulate, the delay is capped at
    ``max_delay_seconds`` (the "circuit breaker" ceiling).
    """
    if consecutive_failures <= 0:
        return float(poll_interval_seconds)

    backoff = compute_backoff_delay(
        attempt=min(consecutive_failures, config.max_attempts),
        config=config,
    )
    # The backoff should never be shorter than the normal poll interval —
    # backing off means waiting *longer*, not shorter.
    return max(backoff, float(poll_interval_seconds) + 1.0)
