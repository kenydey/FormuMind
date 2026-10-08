#!/usr/bin/env python
"""P0-2 (v27): 回填无向量 chunk 的 embedding。

背景：303 个 chunk（180 NULL-model + 123 named-model）没有任何向量
（embedding_blob 与 embedding JSON 皆空），永久 BM25-only；
v25/v26 两轮"查询时回退"对它们无效，必须补数据。

逻辑：
- 有 embedding_model 的行：用其 stored 模型名编码
- NULL-model 的行：按 lang 路由（zh → bge，其余 → 全局默认），与入库路径一致
- 向量写入 embedding_blob（float32 BLOB，经 kb_ann.vector_to_blob）；
  NULL-model 行同时补 embedding_model，使其成为一等向量行
- 模型加载失败的行跳过（保持 BM25），不中断整批

用法：
    cd backend && env -u no_proxy -u NO_PROXY HF_HUB_OFFLINE=1 \
        .venv/bin/python ../scripts/backfill_embeddings.py [--dry-run] [--db PATH]

注意：模型需在本地 HF 缓存（离线加载）；跑之前确认 /tmp 余量。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只统计，不写库")
    ap.add_argument("--db", default=str(BACKEND / "data" / "formumind.db"))
    ap.add_argument("--batch", type=int, default=64)
    args = ap.parse_args()

    from app.services import kb_index
    from app.services.kb_ann import vector_to_blob
    from app.services.rag import embed_model_name

    con = sqlite3.connect(args.db)
    cur = con.cursor()
    cur.execute(
        """SELECT id, text, embedding_model, lang FROM document_chunks
           WHERE embedding_blob IS NULL
             AND (embedding IS NULL OR embedding IN ('', '[]'))"""
    )
    rows = cur.fetchall()
    print(f"no-vector chunks: {len(rows)}")
    if args.dry_run or not rows:
        return 0

    # 按模型分组：named 用 stored 名；NULL 按 lang 路由
    groups: dict[str, list[tuple]] = {}
    for _id, text, mname, lang in rows:
        if not (text or "").strip():
            continue
        model = mname or embed_model_name(lang or "en")
        groups.setdefault(model, []).append((_id, text, mname))

    total_ok, total_fail = 0, 0
    for model, items in groups.items():
        print(f"model {model}: {len(items)} chunks")
        for s in range(0, len(items), args.batch):
            batch = items[s : s + args.batch]
            vecs = kb_index._embed_texts([t for _, t, _ in batch], model)
            if not vecs or len(vecs) != len(batch):
                print(f"  batch {s}: embed failed, skipped ({len(batch)} rows stay BM25)")
                total_fail += len(batch)
                continue
            for (_id, _text, old_mname), vec in zip(batch, vecs):
                if not vec:
                    total_fail += 1
                    continue
                try:
                    blob = vector_to_blob(list(vec))
                except Exception:
                    total_fail += 1
                    continue
                if old_mname:
                    cur.execute(
                        "UPDATE document_chunks SET embedding_blob = ? WHERE id = ?",
                        (blob, _id),
                    )
                else:
                    cur.execute(
                        "UPDATE document_chunks SET embedding_blob = ?, embedding_model = ? WHERE id = ?",
                        (blob, model, _id),
                    )
                total_ok += 1
            con.commit()
            print(f"  batch {s}: ok")
    con.commit()

    cur.execute(
        """SELECT COUNT(*) FROM document_chunks
           WHERE embedding_blob IS NULL
             AND (embedding IS NULL OR embedding IN ('', '[]'))"""
    )
    print(f"done: ok={total_ok} fail={total_fail} remaining={cur.fetchone()[0]}")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
