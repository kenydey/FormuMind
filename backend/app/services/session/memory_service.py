"""
Session memory service for persistent chat context using Redis.

Provides persistent storage of chat history and context across service restarts,
enabling true multi-turn conversation memory.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import redis.asyncio as redis

from ...config import get_settings

logger = logging.getLogger(__name__)

# Redis key prefixes
CHAT_SESSION_PREFIX = "chat_session:"
CHAT_HISTORY_PREFIX = "chat_history:"

class SessionMemoryService:
    """Service for managing persistent chat session memory using Redis."""
    
    def __init__(self):
        self.settings = get_settings()
        self.redis_client: Optional[redis.Redis] = None
        self._initialized = False
    
    async def _get_redis(self) -> redis.Redis:
        """Get or create Redis client."""
        if self.redis_client is None:
            self.redis_client = redis.from_url(
                self.settings.redis_url,
                encoding="utf-8",
                decode_responses=True
            )
        return self.redis_client
    
    async def initialize(self) -> bool:
        """Initialize Redis connection."""
        try:
            client = await self._get_redis()
            await client.ping()
            self._initialized = True
            logger.info("Session memory service initialized with Redis")
            return True
        except Exception as e:
            logger.warning(f"Failed to initialize session memory service: {e}")
            self._initialized = False
            return False
    
    async def close(self) -> None:
        """Close Redis connection."""
        if self.redis_client:
            await self.redis_client.close()
            self.redis_client = None
        self._initialized = False
    
    async def is_available(self) -> bool:
        """Check if Redis is available."""
        if not self._initialized:
            await self.initialize()
        
        if not self._initialized:
            return False
            
        try:
            client = await self._get_redis()
            await client.ping()
            return True
        except Exception:
            self._initialized = False
            return False
    
    async def save_chat_session(
        self, 
        session_id: str, 
        history: List[Dict[str, Any]], 
        context: Optional[Dict[str, Any]] = None,
        ttl_seconds: int = 86400  # 24 hours default
    ) -> bool:
        """
        Save chat session to Redis.
        
        Args:
            session_id: Unique session identifier
            history: List of chat turns
            context: Additional context data (goals, constraints, etc.)
            ttl_seconds: Time to live in seconds
            
        Returns:
            True if successful, False otherwise
        """
        if not await self.is_available():
            logger.warning("Session memory not available (Redis unavailable)")
            return False
            
        try:
            client = await self._get_redis()
            
            # Save chat history
            history_key = f"{CHAT_HISTORY_PREFIX}{session_id}"
            history_data = {
                "history": history,
                "updated_at": json.dumps({"$date": "now"}),
                "context": context or {}
            }
            await client.setex(
                history_key,
                ttl_seconds,
                json.dumps(history_data, default=str)
            )
            
            # Also save to session index for quick lookup
            session_key = f"{CHAT_SESSION_PREFIX}{session_id}"
            await client.setex(
                session_key,
                ttl_seconds,
                json.dumps({
                    "history_count": len(history),
                    "updated_at": json.dumps({"$date": "now"}),
                    "has_context": bool(context)
                }, default=str)
            )
            
            logger.debug(f"Saved chat session {session_id} with {len(history)} turns")
            return True
            
        except Exception as e:
            logger.error(f"Failed to save chat session {session_id}: {e}")
            return False
    
    async def load_chat_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Load chat session from Redis.
        
        Args:
            session_id: Unique session identifier
            
        Returns:
            Dictionary with history and context, or None if not found/expired
        """
        if not await self.is_available():
            logger.warning("Session memory not available (Redis unavailable)")
            return None
            
        try:
            client = await self._get_redis()
            history_key = f"{CHAT_HISTORY_PREFIX}{session_id}"
            data = await client.get(history_key)
            
            if data is None:
                logger.debug(f"Chat session {session_id} not found or expired")
                return None
                
            session_data = json.loads(data)
            logger.debug(f"Loaded chat session {session_id} with {len(session_data.get('history', []))} turns")
            return session_data
            
        except Exception as e:
            logger.error(f"Failed to load chat session {session_id}: {e}")
            return None
    
    async def delete_chat_session(self, session_id: str) -> bool:
        """
        Delete chat session from Redis.
        
        Args:
            session_id: Unique session identifier
            
        Returns:
            True if successful, False otherwise
        """
        if not await self.is_available():
            logger.warning("Session memory not available (Redis unavailable)")
            return False
            
        try:
            client = await self._get_redis()
            history_key = f"{CHAT_HISTORY_PREFIX}{session_id}"
            session_key = f"{CHAT_SESSION_PREFIX}{session_id}"
            
            deleted = await client.delete(history_key, session_key)
            logger.debug(f"Deleted chat session {session_id}")
            return deleted > 0
            
        except Exception as e:
            logger.error(f"Failed to delete chat session {session_id}: {e}")
            return False
    
    async def get_session_history_count(self, session_id: str) -> int:
        """
        Get the number of turns in a chat session.
        
        Args:
            session_id: Unique session identifier
            
        Returns:
            Number of turns, 0 if not found
        """
        if not await self.is_available():
            return 0
            
        try:
            client = await self._get_redis()
            session_key = f"{CHAT_SESSION_PREFIX}{session_id}"
            data = await client.get(session_key)
            
            if data is None:
                return 0
                
            session_data = json.loads(data)
            return session_data.get("history_count", 0)
            
        except Exception as e:
            logger.error(f"Failed to get session history count for {session_id}: {e}")
            return 0
    
    async def list_active_sessions(self, limit: int = 100) -> List[str]:
        """
        List active chat session IDs.
        
        Args:
            limit: Maximum number of sessions to return
            
        Returns:
            List of session IDs
        """
        if not await self.is_available():
            return []
            
        try:
            client = await self._get_redis()
            pattern = f"{CHAT_SESSION_PREFIX}*"
            keys = await client.keys(pattern)
            # Extract session IDs from keys
            session_ids = [
                key.replace(CHAT_SESSION_PREFIX, "") 
                for key in keys[:limit]
            ]
            return session_ids
        except Exception as e:
            logger.error(f"Failed to list active sessions: {e}")
            return []

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