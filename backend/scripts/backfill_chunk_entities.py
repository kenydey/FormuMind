#!/usr/bin/env python3
"""P2: 回填 document_chunks.meta.chem（存量 chunk 在 _attach_entities
上线前索引，meta 为空，导致 entity boost / 化学检索增强全部空转）。

extract_entities 是纯规则提取（CAS/分子式/牌号/SMILES），无 ML 开销，
2087 chunks 秒级完成。幂等：已有 chem 的行跳过。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

for key in ("no_proxy", "NO_PROXY"):
    raw = os.environ.get(key, "")
    if raw:
        os.environ[key] = ",".join(p for p in raw.split(",") if "[" not in p)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DB = Path(__file__).resolve().parent.parent / "data" / "formumind.db"


def main() -> int:
    from app.services.chem_extract import extract_entities

    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT id, text, meta FROM document_chunks"
    ).fetchall()
    total = len(rows)
    updated = 0
    skipped = 0
    for r in rows:
        try:
            meta = json.loads(r["meta"]) if r["meta"] else {}
        except Exception:
            meta = {}
        if meta.get("chem"):
            skipped += 1
            continue
        ent = extract_entities(r["text"] or "")
        if not ent:
            skipped += 1
            continue
        meta.update(ent)
        con.execute(
            "UPDATE document_chunks SET meta = ? WHERE id = ?",
            (json.dumps(meta, ensure_ascii=False), r["id"]),
        )
        updated += 1
        if updated % 500 == 0:
            con.commit()
            print(f"  ...{updated}/{total}")
    con.commit()
    n_chem = con.execute(
        "SELECT count(*) FROM document_chunks "
        "WHERE json_extract(meta, '$.chem') IS NOT NULL "
        "AND json_array_length(json_extract(meta, '$.chem')) > 0"
    ).fetchone()[0]
    con.close()
    print(f"回填完成：{updated} 行写入 chem，{skipped} 行跳过（已有/无实体）")
    print(f"现存带 chem 的 chunk：{n_chem}/{total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
