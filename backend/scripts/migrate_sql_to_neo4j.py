#!/usr/bin/env python
"""Migrate SQLite KG entities → Neo4j.

Run *after* Neo4j is reachable. Idempotent (MERGE on uid).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db.entity_store import get_entity_store  # noqa: E402
from app.services import neo4j_kg  # noqa: E402

logger = logging.getLogger("migrate_sql_to_neo4j")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=0, help="Optional cap on entities")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if not neo4j_kg.is_enabled():
        logger.error(
            "Neo4j adapter disabled — set FORMUMIND_NEO4J_ENABLED=true and "
            "FORMUMIND_NEO4J_URI / USER / PASSWORD."
        )
        return 2

    if not neo4j_kg.healthcheck():
        logger.error("Neo4j unreachable — aborting migration.")
        return 3

    if not neo4j_kg.ensure_schema():
        logger.error("Failed to ensure Neo4j schema (constraints).")
        return 4

    from sqlalchemy import select

    from app.db.models import KGEntity  # noqa: E402

    store = get_entity_store()
    with store._session_factory() as session:  # type: ignore[attr-defined]
        stmt = select(KGEntity).limit(args.limit or 10_000)
        rows = session.execute(stmt).scalars().all()
        entities = [
            {
                "id": r.id,
                "canonical_name": r.canonical_name,
                "zh_name": r.zh_name,
                "cas_no": r.cas_no,
                "smiles": r.smiles,
                "supplier": r.supplier,
                "generic_name_hint": r.generic_name_hint,
            }
            for r in rows
        ]
    logger.info("Loaded %d SQLite KG entities", len(entities))

    ok = 0
    skipped = 0
    for e in entities:
        try:
            # `e` is a dict from entity_store
            uid = str(e.get("id") or e.get("canonical_name") or "")
            name = e.get("canonical_name") or e.get("zh_name") or "(unnamed)"
            if not uid:
                skipped += 1
                continue
            success = neo4j_kg.upsert_compound(
                uid=uid,
                name=name,
                cas_number=e.get("cas_no") or None,
                smiles=e.get("smiles") or None,
                molecular_weight=None,
                supplier=e.get("supplier") or None,
                notes=(e.get("generic_name_hint") or "")[:500] or None,
            )
            if success:
                ok += 1
            else:
                skipped += 1
        except Exception as exc:  # pragma: no cover
            logger.warning("Skip entity due to %s: %s", exc, e)
            skipped += 1

    stats = neo4j_kg.get_stats()
    logger.info(
        "Migration complete: ok=%d skipped=%d | neo4j stats=%s",
        ok,
        skipped,
        stats,
    )
    neo4j_kg.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
