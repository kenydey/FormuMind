#!/usr/bin/env python3
"""D1 (2026-09-04): 中文 chunks 换 bge-small-zh-v1.5 重嵌(双语分流)。

只处理 lang='zh' 且 embedding_model 非 bge 的 chunks; 英文库保持 MiniLM
不动。embedding 存归一化 JSON 列表 + embedding_model 更新(comparable_
embedding 按 维度+模型名 防混算)。失败可重跑(按 embedding_model 过滤)。
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
DB = Path("/root/FormuMind/data/formumind.db")
MODEL = "BAAI/bge-small-zh-v1.5"
BATCH = 32


def main() -> int:
    from sentence_transformers import SentenceTransformer

    con = sqlite3.connect(str(DB))
    cur = con.cursor()
    rows = cur.execute(
        "SELECT id, text FROM document_chunks "
        "WHERE lang='zh' AND (embedding_model IS NULL OR embedding_model != ?)",
        (MODEL,),
    ).fetchall()
    print(f"待重嵌中文 chunks: {len(rows)}")
    if not rows:
        con.close()
        return 0

    t0 = time.time()
    model = SentenceTransformer(MODEL)
    print(f"模型加载 {time.time()-t0:.1f}s", flush=True)

    total = len(rows)
    done = 0
    for i in range(0, total, BATCH):
        batch = rows[i : i + BATCH]
        texts = [r[1] or "" for r in batch]
        vecs = model.encode(texts, normalize_embeddings=True, batch_size=BATCH)
        updates = []
        for (cid, _), v in zip(batch, vecs):
            updates.append((json.dumps([float(x) for x in v]), MODEL, cid))
        cur.executemany(
            "UPDATE document_chunks SET embedding=?, embedding_model=? WHERE id=?",
            updates,
        )
        con.commit()
        done += len(batch)
        if done % 512 < BATCH:
            print(f"  {done}/{total} ({time.time()-t0:.0f}s)", flush=True)
    con.close()
    print(f"完成 {done} 条, 耗时 {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
