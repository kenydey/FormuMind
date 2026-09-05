"""
Neo4j-backed knowledge graph adapter (optional).

This module provides a thin wrapper around the official `neo4j` Python driver
to expose knowledge-graph operations that are siblings to (not replacements
of) the SQLite-backed `app.services.kg` package.

The wrapper is **opt-in**: it is only used when both
``FORMUMIND_NEO4J_ENABLED=true`` and a reachable Bolt endpoint exist. When
unreachable, every public function returns ``None`` / ``[]`` / ``False`` so
the rest of the application can keep running with the existing SQLite
store. This makes the integration safe to add without changing the
default behaviour.

The companion `scripts/migrate_sql_to_neo4j.py` migrates the most important
entities (Compounds, Formulations) from SQLite into Neo4j when desired.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Lazy import: the `neo4j` driver may be absent in some slim installs.
try:
    from neo4j import GraphDatabase, Driver  # type: ignore
    from neo4j.exceptions import ServiceUnavailable, AuthError  # type: ignore

    _DRIVER_OK = True
except Exception:  # pragma: no cover - driver missing
    GraphDatabase = None  # type: ignore
    Driver = None  # type: ignore
    ServiceUnavailable = Exception  # type: ignore
    AuthError = Exception  # type: ignore
    _DRIVER_OK = False


_driver: Optional["Driver"] = None
_enabled: Optional[bool] = None  # cached result of env check


def _flag_from_env_file() -> str:
    """Fallback to the canonical env file (resolve_env_path) when the process
    env lacks the flag. Only UI-registry keys get loaded into os.environ by
    reload_settings, so file-only keys like FORMUMIND_NEO4J_ENABLED were
    silently dead (2026-09-05: neo4j container ran for days while
    is_enabled()==False)."""
    try:
        from pathlib import Path

        from ..config import resolve_env_path

        for line in Path(resolve_env_path()).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("FORMUMIND_NEO4J_ENABLED="):
                return line.split("=", 1)[1].strip().strip('"').strip("'").lower()
    except Exception:
        pass
    return ""


def _env_or_file(name: str, default: str = "") -> str:
    """os.environ first, then the canonical env file — same rationale as
    _flag_from_env_file (file-only keys never reach os.environ)."""
    val = (os.getenv(name, "") or "").strip()
    if val:
        return val
    try:
        from pathlib import Path

        from ..config import resolve_env_path

        prefix = name + "="
        for line in Path(resolve_env_path()).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(prefix):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return default


def is_enabled() -> bool:
    """Return True if the Neo4j adapter should be used.

    Prefers ``Settings.neo4j_enabled`` (promoted into ``os.environ`` by the
    Settings UI / env_flags). Falls back to env-or-file for processes that
    never load Settings.
    """
    global _enabled
    if _enabled is not None:
        return _enabled
    try:
        from ..config import get_settings

        _enabled = bool(get_settings().neo4j_enabled)
        return _enabled
    except Exception:
        pass
    flag = _env_or_file("FORMUMIND_NEO4J_ENABLED", "false").lower()
    _enabled = flag in {"1", "true", "yes", "on"}
    return _enabled


def _get_driver() -> Optional["Driver"]:
    """Create (or return) the Bolt driver. Returns None when disabled/unreachable."""
    global _driver
    if not is_enabled():
        return None
    if not _DRIVER_OK:
        logger.debug("neo4j driver not installed; skipping")
        return None
    if _driver is not None:
        return _driver
    uri = _env_or_file("FORMUMIND_NEO4J_URI", "bolt://kg:7687")
    user = _env_or_file("FORMUMIND_NEO4J_USER", "neo4j")
    password = _env_or_file("FORMUMIND_NEO4J_PASSWORD", "formumind123")
    try:
        _driver = GraphDatabase.driver(uri, auth=(user, password))
        # Verify reachability once, but don't hard-fail.
        with _driver.session() as s:
            s.run("RETURN 1").single()
        logger.info("Neo4j driver connected: %s", uri)
        return _driver
    except (ServiceUnavailable, AuthError) as e:  # type: ignore[misc]
        logger.warning("Neo4j unreachable (%s) — adapter disabled for this process", e)
        _driver = None
        return None
    except Exception as e:  # pragma: no cover - network surprise
        logger.warning("Neo4j driver init failed: %s", e)
        _driver = None
        return None


# ----- Schema bootstrap ---------------------------------------------------

_CONSTRAINTS: List[str] = [
    "CREATE CONSTRAINT compound_uid IF NOT EXISTS FOR (c:Compound) REQUIRE c.uid IS UNIQUE",
    "CREATE CONSTRAINT formulation_uid IF NOT EXISTS FOR (f:Formulation) REQUIRE f.uid IS UNIQUE",
    "CREATE CONSTRAINT experiment_uid IF NOT EXISTS FOR (e:ExperimentReport) REQUIRE e.uid IS UNIQUE",
    "CREATE CONSTRAINT compound_name IF NOT EXISTS FOR (c:Compound) REQUIRE c.name IS UNIQUE",
    "CREATE CONSTRAINT formulation_name IF NOT EXISTS FOR (f:Formulation) REQUIRE f.name IS UNIQUE",
]


def ensure_schema() -> bool:
    """Create uniqueness constraints. Safe to call repeatedly."""
    drv = _get_driver()
    if drv is None:
        return False
    try:
        with drv.session() as s:
            for stmt in _CONSTRAINTS:
                s.run(stmt)
        logger.info("Neo4j schema ensured (%d constraints)", len(_CONSTRAINTS))
        return True
    except Exception as e:
        logger.warning("ensure_schema failed: %s", e)
        return False


# ----- Write helpers ------------------------------------------------------


def upsert_compound(
    uid: str,
    name: str,
    *,
    cas_number: Optional[str] = None,
    smiles: Optional[str] = None,
    molecular_weight: Optional[float] = None,
    supplier: Optional[str] = None,
    notes: Optional[str] = None,
) -> bool:
    drv = _get_driver()
    if drv is None:
        return False
    try:
        with drv.session() as s:
            s.run(
                """
                MERGE (c:Compound {uid: $uid})
                ON CREATE SET c.created_at = datetime()
                SET c.name = $name,
                    c.cas_number = $cas_number,
                    c.smiles = $smiles,
                    c.molecular_weight = $molecular_weight,
                    c.supplier = $supplier,
                    c.notes = $notes,
                    c.updated_at = datetime()
                """,
                uid=uid,
                name=name,
                cas_number=cas_number,
                smiles=smiles,
                molecular_weight=molecular_weight,
                supplier=supplier,
                notes=notes,
            )
        return True
    except Exception as e:
        logger.warning("upsert_compound failed for %s: %s", name, e)
        return False


def upsert_formulation(
    uid: str,
    name: str,
    *,
    description: Optional[str] = None,
    target_property: Optional[str] = None,
    target_value: Optional[float] = None,
    status: str = "draft",
) -> bool:
    drv = _get_driver()
    if drv is None:
        return False
    try:
        with drv.session() as s:
            s.run(
                """
                MERGE (f:Formulation {uid: $uid})
                ON CREATE SET f.created_at = datetime()
                SET f.name = $name,
                    f.description = $description,
                    f.target_property = $target_property,
                    f.target_value = $target_value,
                    f.status = $status,
                    f.updated_at = datetime()
                """,
                uid=uid,
                name=name,
                description=description,
                target_property=target_property,
                target_value=target_value,
                status=status,
            )
        return True
    except Exception as e:
        logger.warning("upsert_formulation failed for %s: %s", name, e)
        return False


def link_formulation_contains(
    formulation_uid: str, compound_uid: str, *, ratio: Optional[float] = None
) -> bool:
    drv = _get_driver()
    if drv is None:
        return False
    try:
        with drv.session() as s:
            s.run(
                """
                MATCH (f:Formulation {uid: $fu}), (c:Compound {uid: $cu})
                MERGE (f)-[r:CONTAINS]->(c)
                SET r.ratio = $ratio
                """,
                fu=formulation_uid,
                cu=compound_uid,
                ratio=ratio,
            )
        return True
    except Exception as e:
        logger.warning("link_formulation_contains failed: %s", e)
        return False


def link_formulation_similarity(form_a: str, form_b: str, score: float = 1.0) -> bool:
    drv = _get_driver()
    if drv is None:
        return False
    try:
        with drv.session() as s:
            s.run(
                """
                MATCH (a:Formulation {uid: $a}), (b:Formulation {uid: $b})
                MERGE (a)-[r:SIMILAR_TO]->(b)
                SET r.score = $score
                """,
                a=form_a,
                b=form_b,
                score=score,
            )
        return True
    except Exception as e:
        logger.warning("link_formulation_similarity failed: %s", e)
        return False


# ----- Read helpers -------------------------------------------------------


def find_similar_compounds(
    compound_uid: str, limit: int = 5
) -> List[Dict[str, Any]]:
    """Return compounds co-occurring in the same formulation."""
    drv = _get_driver()
    if drv is None:
        return []
    try:
        with drv.session() as s:
            rows = s.run(
                """
                MATCH (c:Compound {uid: $uid})<-[:CONTAINS]-(f:Formulation)-[:CONTAINS]->(other:Compound)
                WHERE other.uid <> c.uid
                RETURN other.uid AS uid, other.name AS name, count(*) AS co_count
                ORDER BY co_count DESC
                LIMIT $limit
                """,
                uid=compound_uid,
                limit=limit,
            ).data()
        return list(rows)
    except Exception as e:
        logger.warning("find_similar_compounds failed: %s", e)
        return []


def get_compounds_for_formulation(formulation_uid: str) -> List[Dict[str, Any]]:
    drv = _get_driver()
    if drv is None:
        return []
    try:
        with drv.session() as s:
            rows = s.run(
                """
                MATCH (f:Formulation {uid: $uid})-[:CONTAINS]->(c:Compound)
                RETURN c.uid AS uid, c.name AS name, c.smiles AS smiles,
                       c.cas_number AS cas_number, c.molecular_weight AS molecular_weight
                ORDER BY c.name
                """,
                uid=formulation_uid,
            ).data()
        return list(rows)
    except Exception as e:
        logger.warning("get_compounds_for_formulation failed: %s", e)
        return []


def list_compounds(query: str = "", limit: int = 50) -> List[Dict[str, Any]]:
    """List/search compounds by uid/name/cas/smiles prefix (browse panel)."""
    drv = _get_driver()
    if drv is None:
        return []
    try:
        q = (query or "").strip()
        cypher = """
            MATCH (c:Compound)
            WHERE $q = "" OR toLower(c.name) CONTAINS toLower($q)
               OR toLower(coalesce(c.uid, "")) CONTAINS toLower($q)
               OR toLower(coalesce(c.cas_number, "")) CONTAINS toLower($q)
            RETURN c.uid AS uid, c.name AS name, c.smiles AS smiles,
                   c.cas_number AS cas_number, c.molecular_weight AS molecular_weight,
                   coalesce(c.supplier, "") AS supplier
            ORDER BY c.name
            LIMIT $limit
        """
        with drv.session() as s:
            rows = s.run(cypher, q=q, limit=int(limit)).data()
        return list(rows)
    except Exception as e:
        logger.warning("list_compounds failed: %s", e)
        return []


def list_formulations(limit: int = 50) -> List[Dict[str, Any]]:
    """List formulations (browse panel)."""
    drv = _get_driver()
    if drv is None:
        return []
    try:
        with drv.session() as s:
            rows = s.run(
                """
                MATCH (f:Formulation)
                RETURN f.uid AS uid, f.name AS name,
                       coalesce(f.target_property, "") AS target_property,
                       coalesce(f.target_value, 0) AS target_value,
                       coalesce(f.status, "") AS status
                ORDER BY f.name
                LIMIT $limit
                """,
                limit=int(limit),
            ).data()
        return list(rows)
    except Exception as e:
        logger.warning("list_formulations failed: %s", e)
        return []


def get_stats() -> Dict[str, int]:
    drv = _get_driver()
    if drv is None:
        return {}
    out: Dict[str, int] = {}
    try:
        with drv.session() as s:
            for label in ("Compound", "Formulation", "ExperimentReport"):
                rec = s.run(f"MATCH (n:{label}) RETURN count(n) AS c").single()
                out[label.lower()] = rec["c"] if rec else 0
            for rel in ("CONTAINS", "SIMILAR_TO", "EVALUATES"):
                rec = s.run(f"MATCH ()-[r:{rel}]->() RETURN count(r) AS c").single()
                out[f"{rel.lower()}_rels"] = rec["c"] if rec else 0
        return out
    except Exception as e:
        logger.warning("get_stats failed: %s", e)
        return {}


def healthcheck() -> bool:
    drv = _get_driver()
    if drv is None:
        return False
    try:
        with drv.session() as s:
            s.run("RETURN 1").single()
        return True
    except Exception:
        return False


def close() -> None:
    global _driver, _enabled
    if _driver is not None:
        try:
            _driver.close()
        except Exception:  # pragma: no cover
            pass
        _driver = None
    _enabled = None


# ---- Relationship helpers ----

def upsert_relationship(
    src_uid: str,
    dst_uid: str,
    rel_type: str,
    *,
    confidence: Optional[float] = None,
    evidence: Optional[str] = None,
    extraction_method: Optional[str] = None,
) -> bool:
    """Create or update a relationship between two entities by their uid.
    rel_type should be like 'INHIBITS', 'SYNERGIZES', etc.
    """
    drv = _get_driver()
    if drv is None:
        return False
    try:
        with drv.session() as s:
            s.run(
                """
                MATCH (a:Compound {uid: $src_uid}), (b:Compound {uid: $dst_uid})
                MERGE (a)-[r:REL_TYPE]->(b)
                SET r.confidence = $confidence,
                    r.evidence = $evidence,
                    r.extraction_method = $extraction_method,
                    r.updated_at = datetime()
                """.replace("REL_TYPE", rel_type),
                src_uid=src_uid,
                dst_uid=dst_uid,
                confidence=confidence,
                evidence=evidence,
                extraction_method=extraction_method,
            )
        return True
    except Exception as e:
        logger.warning("upsert_relationship failed for %s-%s: %s", src_uid, dst_uid, e)
        return False


def get_relation_by_name(
    src_name: str,
    dst_name: str,
    rel_type: str,
) -> Optional[Dict[str, Any]]:
    """Return a dict with keys confidence, evidence, extraction_method if relation exists."""
    drv = _get_driver()
    if drv is None:
        return None
    try:
        with drv.session() as s:
            row = s.run(
                """
                MATCH (a:Compound {name: $src_name})-[r:REL_TYPE]->(b:Compound {name: $dst_name})
                RETURN r.confidence AS confidence, r.evidence AS evidence, r.extraction_method AS extraction_method
                """.replace("REL_TYPE", rel_type),
                src_name=src_name,
                dst_name=dst_name,
            ).single()
            if row:
                return {
                    "confidence": row["confidence"],
                    "evidence": row["evidence"],
                    "extraction_method": row["extraction_method"],
                }
            return None
    except Exception as e:
        logger.warning("get_relation_by_name failed for %s-%s: %s", src_name, dst_name, e)
        return None


def has_measured_evidence_for_entity(name: str) -> bool:
    """Check if the entity has any link with extraction_method == 'measured'."""
    drv = _get_driver()
    if drv is None:
        return False
    try:
        with drv.session() as s:
            row = s.run(
                """
                MATCH (a:Compound {name: $name})-[r]->()
                WHERE r.extraction_method = $method
                RETURN count(r) > 0 AS has_measured
                """,
                name=name,
                method="measured",
            ).single()
            return bool(row["has_measured"]) if row else False
    except Exception as e:
        logger.warning("has_measured_evidence_for_entity failed for %s: %s", name, e)
        return False


# ---- Entity resolution helpers for chemical check ----

def resolve_entity_id(material_name: str) -> Optional[str]:
    """Best-effort resolve a free-text material name to a Compound entity uid.
    Neo4j implementation: search by name field (case-insensitive contains).
    Falls back to None if no match.
    """
    drv = _get_driver()
    if drv is None:
        return None
    try:
        with drv.session() as s:
            # Search by name field (case-insensitive contains)
            row = s.run(
                """
                MATCH (c:Compound)
                WHERE toLower(c.name) CONTAINS toLower($name)
                RETURN c.uid AS uid
                LIMIT 1
                """,
                name=material_name,
            ).single()
            return row["uid"] if row else None
    except Exception as e:
        logger.warning("resolve_entity_id failed for %s: %s", material_name, e)
        return None
def get_incompatible_pairs_for(entity_id: str) -> List[Tuple[str, str, str]]:
    """Return [(other_entity_id, relation_type, evidence_sentence), ...] for INHIBITS relations."""
    drv = _get_driver()
    if drv is None:
        return []
    try:
        with drv.session() as s:
            rows = s.run(
                """
                MATCH (a:Compound {uid: $entity_id})-[r:INHIBITS]->(b:Compound)
                WHERE r.confidence >= $min_conf
                RETURN b.uid AS other_id, r.relation_type AS rel_type, r.evidence AS evidence
                """,
                entity_id=entity_id,
                min_conf=0.55,  # Same as _MIN_RELATION_CONFIDENCE
            ).data()
            out: List[Tuple[str, str, str]] = []
            for r in rows:
                # evidence is a list of strings; take first if exists
                sentence = r["evidence"][0] if r["evidence"] and len(r["evidence"]) > 0 else ""
                out.append((r["other_id"], r["rel_type"], sentence))
            return out
    except Exception as e:
        logger.warning("get_incompatible_pairs_for failed for %s: %s", entity_id, e)
        return []


def get_synergy_pairs_for(entity_id: str) -> List[Tuple[str, str, str]]:
    """Return [(other_entity_id, relation_type, evidence_sentence), ...] for SYNERGIZES relations."""
    drv = _get_driver()
    if drv is None:
        return []
    try:
        with drv.session() as s:
            rows = s.run(
                """
                MATCH (a:Compound {uid: $entity_id})-[r:SYNERGIZES]->(b:Compound)
                WHERE r.confidence >= $min_conf
                RETURN b.uid AS other_id, r.relation_type AS rel_type, r.evidence AS evidence
                """,
                entity_id=entity_id,
                min_conf=0.55,
            ).data()
            out: List[Tuple[str, str, str]] = []
            for r in rows:
                sentence = r["evidence"][0] if r["evidence"] and len(r["evidence"]) > 0 else ""
                out.append((r["other_id"], r["rel_type"], sentence))
            return out
    except Exception as e:
        logger.warning("get_synergy_pairs_for failed for %s: %s", entity_id, e)
        return []


def has_measured_evidence(entity_id: str) -> bool:
    """Check if the entity has any link with extraction_method == 'measured'."""
    drv = _get_driver()
    if drv is None:
        return False
    try:
        with drv.session() as s:
            row = s.run(
                """
                MATCH (a:Compound {uid: $entity_id})-[r]->()
                WHERE r.extraction_method = $method
                RETURN count(r) > 0 AS has_measured
                """,
                entity_id=entity_id,
                method="measured",
            ).single()
            return bool(row["has_measured"]) if row else False
    except Exception as e:
        logger.warning("has_measured_evidence failed for %s: %s", entity_id, e)
        return False
