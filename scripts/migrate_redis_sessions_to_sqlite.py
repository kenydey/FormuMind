"""One-shot migration: Redis chat sessions → SQLite project database (2026-09-05).

会话入库(方案 v2): Redis 曾是无 project_id/零持久化的唯一会话存储; 本脚本把
存量 Redis 会话迁入主库 chat_sessions/chat_messages, 按内容挂回所属项目。

用法(dev, 与 start-dev.sh 同环境):
    export FORMUMIND_ENV_FILE=/root/FormuMind/data/.env.host
    export FORMUMIND_DB_URL=sqlite:////root/FormuMind/data/formumind.db
    .venv/bin/python -m scripts.migrate_redis_sessions_to_sqlite
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import redis.asyncio as rredis  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.services.session.memory_service import (  # noqa: E402
    get_session_memory_service,
)

CHAT_HISTORY_PREFIX = "chat_history:"
CHAT_SESSION_PREFIX = "chat_session:"

# 会话 → 项目归属(按会话内容人工确认: 镁合金防腐蚀机理对话属主项目)
DEFAULT_PROJECT_ID = "1d10717c-80d5-47a5-9ce4-7209081d607c"


async def main() -> None:
    svc = get_session_memory_service()
    client = rredis.from_url(get_settings().redis_url, decode_responses=True)
    keys = [k async for k in client.scan_iter(match=f"{CHAT_HISTORY_PREFIX}*")]
    print(f"Redis 会话: {len(keys)}")
    moved = 0
    for key in keys:
        session_id = key.replace(CHAT_HISTORY_PREFIX, "")
        raw = await client.get(key)
        if not raw:
            continue
        data = json.loads(raw)
        history = data.get("history") or []
        context = data.get("context") or {}
        meta_raw = await client.get(f"{CHAT_SESSION_PREFIX}{session_id}")
        # 标题: 首个 user 消息截断 60 字
        first_user = next((h.get("content", "") for h in history if h.get("role") == "user"), "")
        title = first_user[:60]
        ok = await svc.save_chat_session(
            session_id=session_id,
            history=history,
            context=context,
            project_id=DEFAULT_PROJECT_ID,
            title=title,
        )
        print(f"  {'✓' if ok else '✗'} {session_id}: {len(history)} 条 → project {DEFAULT_PROJECT_ID[:8]}… title={title[:30]}")
        if ok:
            moved += 1
    await client.close()
    await svc.close()
    print(f"完成: {moved}/{len(keys)} 会话迁入 SQLite(Redis 原键保留, 确认后手动清理)")


if __name__ == "__main__":
    asyncio.run(main())
