"""Session memory API endpoints for persistent chat context.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

from ..services.session.memory_service import (
    get_session_memory_service,
    init_session_memory,
    close_session_memory,
    SessionMemoryService
)
from ..domain.schemas import Requirement

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/session", tags=["session"])


class SessionSaveRequest(BaseModel):
    session_id: str = Field(..., description="Unique session identifier")
    history: List[Dict[str, Any]] = Field(default_factory=list, description="Chat history turns")
    context: Optional[Dict[str, Any]] = Field(None, description="Additional context data")
    ttl_seconds: int = Field(86400, ge=60, le=2592000, description="Time to live in seconds (default 24h)")


class SessionLoadResponse(BaseModel):
    history: List[Dict[str, Any]] = Field(default_factory=list)
    context: Optional[Dict[str, Any]] = None
    updated_at: Optional[str] = None


class SessionInfoResponse(BaseModel):
    session_id: str
    history_count: int
    has_context: bool
    updated_at: Optional[str] = None


class SessionListResponse(BaseModel):
    sessions: List[SessionInfoResponse] = Field(default_factory=list)
    total_count: int


class SessionDeleteResponse(BaseModel):
    success: bool
    message: str


@router.post("/save", response_model=Dict[str, str])
async def save_session(
    payload: SessionSaveRequest,
    service: SessionMemoryService = Depends(get_session_memory_service)
):
    """
    Save chat session to persistent memory.
    """
    try:
        # Initialize service if needed
        if not await service.is_available():
            await service.initialize()
        
        success = await service.save_chat_session(
            session_id=payload.session_id,
            history=payload.history,
            context=payload.context,
            ttl_seconds=payload.ttl_seconds
        )
        
        if success:
            return {"status": "success", "message": f"Session {payload.session_id} saved"}
        else:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Failed to save session (service unavailable)"
            )
    except Exception as e:
        logger.error(f"Error saving session {payload.session_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )


@router.get("/load/{session_id}", response_model=SessionLoadResponse)
async def load_session(
    session_id: str,
    service: SessionMemoryService = Depends(get_session_memory_service)
):
    """
    Load chat session from persistent memory.
    """
    try:
        # Initialize service if needed
        if not await service.is_available():
            await service.initialize()
        
        session_data = await service.load_chat_session(session_id)
        
        if session_data is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session {session_id} not found or expired"
            )
        
        return SessionLoadResponse(
            history=session_data.get("history", []),
            context=session_data.get("context"),
            updated_at=session_data.get("updated_at")
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error loading session {session_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )


@router.get("/info/{session_id}", response_model=SessionInfoResponse)
async def get_session_info(
    session_id: str,
    service: SessionMemoryService = Depends(get_session_memory_service)
):
    """
    Get session information (count, timestamps) without loading full history.
    """
    try:
        # Initialize service if needed
        if not await service.is_available():
            await service.initialize()
        
        history_count = await service.get_session_history_count(session_id)
        
        if history_count == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session {session_id} not found or expired"
            )
        
        # Get additional info
        session_data = await service.load_chat_session(session_id) or {}
        
        return SessionInfoResponse(
            session_id=session_id,
            history_count=history_count,
            has_context=bool(session_data.get("context")),
            updated_at=session_data.get("updated_at")
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting session info for {session_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )


@router.delete("/delete/{session_id}", response_model=SessionDeleteResponse)
async def delete_session(
    session_id: str,
    service: SessionMemoryService = Depends(get_session_memory_service)
):
    """
    Delete chat session from persistent memory.
    """
    try:
        # Initialize service if needed
        if not await service.is_available():
            await service.initialize()
        
        success = await service.delete_chat_session(session_id)
        
        if success:
            return SessionDeleteResponse(
                success=True,
                message=f"Session {session_id} deleted successfully"
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session {session_id} not found"
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting session {session_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )


@router.get("/list", response_model=SessionListResponse)
async def list_sessions(
    limit: int = 100,
    service: SessionMemoryService = Depends(get_session_memory_service)
):
    """
    List all active chat sessions.
    """
    try:
        # Initialize service if needed
        if not await service.is_available():
            await service.initialize()
        
        session_ids = await service.list_active_sessions(limit=limit)
        sessions_info = []
        
        for session_id in session_ids:
            history_count = await service.get_session_history_count(session_id)
            session_data = await service.load_chat_session(session_id) or {}
            
            sessions_info.append(SessionInfoResponse(
                session_id=session_id,
                history_count=history_count,
                has_context=bool(session_data.get("context")),
                updated_at=session_data.get("updated_at")
            ))
        
        return SessionListResponse(
            sessions=sessions_info,
            total_count=len(sessions_info)
        )
    except Exception as e:
        logger.error(f"Error listing sessions: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )


# Startup/shutdown handlers
async def init_session_memory_on_startup():
    """Initialize session memory on application startup."""
    try:
        success = await init_session_memory()
        if success:
            logger.info("Session memory service initialized successfully")
        else:
            logger.warning("Failed to initialize session memory service")
    except Exception as e:
        logger.error(f"Error initializing session memory service: {e}")


async def close_session_memory_on_shutdown():
    """Close session memory service on application shutdown."""
    try:
        await close_session_memory()
        logger.info("Session memory service closed successfully")
    except Exception as e:
        logger.error(f"Error closing session memory service: {e}")