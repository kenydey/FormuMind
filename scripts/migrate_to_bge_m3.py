#!/usr/bin/env python
"""v29 Phase2: 全库重建索引 —— 迁移到统一多语言向量空间（bge-m3）。

背景：双语分流（zh→bge-small-zh 512d / en→MiniLM 384d）导致跨语言余弦不可比，
是中文跨语言召回 0.875 vs 1.0 的结构性根因。统一为 bge-m3（1024d，多语言）
后，中英文在同一向量空间可直接 cosine，无需翻译桥接。

前置条件：
1. 本机已设置 FORMUMIND_EMBEDDING_MODEL=BAAI/bge-m3
2. bge-m3 已在本地 HF 缓存（~2GB，首次需联网下载）
3. 已备份数据库（脚本自动备份）

用法：
    cd backend && env -u no_proxy -u NO_PROXY \
        FORMUMIND_EMBEDDING_MODEL=BAAI/bge-m3 \
        .venv/bin/python ../scripts/migrate_to_bge_m3.py [--dry-run] [--db PATH]

流程：
1. 备份 DB 到 {db}.bak.{timestamp}
2. 加载 bge-m3（离线，HF_HUB_OFFLINE=1）
3. 逐批重嵌所有 chunks（text → 1024d 向量 → embedding_blob）
4. 更新 embedding_model='BAAI/bge-m3'，清空旧 embedding JSON（维度不兼容）
5. 验证：抽查向量维度一致性 + 数量对账

回滚：用备份文件恢复 + 取消环境变量。
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

TARGET_MODEL = "BAAI/bge-m3"
TARGET_DIM = 1024


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只统计，不写库")
    ap.add_argument("--db", default=str(BACKEND / "data" / "formumind.db"))
    ap.add_argument("--batch", type=int, default=32)
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"DB 不存在: {db_path}", file=sys.stderr)
        return 1

    # 1. 统计
    con = sqlite3.connect(str(db_path))
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM document_chunks")
    total = cur.fetchone()[0]
    cur.execute(
        "SELECT embedding_model, COUNT(*) FROM document_chunks GROUP BY embedding_model"
    )
    by_model = cur.fetchall()
    print(f"总 chunks: {total}")
    for m, c in by_model:
        print(f"  {m}: {c}")

    if args.dry_run:
        print(f"[dry-run] 需重嵌 {total} chunks → {TARGET_MODEL} ({TARGET_DIM}d)")
        return 0

    # 2. 备份
    bak = db_path.with_suffix(f".db.bak.{int(time.time())}")
    shutil.copy2(db_path, bak)
    print(f"已备份: {bak}")

    # 3. 加载模型（离线）
    import os

    os.environ["HF_HUB_OFFLINE"] = "1"
    print(f"加载模型 {TARGET_MODEL} ...")
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(TARGET_MODEL)
    dim = model.get_sentence_embedding_dimension()
    assert dim == TARGET_DIM, f"维度不符: {dim} != {TARGET_DIM}"
    print(f"模型加载成功，维度 {dim}")

    # 4. 逐批重嵌
    from app.services.kb_ann import vector_to_blob

    cur.execute("SELECT id, text FROM document_chunks ORDER BY id")
    rows = cur.fetchall()
    done = 0
    t0 = time.time()
    for i in range(0, len(rows), args.batch):
        batch = rows[i : i + args.batch]
        texts = [(r[1] or "")[:8000] for r in batch]  # 截断超长文本
        vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        for (cid, _), vec in zip(batch, vecs):
            blob = vector_to_blob(vec.astype("float32"))
            cur.execute(
                """UPDATE document_chunks
                   SET embedding_blob=?, embedding_model=?, embedding=NULL
                   WHERE id=?""",
                (blob, TARGET_MODEL, cid),
            )
        con.commit()
        done += len(batch)
        el = time.time() - t0
        print(f"  {done}/{total} ({done/total*100:.1f}%) {el:.0f}s", flush=True)

    # 5. 验证
    cur.execute(
        "SELECT COUNT(*) FROM document_chunks WHERE embedding_model=?", (TARGET_MODEL,)
    )
    migrated = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM document_chunks WHERE embedding_blob IS NULL")
    null_blob = cur.fetchone()[0]
    print(f"\n迁移完成: {migrated}/{total} 已切 {TARGET_MODEL}")
    print(f"NULL blob: {null_blob}（应为 0）")
    # 抽查维度
    cur.execute("SELECT embedding_blob FROM document_chunks LIMIT 1")
    blob = cur.fetchone()[0]
    from app.services.kb_ann import blob_to_vector

    v = blob_to_vector(blob)
    print(f"抽查向量维度: {len(v)}（应为 {TARGET_DIM}）")
    assert len(v) == TARGET_DIM, "维度校验失败！"
    assert null_blob == 0, "仍有 NULL blob！"
    print("✓ 全部校验通过")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
