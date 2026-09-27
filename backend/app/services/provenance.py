"""Cross-artifact provenance graph (W2-6 / P1-2).

Records directed edges between five node kinds so a formulation conclusion
can be traced back to the literature / experimental data behind it:

    formulation -> claim -> source        (report / recommendation claims)
    run -> formulation                    (DOE experiments test formulations)

Style mirrors ``services/source_fts.py``: plain-SQLite table created by an
independent ``ensure_*`` function (no alembic), and every public helper is
fail-open — a provenance error is logged and swallowed so provenance can
never break the business path it instruments.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)

_PROV_TABLE = "provenance_edges"
_PROV_META = "provenance_meta"
_PROV_SCHEMA = "1"

#: Node kinds that may appear on either end of an edge.
NODE_TYPES = frozenset({"artifact", "run", "source", "claim", "formulation"})

#: Reserved ``SourceDocument`` fields that provenance / agents must not rewrite.
#: ``source_url`` is the plan name for the model's ``origin_url``; ``retrieved_at``
#: has no model column yet but is guarded for forward compatibility.
RESERVED_SOURCE_FIELDS = frozenset(
    {"source_url", "retrieved_at", "content_hash", "origin_url", "created_at"}
)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _session_factory() -> sessionmaker[Session]:
    from ..db.database import default_session_factory

    return default_session_factory()


def claim_id_for_text(text_value: str) -> str:
    """Stable claim node id derived from the claim text (sha256, 16 hex)."""
    digest = hashlib.sha256((text_value or "").encode("utf-8")).hexdigest()[:16]
    return f"claim:{digest}"


def ensure_provenance(session_factory: sessionmaker[Session] | None = None) -> None:
    """Create the provenance edge table + schema meta if missing/outdated."""
    sf = session_factory or _session_factory()
    with sf() as session:
        session.execute(
            text(
                f"""CREATE TABLE IF NOT EXISTS {_PROV_META} (
                    k TEXT PRIMARY KEY,
                    v TEXT NOT NULL
                )"""
            )
        )
        cur = session.execute(
            text(f"SELECT v FROM {_PROV_META} WHERE k = 'schema'")
        ).scalar()
        if cur != _PROV_SCHEMA:
            session.execute(text(f"DROP TABLE IF EXISTS {_PROV_TABLE}"))
            _create_table(session)
            session.execute(
                text(
                    f"""INSERT INTO {_PROV_META}(k, v) VALUES ('schema', :v)
                    ON CONFLICT(k) DO UPDATE SET v = excluded.v"""
                ),
                {"v": _PROV_SCHEMA},
            )
            session.commit()
            return
        _create_table(session)
        session.commit()


def _create_table(session: Session) -> None:
    session.execute(
        text(
            f"""CREATE TABLE IF NOT EXISTS {_PROV_TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_type TEXT NOT NULL,
                from_id TEXT NOT NULL,
                to_type TEXT NOT NULL,
                to_id TEXT NOT NULL,
                relation TEXT NOT NULL DEFAULT 'derived_from',
                created_at TEXT NOT NULL,
                UNIQUE(from_type, from_id, to_type, to_id, relation)
            )"""
        )
    )
    session.execute(
        text(
            f"CREATE INDEX IF NOT EXISTS idx_prov_from "
            f"ON {_PROV_TABLE}(from_type, from_id)"
        )
    )
    session.execute(
        text(
            f"CREATE INDEX IF NOT EXISTS idx_prov_to "
            f"ON {_PROV_TABLE}(to_type, to_id)"
        )
    )


def _validate_node(node_type: str, node_id: str) -> None:
    if node_type not in NODE_TYPES:
        raise ValueError(f"unknown provenance node type: {node_type!r}")
    if not node_id:
        raise ValueError("provenance node id must be non-empty")


def link(
    from_type: str,
    from_id: str,
    to_type: str,
    to_id: str,
    relation: str = "derived_from",
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> bool:
    """Record one directed edge. Idempotent; fail-open (returns False on DB error).

    Raises ``ValueError`` on an unknown node type or empty node id — those are
    programmer errors, not runtime failures.
    """
    _validate_node(from_type, from_id)
    _validate_node(to_type, to_id)
    try:
        sf = session_factory or _session_factory()
        ensure_provenance(sf)
        with sf() as session:
            session.execute(
                text(
                    f"""INSERT OR IGNORE INTO {_PROV_TABLE}
                    (from_type, from_id, to_type, to_id, relation, created_at)
                    VALUES (:ft, :fid, :tt, :tid, :rel, :now)"""
                ),
                {
                    "ft": from_type,
                    "fid": from_id,
                    "tt": to_type,
                    "tid": to_id,
                    "rel": relation or "derived_from",
                    "now": _utcnow_iso(),
                },
            )
            session.commit()
        return True
    except Exception:
        logger.warning("provenance.link failed (fail-open)", exc_info=True)
        return False


def record_claim_sources(
    claim_text: str,
    source_ids: list[str],
    *,
    relation: str = "cites",
    session_factory: sessionmaker[Session] | None = None,
) -> str:
    """Link one claim node to its evidence sources. Returns the claim node id."""
    claim_node_id = claim_id_for_text(claim_text)
    seen: set[str] = set()
    for sid in source_ids or []:
        if not sid or sid in seen:
            continue
        seen.add(sid)
        link(
            "claim",
            claim_node_id,
            "source",
            sid,
            relation,
            session_factory=session_factory,
        )
    return claim_node_id


def lineage(
    node_type: str,
    node_id: str,
    *,
    depth: int = 3,
    session_factory: sessionmaker[Session] | None = None,
) -> list[dict[str, Any]]:
    """Upstream BFS from a node: edges whose target is the node, then targets'
    own upstream edges, up to ``depth`` hops.

    Cycle-safe (visited node keys) and deduped (UNIQUE edge constraint plus a
    seen-edge set). Returns edge dicts in BFS order. Fail-open: returns [] on
    DB error.
    """
    _validate_node(node_type, node_id)
    if depth < 1:
        return []
    try:
        sf = session_factory or _session_factory()
        ensure_provenance(sf)
        with sf() as session:
            return _bfs(session, node_type, node_id, depth)
    except Exception:
        logger.warning("provenance.lineage failed (fail-open)", exc_info=True)
        return []


def _bfs(
    session: Session, node_type: str, node_id: str, depth: int
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    visited: set[tuple[str, str]] = {(node_type, node_id)}
    seen_edges: set[tuple[str, str, str, str, str]] = set()
    frontier: list[tuple[str, str]] = [(node_type, node_id)]
    for _ in range(depth):
        nxt: list[tuple[str, str]] = []
        for ttype, tid in frontier:
            rows = session.execute(
                text(
                    f"""SELECT from_type, from_id, to_type, to_id, relation
                    FROM {_PROV_TABLE}
                    WHERE to_type = :tt AND to_id = :tid"""
                ),
                {"tt": ttype, "tid": tid},
            ).all()
            for from_type, from_id, to_type, to_id, relation in rows:
                key = (from_type, from_id, to_type, to_id, relation)
                if key in seen_edges:
                    continue
                seen_edges.add(key)
                out.append(
                    {
                        "from_type": from_type,
                        "from_id": from_id,
                        "to_type": to_type,
                        "to_id": to_id,
                        "relation": relation,
                    }
                )
                node_key = (from_type, from_id)
                if node_key not in visited:
                    visited.add(node_key)
                    nxt.append(node_key)
        frontier = nxt
        if not frontier:
            break
    return out


def formulation_id_for(formulation: Any) -> str:
    """Stable formulation node id from ingredient composition (sha256, 16 hex).

    Accepts a ``Formulation``-like object or a plain dict. Same composition →
    same node id, so repeated DOE runs testing one composition converge on one
    node and cross-run lineage queries work.
    """
    try:
        ings = (
            formulation.ingredients
            if hasattr(formulation, "ingredients")
            else (formulation.get("ingredients", []) if isinstance(formulation, dict) else [])
        )
        rows: list[str] = []
        for ing in ings or []:
            if hasattr(ing, "name"):
                name, pct = ing.name or "", ing.weight_pct or 0
            elif isinstance(ing, dict):
                name, pct = ing.get("name", ""), ing.get("weight_pct", 0)
            else:
                continue
            rows.append(f"{name}:{round(float(pct), 4)}")
        payload = "|".join(sorted(rows))
    except Exception:
        payload = repr(formulation)[:500]
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"formulation:{digest}"


def count_edges(*, session_factory: sessionmaker[Session] | None = None) -> int:
    """Edge count (diagnostics/tests). Fail-open: -1 on DB error."""
    try:
        sf = session_factory or _session_factory()
        ensure_provenance(sf)
        with sf() as session:
            return int(
                session.execute(text(f"SELECT COUNT(*) FROM {_PROV_TABLE}")).scalar() or 0
            )
    except Exception:
        logger.warning("provenance.count_edges failed (fail-open)", exc_info=True)
        return -1
