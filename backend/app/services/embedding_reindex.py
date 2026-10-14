"""v29: 通用 embedding 模型重建索引服务。

从 scripts/migrate_to_bge_m3.py 泛化而来，支持任意 catalog 模型的切换。
"""
from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

# 模型维度映射（API 层同样引用这一份）
MODEL_DIMS = {
    "sentence-transformers/all-MiniLM-L6-v2": 384,
    "BAAI/bge-small-zh-v1.5": 512,
    "BAAI/bge-m3": 1024,
    "Qwen/Qwen3-Embedding-0.6B": 1024,
    "moka-ai/m3e-base": 768,
}


def _configured_db_path() -> Path:
    """The SQLite file the app is actually using (``FORMUMIND_DB_URL`` / ``Settings.db_url``)."""
    import os

    from ..config import get_settings

    url = os.environ.get("FORMUMIND_DB_URL") or get_settings().db_url
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        raise RuntimeError(f"嵌入模型重建只支持 SQLite 数据库，当前: {url.split(':', 1)[0]}")
    return Path(url[len(prefix):]).resolve()


def reindex_for_model(
    model_id: str,
    db_path: str | Path | None = None,
    batch: int = 32,
    progress_cb: Callable[[float], None] | None = None,
) -> dict:
    """为指定模型重建全库索引。

    流程：备份 → 加载模型 → 逐批重嵌 → 更新 embedding_model → 校验。
    返回统计 dict。
    """
    db_path = Path(db_path) if db_path is not None else _configured_db_path()
    if not db_path.exists():
        raise FileNotFoundError(f"DB 不存在: {db_path}")

    target_dim = MODEL_DIMS.get(model_id)
    if not target_dim:
        raise ValueError(f"未知模型维度: {model_id}")

    # 1. 备份
    bak = db_path.with_suffix(f".db.bak.{int(time.time())}")
    # sqlite3's online-backup API: a plain file copy of a live WAL-mode database can miss pages still in the -wal file.
    src = sqlite3.connect(str(db_path), timeout=60)
    dst = sqlite3.connect(str(bak))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    logger.info("已备份 DB: %s", bak)
    # C P2-3: 备份 retention —— 只保留最近 3 个，避免磁盘堆积。
    try:
        _baks = sorted(
            db_path.parent.glob(db_path.name + ".bak.*"),
            key=lambda p: p.stat().st_mtime,
        )
        for _old in _baks[:-3]:
            _old.unlink()
            logger.info("清理旧备份: %s", _old)
    except Exception:
        pass

    # 2. 加载模型：先只用本地缓存，缺失再联网（不改进程级 HF_HUB_OFFLINE）
    print(f"加载模型 {model_id} ...")
    from sentence_transformers import SentenceTransformer

    try:
        model = SentenceTransformer(model_id, local_files_only=True)
    except Exception:
        model = SentenceTransformer(model_id)
    dim = model.get_sentence_embedding_dimension()
    if dim != target_dim:
        # 在动任何数据之前拒绝：否则整库会写入维度不符的向量，直到最后的抽查才发现。
        raise RuntimeError(f"模型维度 {dim} 与预期 {target_dim} 不符: {model_id}")

    # 3. 逐批重嵌
    from .kb_ann import vector_to_blob

    con = sqlite3.connect(str(db_path), timeout=60)  # the app keeps writing while we re-embed: wait, don't fail
    try:
        total = con.execute("SELECT COUNT(*) FROM document_chunks").fetchone()[0]
        done = 0
        last_id = ""
        t0 = time.time()
        # C P2-4: 分页读取 —— 全表 fetchall 在 10 万 chunk 级内存爆炸。
        # 按 id 做 keyset 分页：不持有未读完的 SELECT 游标，逐批 UPDATE/COMMIT 不会打断读取。
        while True:
            batch_rows = con.execute(
                "SELECT id, text FROM document_chunks WHERE id > ? ORDER BY id LIMIT ?",
                (last_id, batch),
            ).fetchall()
            if not batch_rows:
                break
            texts = [(r[1] or "")[:8000] for r in batch_rows]
            vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
            con.executemany(
                """UPDATE document_chunks
                   SET embedding_blob=?, embedding_model=?, embedding=NULL
                   WHERE id=?""",
                [
                    (vector_to_blob(vec.astype("float32")), model_id, cid)
                    for (cid, _), vec in zip(batch_rows, vecs)
                ],
            )
            con.commit()
            last_id = batch_rows[-1][0]
            done += len(batch_rows)
            if progress_cb:
                progress_cb(done / total if total else 1.0)
            if done % 320 == 0:
                logger.info("重建进度 %d/%d", done, total)

        # 4. 校验
        migrated = con.execute(
            "SELECT COUNT(*) FROM document_chunks WHERE embedding_model=?", (model_id,)
        ).fetchone()[0]
        null_blob = con.execute(
            "SELECT COUNT(*) FROM document_chunks WHERE embedding_blob IS NULL"
        ).fetchone()[0]
        row = con.execute("SELECT embedding_blob FROM document_chunks LIMIT 1").fetchone()
        if row and row[0]:
            n_dims = len(row[0]) // 4  # float32 little-endian, see vector_to_blob
            if n_dims != target_dim:
                raise RuntimeError(f"维度校验失败: {n_dims} != {target_dim}")
    finally:
        con.close()

    if null_blob != 0:
        raise RuntimeError(f"仍有 {null_blob} 个 NULL blob")
    if migrated != total:
        raise RuntimeError(f"迁移数量不符: {migrated} != {total}")

    el = time.time() - t0
    logger.info("重建完成: %d chunks, %.0fs, 备份 %s", total, el, bak)
    return {"total": total, "migrated": migrated, "elapsed_s": round(el, 1), "backup": str(bak)}
