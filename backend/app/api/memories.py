"""Agent memory management API (W3-8 / P1-1 UI).

Read/delete endpoints backing the settings memory panel. Auth is enforced by
the global bearer-token middleware (see middleware/api_auth), same as the
other routers; no per-endpoint auth code here.

NOTE: this router is intentionally NOT registered in main.py — the main flow
owns router registration and will wire it up separately.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from ..services import agent_memory

router = APIRouter(prefix="/api/memories", tags=["memories"])

_SCOPES = ("global", "project", "user")
_MEM_TABLE = "agent_memories"
_FTS_TABLE = "agent_memories_fts"


def _session_factory() -> sessionmaker[Session]:
    from ..db.database import default_session_factory

    return default_session_factory()


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("", response_model=dict)
def list_memories(
    scope: str | None = Query(default=None, description="global|project|user"),
    scope_id: str | None = Query(default=None),
    q: str | None = Query(default=None, description="substring filter on key/value"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict:
    if scope is not None and scope not in _SCOPES:
        raise HTTPException(status_code=400, detail=f"scope must be one of {_SCOPES}")
    sf = _session_factory()
    agent_memory.ensure_agent_memory(sf)
    where = []
    params: dict = {}
    if scope is not None:
        where.append("scope = :scope")
        params["scope"] = scope
    if scope_id is not None:
        where.append("scope_id = :scope_id")
        params["scope_id"] = scope_id
    if q:
        where.append("(key LIKE :q ESCAPE '\\' OR value LIKE :q ESCAPE '\\')")
        params["q"] = f"%{_escape_like(q)}%"
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    with sf() as session:
        total = session.execute(
            text(f"SELECT COUNT(*) FROM {_MEM_TABLE} {clause}"), params
        ).scalar() or 0
        rows = session.execute(
            text(
                f"SELECT id, scope, scope_id, key, value, created_at, updated_at "
                f"FROM {_MEM_TABLE} {clause} "
                f"ORDER BY updated_at DESC, id DESC "
                f"LIMIT :lim OFFSET :off"
            ),
            {**params, "lim": page_size, "off": (page - 1) * page_size},
        ).mappings().all()
    return {
        "items": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.delete("/{memory_id}", response_model=dict)
def delete_memory(memory_id: int) -> dict:
    sf = _session_factory()
    agent_memory.ensure_agent_memory(sf)
    with sf() as session:
        exists = session.execute(
            text(f"SELECT id FROM {_MEM_TABLE} WHERE id = :mid"),
            {"mid": memory_id},
        ).scalar()
        if exists is None:
            raise HTTPException(status_code=404, detail="Memory not found")
        session.execute(
            text(f"DELETE FROM {_FTS_TABLE} WHERE memory_id = :mid"),
            {"mid": memory_id},
        )
        session.execute(
            text(f"DELETE FROM {_MEM_TABLE} WHERE id = :mid"),
            {"mid": memory_id},
        )
        session.commit()
    return {"ok": True, "id": memory_id}
