"""Shared pytest helpers."""
from __future__ import annotations

import pytest


def redis_available() -> bool:
    try:
        import redis

        client = redis.Redis.from_url("redis://localhost:6379/0", socket_connect_timeout=0.5)
        client.ping()
        return True
    except Exception:
        return False


requires_redis = pytest.mark.skipif(not redis_available(), reason="Redis not available")
