"""v29: 通用 embedding 模型重建索引服务。

从 scripts/migrate_to_bge_m3.py 泛化而来，支持任意 catalog 模型的切换。
"""
from __future__ import annotations

import logging
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

# 模型维度映射（与 API 一致）
_MODEL_DIMS = {
    "sentence-transformers/all-MiniLM-L6-v2": 384,
    "BAAI/bge-small-zh-v1.5": 512,
    "BAAI/bge-m3": 1024,
    "Qwen/Qwen3-Embedding-0.6B": 1024,
    "moka-ai/m3e-base": 768,
}


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
    from ..config import get_settings

    settings = get_settings()
    # DB 路径：默认从 settings 或 backend/data/formumind.db
    if db_path is None:
        # 尝试从 settings 获取，或用默认
        db_path = Path(__file__).resolve().parent.parent.parent / "data" / "formumind.db"
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"DB 不存在: {db_path}")

    target_dim = _MODEL_DIMS.get(model_id)
    if not target_dim:
        raise ValueError(f"未知模型维度: {model_id}")

    # 1. 备份
    bak = db_path.with_suffix(f".db.bak.{int(time.time())}")
    shutil.copy2(db_path, bak)
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

    # 2. 加载模型（离线优先，失败则在线）
    import os

    # 先尝试离线（已缓存），失败则允许下载
    print(f"加载模型 {model_id} ...")
    from sentence_transformers import SentenceTransformer

    try:
        os.environ["HF_HUB_OFFLINE"] = "1"
        model = SentenceTransformer(model_id)
    except Exception:
        # 离线失败，尝试在线下载
        os.environ.pop("HF_HUB_OFFLINE", None)
        model = SentenceTransformer(model_id)
    dim = model.get_sentence_embedding_dimension()
    if dim != target_dim:
        logger.warning("模型维度 %d 与预期 %d 不符", dim, target_dim)

    # 3. 逐批重嵌
    from .kb_ann import blob_to_vector, vector_to_blob

    con = sqlite3.connect(str(db_path))
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM document_chunks")
    total = cur.fetchone()[0]
    # C P2-4: 分页读取 —— 全表 fetchall 在 10 万 chunk 级内存爆炸。
    # ORDER BY id 保证分页稳定。
    cur.execute("SELECT id, text FROM document_chunks ORDER BY id")

    done = 0
    t0 = time.time()
    while True:
        batch_rows = cur.fetchmany(batch)
        if not batch_rows:
            break
        texts = [(r[1] or "")[:8000] for r in batch_rows]
        vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        for (cid, _), vec in zip(batch_rows, vecs):
            blob = vector_to_blob(vec.astype("float32"))
            cur.execute(
                """UPDATE document_chunks
                   SET embedding_blob=?, embedding_model=?, embedding=NULL
                   WHERE id=?""",
                (blob, model_id, cid),
            )
        con.commit()
        done += len(batch_rows)
        if progress_cb:
            progress_cb(done / total if total else 1.0)
        if done % 320 == 0:
            logger.info("重建进度 %d/%d", done, total)

    # 4. 校验
    cur.execute(
        "SELECT COUNT(*) FROM document_chunks WHERE embedding_model=?", (model_id,)
    )
    migrated = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM document_chunks WHERE embedding_blob IS NULL")
    null_blob = cur.fetchone()[0]
    # 抽查维度
    cur.execute("SELECT embedding_blob FROM document_chunks LIMIT 1")
    row = cur.fetchone()
    if row and row[0]:
        v = blob_to_vector(row[0])
        if len(v) != target_dim:
            raise RuntimeError(f"维度校验失败: {len(v)} != {target_dim}")
    con.close()

    if null_blob != 0:
        raise RuntimeError(f"仍有 {null_blob} 个 NULL blob")
    if migrated != total:
        raise RuntimeError(f"迁移数量不符: {migrated} != {total}")

    el = time.time() - t0
    logger.info("重建完成: %d chunks, %.0fs, 备份 %s", total, el, bak)
    return {"total": total, "migrated": migrated, "elapsed_s": round(el, 1), "backup": str(bak)}
