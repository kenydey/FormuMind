"""Redis Pub/Sub skeleton for the multi-agent layer (v0.8) — reserved.

This wires channel names and gated publish/subscribe helpers so the next phase
(heavy physics computation) can hand jobs off asynchronously. It is fully
optional: when ``agent_bus_enabled`` is False, Redis is unreachable, or the
``redis`` library is missing, every call is a silent no-op. This preserves the
offline / Celery-eager behaviour and keeps tests free of external services.
"""
from __future__ import annotations

import logging
from ..services.errors import degrade_return, log_handled_exception, optional_import, reraise_if_fatal
import json

from ..config import get_settings

logger = logging.getLogger(__name__)

# Logical channel keys → concrete Redis channel names.
CHANNELS: dict[str, str] = {
    "agent_events": "formumind:agent:events",
    # Reserved for the next phase: a physics worker subscribes to job requests
    # and publishes results back.
    "physics_jobs": "formumind:physics:jobs",
    "physics_results": "formumind:physics:results",
}


def _client():
    """Return a live Redis client, or None when the bus is disabled/unreachable.

    Gated by ``agent_bus_enabled`` and a short ping timeout so a missing or slow
    Redis never blocks a request.
    """
    s = get_settings()
    if not s.agent_bus_enabled:
        return None
    try:
        import redis  # redis is a core dependency (Celery broker)

        client = redis.Redis.from_url(s.redis_url, socket_connect_timeout=0.2)
        client.ping()
        return client
    except Exception as exc:
        return degrade_return(logger, exc, "operation failed", None)


def publish(channel_key: str, payload: dict) -> bool:
    """Publish a JSON payload to a logical channel.

    Returns True if the message was sent, False on any no-op/failure path.
    """
    client = _client()
    if client is None:
        return False
    channel = CHANNELS.get(channel_key)
    if channel is None:
        return False
    try:
        client.publish(channel, json.dumps(payload, ensure_ascii=False))
        return True
    except Exception as exc:
        log_handled_exception(logger, exc, "optional feature check")
        return False


def subscribe(channel_key: str):
    """Subscribe to a logical channel; returns a Redis PubSub or None.

    Reserved for the next-phase physics worker. No-op (None) when the bus is
    disabled or Redis is unreachable.
    """
    client = _client()
    if client is None:
        return None
    channel = CHANNELS.get(channel_key)
    if channel is None:
        return None
    try:
        pubsub = client.pubsub()
        pubsub.subscribe(channel)
        return pubsub
    except Exception as exc:
        return degrade_return(logger, exc, "operation failed", None)
