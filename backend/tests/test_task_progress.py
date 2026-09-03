"""Redis Pub/Sub progress bus unit tests."""
from __future__ import annotations

import json
import uuid

from tests.redis_helpers import requires_redis

from app.worker.task_progress import (
    TaskProgressStatus,
    channel_name,
    get_task_meta,
    get_task_result,
    persist_result,
    publish_progress,
    register_pending,
    task_exists,
)


def _redis_available() -> bool:
    from tests.redis_helpers import redis_available

    return redis_available()


pytestmark = requires_redis


def test_channel_name():
    assert channel_name("abc-123") == "task_progress:abc-123"


def test_publish_subscribe_roundtrip():
    import redis

    task_id = f"test-{uuid.uuid4().hex}"
    client = redis.Redis.from_url("redis://localhost:6379/0", decode_responses=True)
    pubsub = client.pubsub()
    pubsub.subscribe(channel_name(task_id))
    pubsub.get_message(timeout=2)  # subscribe ack

    event = publish_progress(
        task_id,
        TaskProgressStatus.RUNNING,
        stage="retrieve",
        message="正在检索",
        progress=0.2,
    )
    assert event.stage == "retrieve"

    msg = None
    for _ in range(20):
        raw = pubsub.get_message(timeout=2)
        if raw and raw.get("type") == "message":
            msg = raw
            break
    assert msg is not None
    payload = json.loads(msg["data"])
    assert payload["message"] == "正在检索"
    assert payload["status"] == "RUNNING"

    meta = get_task_meta(task_id)
    assert meta is not None
    assert meta["status"] == "RUNNING"
    assert meta["stage"] == "retrieve"

    register_pending(task_id, "deep_research")
    assert task_exists(task_id)

    persist_result(task_id, {"report": {"topic": "demo"}}, failed=False)
    result = get_task_result(task_id)
    assert result is not None
    assert result["report"]["topic"] == "demo"
    meta = get_task_meta(task_id)
    assert meta["status"] == "COMPLETED"

    pubsub.unsubscribe()
    client.delete(f"task:meta:{task_id}", f"task:result:{task_id}")
