"""
Redis client management.
"""
from redis import asyncio as aioredis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.core.config import get_settings

settings = get_settings()

redis_client = aioredis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)

# Health states reported by ``redis_health()``.
REDIS_UP = "up"
REDIS_DOWN = "down"
REDIS_UNREACHABLE = "unreachable"


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
    try:
        if await redis_client.ping():
            return REDIS_UP
        return REDIS_DOWN
    except (RedisConnectionError, RedisTimeoutError):
        return REDIS_UNREACHABLE
    except (RedisError, OSError):
        return REDIS_DOWN
    except Exception:
        return REDIS_DOWN
