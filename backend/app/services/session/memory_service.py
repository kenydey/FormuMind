"""
Session memory service — SQLite authority with Redis hot cache (2026-09-05).

会话入库项目数据库: ``chat_sessions`` / ``chat_messages`` 存于主库
(formumind.db), project_id 关联项目, 消息全量落库 —— Redis 重启/清空
不会丢会话。Redis 仅作热缓存: save 写穿 setex(TTL), load 优先 SQLite。

接口(init/close/is_available/save/load/delete/count/list)与旧 Redis 实现
对齐, 新增 project_id/title 支持(前端会话归属项目)。
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func

logger = logging.getLogger(__name__)

HOT_CACHE_TTL = 86400  # 24h —— Redis 热缓存 TTL(权威在 SQLite)


class SessionMemoryService:
    """Project-scoped chat sessions persisted in the main SQLite database."""

    def __init__(self) -> None:
        from ...db.database import default_session_factory

        self._session_factory = default_session_factory()
        self._initialized = True
        self._redis = None  # optional hot cache, lazily created

    # ── lifecycle ──────────────────────────────────────────────
    async def initialize(self) -> bool:
        try:
            self._ensure_tables()
            self._initialized = True
            return True
        except Exception as e:  # pragma: no cover
            logger.warning("session memory init failed: %s", e)
            self._initialized = False
            return False

    async def close(self) -> None:
        self._initialized = False

    async def is_available(self) -> bool:
        try:
            self._ensure_tables()
            return True
        except Exception:
            return False

    def _ensure_tables(self) -> None:
        """create_all 会建新表; 此处兜底(老进程热更后表未建时)。"""
        from ...db.models import Base

        engine = getattr(self._session_factory, "bind", None)
        if engine is not None:
            Base.metadata.create_all(engine)

    # ── redis hot cache (best-effort) ──────────────────────────
    async def _get_redis(self):
        if self._redis is None:
            import redis.asyncio as rredis

            from ...config import get_settings

            self._redis = rredis.from_url(
                get_settings().redis_url, encoding="utf-8", decode_responses=True
            )
        return self._redis

    async def _cache_write(self, session_id: str, history: list, context: dict) -> None:
        try:
            client = await self._get_redis()
            data = {
                "history": history,
                "updated_at": json.dumps({"$date": "now"}),
                "context": context or {},
            }
            await client.setex(f"chat_history:{session_id}", HOT_CACHE_TTL, json.dumps(data, default=str))
            await client.setex(
                f"chat_session:{session_id}",
                HOT_CACHE_TTL,
                json.dumps(
                    {"history_count": len(history), "updated_at": json.dumps({"$date": "now"}),
                     "has_context": bool(context)},
                    default=str,
                ),
            )
        except Exception as e:  # hot cache failure is non-fatal
            logger.debug("redis hot cache write failed: %s", e)

    async def _cache_drop(self, session_id: str) -> None:
        try:
            client = await self._get_redis()
            await client.delete(f"chat_history:{session_id}", f"chat_session:{session_id}")
        except Exception:
            pass

    # ── persistence API ────────────────────────────────────────
    async def save_chat_session(
        self,
        session_id: str,
        history: List[Dict[str, Any]],
        context: Optional[Dict[str, Any]] = None,
        ttl_seconds: int = HOT_CACHE_TTL,  # noqa: ARG002 — kept for API compat (Redis era)
        project_id: Optional[str] = None,
        title: Optional[str] = None,
    ) -> bool:
        """SQLite authority: upsert session row + full rewrite of its messages."""
        from ...db.models import ChatMessageRow, ChatSessionRow
        from ...db.session_utils import commit_session

        context = context or {}
        title = title or ""
        try:
            with commit_session(self._session_factory) as session:
                existing = session.get(ChatSessionRow, session_id)
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                if existing is None:
                    existing = ChatSessionRow(
                        id=session_id,
                        project_id=project_id,
                        title=title or "",
                        created_at=now,
                    )
                    session.add(existing)
                else:
                    if project_id is not None:
                        existing.project_id = project_id
                    if title:
                        existing.title = title
                existing.context = context
                existing.has_context = bool(context)
                existing.message_count = len(history)
                existing.updated_at = now
                session.flush()
                # messages: 全量重写(消息无外部 id 引用; 保序 seq)
                session.query(ChatMessageRow).filter(
                    ChatMessageRow.session_id == session_id
                ).delete(synchronize_session=False)
                for i, turn in enumerate(history or []):
                    role = str(turn.get("role") or "user")[:16]
                    content = str(turn.get("content") or "")
                    meta = {k: v for k, v in turn.items() if k not in ("role", "content")}
                    session.add(
                        ChatMessageRow(
                            id=str(uuid.uuid4()),
                            session_id=session_id,
                            project_id=project_id,
                            role=role,
                            content=content,
                            meta_json=meta or None,
                            seq=i,
                            created_at=now,
                        )
                    )
            await self._cache_write(session_id, history or [], context)
            logger.debug("saved chat session %s (%d turns, project=%s)", session_id, len(history or []), project_id)
            return True
        except Exception as e:
            logger.error("failed to save chat session %s: %s", session_id, e)
            return False

    async def load_chat_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """SQLite authority; Redis only as fallback if row is missing."""
        from ...db.models import ChatMessageRow, ChatSessionRow

        try:
            with self._session_factory() as session:
                sess = session.get(ChatSessionRow, session_id)
                if sess is None:
                    return await self._cache_read(session_id)
                messages = (
                    session.query(ChatMessageRow)
                    .filter(ChatMessageRow.session_id == session_id)
                    .order_by(ChatMessageRow.seq.asc(), ChatMessageRow.created_at.asc())
                    .all()
                )
                history = []
                for m in messages:
                    turn: dict = {"role": m.role, "content": m.content}
                    if m.meta_json:
                        turn.update({k: v for k, v in m.meta_json.items() if k not in ("role", "content")})
                    history.append(turn)
                return {
                    "history": history,
                    "context": sess.context or {},
                    "updated_at": sess.updated_at.isoformat() if sess.updated_at else None,
                    "project_id": sess.project_id,
                    "title": sess.title,
                }
        except Exception as e:
            logger.error("failed to load chat session %s: %s", session_id, e)
            return None

    async def _cache_read(self, session_id: str) -> Optional[Dict[str, Any]]:
        try:
            client = await self._get_redis()
            raw = await client.get(f"chat_history:{session_id}")
            if raw is None:
                return None
            data = json.loads(raw)
            return {
                "history": data.get("history", []),
                "context": data.get("context") or {},
                "updated_at": data.get("updated_at"),
            }
        except Exception:
            return None

    async def delete_chat_session(self, session_id: str) -> bool:
        from ...db.models import ChatMessageRow, ChatSessionRow
        from ...db.session_utils import commit_session

        try:
            with commit_session(self._session_factory) as session:
                sess = session.get(ChatSessionRow, session_id)
                if sess is None:
                    return False
                session.query(ChatMessageRow).filter(
                    ChatMessageRow.session_id == session_id
                ).delete(synchronize_session=False)
                session.delete(sess)
            await self._cache_drop(session_id)
            return True
        except Exception as e:
            logger.error("failed to delete chat session %s: %s", session_id, e)
            return False

    async def get_session_history_count(self, session_id: str) -> int:
        from ...db.models import ChatSessionRow

        try:
            with self._session_factory() as session:
                sess = session.get(ChatSessionRow, session_id)
                return sess.message_count if sess else 0
        except Exception:
            return 0

    async def list_active_sessions(
        self, limit: int = 100, project_id: Optional[str] = None
    ) -> List[str]:
        from ...db.models import ChatSessionRow

        try:
            with self._session_factory() as session:
                q = session.query(ChatSessionRow).order_by(ChatSessionRow.updated_at.desc())
                if project_id:
                    q = q.filter(ChatSessionRow.project_id == project_id)
                rows = q.limit(limit).all()
                return [r.id for r in rows]
        except Exception as e:
            logger.error("failed to list sessions: %s", e)
            return []

    async def session_meta(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Session metadata incl. project_id + title (list UI)."""
        from ...db.models import ChatSessionRow

        try:
            with self._session_factory() as session:
                sess = session.get(ChatSessionRow, session_id)
                if sess is None:
                    return None
                return {
                    "session_id": sess.id,
                    "project_id": sess.project_id,
                    "title": sess.title,
                    "history_count": sess.message_count,
                    "has_context": sess.has_context,
                    "updated_at": sess.updated_at.isoformat() if sess.updated_at else None,
                    "created_at": sess.created_at.isoformat() if sess.created_at else None,
                }
        except Exception:
            return None


# Global instance
_session_service: Optional[SessionMemoryService] = None


def get_session_memory_service() -> SessionMemoryService:
    """Get or create the global session memory service instance."""
    global _session_service
    if _session_service is None:
        _session_service = SessionMemoryService()
    return _session_service


async def init_session_memory() -> bool:
    """Initialize the session memory service."""
    service = get_session_memory_service()
    return await service.initialize()


async def close_session_memory() -> None:
    """Close the session memory service."""
    global _session_service
    if _session_service is not None:
        await _session_service.close()
        _session_service = None
