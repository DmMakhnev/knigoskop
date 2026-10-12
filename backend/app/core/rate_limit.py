from functools import lru_cache

from redis import Redis
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.errors import AppError
from app.core.security import digest


@lru_cache
def get_redis():
    return Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=2,
        socket_timeout=2,
        decode_responses=True,
    )


def enforce_rate_limit(redis, bucket: str, identity: str, limit: int):
    key = f"auth-rate:{bucket}:{digest(identity)}"
    try:
        with redis.pipeline(transaction=True) as pipeline:
            pipeline.incr(key)
            pipeline.expire(key, settings.auth_rate_window_seconds, nx=True)
            count, _ = pipeline.execute()
    except RedisError as exc:
        raise AppError(
            503, "auth_unavailable", "Авторизация временно недоступна"
        ) from exc
    if count > limit:
        raise AppError(429, "rate_limited", "Слишком много попыток. Попробуйте позже")
