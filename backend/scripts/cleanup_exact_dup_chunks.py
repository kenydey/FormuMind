#!/usr/bin/env python3
"""One-time cleanup of exact-duplicate KB chunks (2026-10-01).

Finds groups of chunks with identical normalized text (same normalization as
``app/services/kb_dedup.py``: whitespace-collapsed, stripped, lowercased),
keeps ONE chunk per group (earliest created_at, tie-break smallest id),
deletes the rest.

DRY-RUN BY DEFAULT. Pass ``--apply`` to actually delete. Deleting data
requires explicit approval — this script never runs as part of any test or
ingest path.

Usage:
    .venv/bin/python scripts/cleanup_exact_dup_chunks.py [--db PATH] [--apply]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from app.services.kb_dedup import chunk_dedup_key


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/formumind.db")
    ap.add_argument("--apply", action="store_true",
                    help="actually delete; without it, dry-run only")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    rows = con.execute(
        "SELECT id, source_id, created_at, text, heading_path, page_no "
        "FROM document_chunks"
    ).fetchall()
    print(f"total chunks: {len(rows)}")

    groups: dict[str, list[tuple]] = {}
    for r in rows:
        groups.setdefault(chunk_dedup_key(r[3], r[4], r[5]), []).append(r)
    dup_groups = [g for g in groups.values() if len(g) > 1]
    print(f"exact-dup groups: {len(dup_groups)}")

    to_delete: list[str] = []
    for g in sorted(dup_groups, key=len, reverse=True):
        # keep earliest created_at, tie-break smallest id (deterministic)
        ordered = sorted(g, key=lambda r: (r[2] or "", r[0]))
        keep, drop = ordered[0], ordered[1:]
        preview = (keep[3] or "")[:80].replace("\n", " ")
        print(f"  group size {len(g)}: keep {keep[0][:8]} "
              f"(source {str(keep[1])[:8]}), drop {len(drop)} | {preview}")
        to_delete.extend(r[0] for r in drop)

    print(f"chunks to delete: {len(to_delete)} (keeping 1 per group)")
    if not args.apply:
        print("dry-run: nothing deleted. Re-run with --apply to delete.")
        return 0

    confirm = input(f"DELETE {len(to_delete)} chunks from {args.db}? type YES: ")
    if confirm.strip() != "YES":
        print("aborted.")
        return 1
    with con:
        con.executemany("DELETE FROM document_chunks WHERE id = ?", [(i,) for i in to_delete])
    remaining = con.execute("SELECT COUNT(*) FROM document_chunks").fetchone()[0]
    print(f"deleted {len(to_delete)} chunks; remaining: {remaining}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
