"""
Redis client management.
"""
import logging

from redis import asyncio as aioredis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.core.config import get_settings

settings = get_settings()

logger = logging.getLogger(__name__)

# Connection/operation timeouts (seconds). Redis is a cache/rate-limit
# dependency, not a source of truth (PostgreSQL is), so a hung or unreachable
# Redis must fail FAST rather than block a request indefinitely: the connect
# timeout bounds the TCP/TLS handshake and the socket timeout bounds every
# subsequent operation. Each caller decides its own outage behaviour: the
# signup rate limiter (billing.py) falls back to a per-process limit.
_REDIS_CONNECT_TIMEOUT_SECONDS = 2.0
_REDIS_SOCKET_TIMEOUT_SECONDS = 5.0

redis_client = aioredis.from_url(
    settings.redis_url,
    encoding="utf-8",
    decode_responses=True,
    socket_connect_timeout=_REDIS_CONNECT_TIMEOUT_SECONDS,
    socket_timeout=_REDIS_SOCKET_TIMEOUT_SECONDS,
)

# Health states reported by ``redis_health()``.
REDIS_UP = "up"
REDIS_DOWN = "down"
REDIS_UNREACHABLE = "unreachable"

# Last observed health state, used ONLY to emit a single log line on a
# transition (degradation or recovery). Never stores or logs the URL or
# credentials - only the state names above.
_last_state: str | None = None


async def check_redis_connection() -> bool:
    try:
        return await redis_client.ping()
    except Exception:
        return False


async def redis_health() -> str:
    """Classify the configured Redis dependency as up / down / unreachable.

    These three states are operationally different and are reported
    separately:

      - ``up``           a real PING was answered by Redis
      - ``unreachable``  Redis could not be contacted at all (connection
                         refused / DNS failure / network timeout)
      - ``down``         Redis was contacted but did not answer a healthy
                         PING (or answered with an error)

    ``up`` is NEVER returned unless a real PING succeeded, so an
    unreachable Redis can never be reported as healthy.

    Only the state name is returned.  The configured URL and any
    credentials in it are never included in the result, so this value is
    safe to expose through the health API.
    """
    global _last_state
    try:
        if await redis_client.ping():
            state = REDIS_UP
        else:
            state = REDIS_DOWN
    except (RedisConnectionError, RedisTimeoutError):
        state = REDIS_UNREACHABLE
    except (RedisError, OSError):
        state = REDIS_DOWN
    except Exception:
        state = REDIS_DOWN

    # Structured, sanitized observability (only state names are logged - never
    # the URL or any credential). Log a degradation once on entering a non-up
    # state and recovery exactly once when Redis comes back. Steady-state
    # repeats are suppressed so a persistent outage does not flood the logs on
    # every health probe.
    prev = _last_state
    _last_state = state
    if state == REDIS_UP:
        if prev is not None and prev != REDIS_UP:
            logger.info("Redis dependency recovered: %s -> up", prev)
    elif prev != state:
        logger.warning("Redis dependency degraded: %s -> %s", prev, state)
    return state
