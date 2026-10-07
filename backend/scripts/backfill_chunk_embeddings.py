#!/usr/bin/env python3
"""P0-1 (2026-10-01): 回填 document_chunks 的 embedding_blob（双语分流）。

背景: 开发库 2284 个 chunks 的 embedding/embedding_blob 全空, 向量检索实际退化
为 BM25, 真实 rerank A/B 被阻塞。

按 lang 分流: en -> sentence-transformers/all-MiniLM-L6-v2 (384d),
zh -> BAAI/bge-small-zh-v1.5 (512d)。写入 embedding_blob (float32 小端 BLOB,
kb_ann BLOB-first 读取) + embedding_model (防混算)。只处理 embedding_blob
IS NULL 的行, 失败可重跑。

沙箱 workaround: 本环境 no_proxy 同时含 `::1` 与 `[::1]`, httpx 解析括号写法
生成 `all://*[::1]` 后构造 URLPattern 即崩
(`httpx.InvalidURL: Invalid port: ':1]'`), 导致任何默认 trust_env 的
httpx.Client() 无法构造, HF 模型下载失败。脚本启动时清洗 no_proxy/NO_PROXY
(去掉 `[...]` 括号项, 保留 httpx 可正确处理的裸写法)。
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DB = Path(__file__).resolve().parent.parent / "data" / "formumind.db"
MODELS = {
    "en": "sentence-transformers/all-MiniLM-L6-v2",
    "zh": "BAAI/bge-small-zh-v1.5",
}
BATCH = 32


def _sanitize_no_proxy() -> None:
    """去掉 no_proxy 中的括号 IPv6 项, 绕过本沙箱 httpx 的解析缺陷。"""
    for key in ("no_proxy", "NO_PROXY"):
        raw = os.environ.get(key, "")
        if not raw:
            continue
        cleaned = ",".join(
            h for h in raw.split(",") if not (h.startswith("[") and h.endswith("]"))
        )
        os.environ[key] = cleaned


def main(db_path: Path | None = None) -> int:
    _sanitize_no_proxy()
    from sentence_transformers import SentenceTransformer

    db = Path(db_path) if db_path else DB
    con = sqlite3.connect(str(db))
    cur = con.cursor()
    total_missing = cur.execute(
        "SELECT COUNT(*) FROM document_chunks WHERE embedding_blob IS NULL"
    ).fetchone()[0]
    print(f"待回填 chunks: {total_missing}", flush=True)
    if not total_missing:
        con.close()
        return 0

    loaded: dict[str, object] = {}
    # v15: per-lang try/except + 结构化摘要（与在线索引一致的 partial-failure 语义）
    summary: dict[str, str] = {}
    t0 = time.time()
    done = 0
    for lang, model_name in MODELS.items():
        rows = cur.execute(
            "SELECT id, text FROM document_chunks "
            "WHERE embedding_blob IS NULL AND lang = ? ORDER BY id",
            (lang,),
        ).fetchall()
        if not rows:
            summary[lang] = "0 行待回填，跳过"
            continue
        if lang not in loaded:
            t1 = time.time()
            try:
                loaded[lang] = SentenceTransformer(model_name)
            except Exception as exc:  # noqa: BLE001
                # v15: 单语言模型加载失败只跳过该语言，不崩脚本
                summary[lang] = f"模型加载失败，跳过 {len(rows)} 行: {exc}"
                print(f"[{lang}] {summary[lang]}", flush=True)
                continue
            print(f"模型 {model_name} 加载 {time.time()-t1:.1f}s", flush=True)
        model = loaded[lang]
        lang_done = 0
        try:
            for i in range(0, len(rows), BATCH):
                batch = rows[i : i + BATCH]
                vecs = model.encode(
                    [t for _, t in batch], batch_size=BATCH, show_progress_bar=False,
                    normalize_embeddings=True,
                )
                for (cid, _), vec in zip(batch, vecs):
                    blob = vec.astype("<f4").tobytes()
                    cur.execute(
                        "UPDATE document_chunks SET embedding_blob = ?, "
                        "embedding_model = ? WHERE id = ?",
                        (blob, model_name, cid),
                    )
                con.commit()
                lang_done += len(batch)
                done += len(batch)
                print(f"  [{lang}] {done}/{total_missing}", flush=True)
        except Exception as exc:  # noqa: BLE001
            summary[lang] = f"编码中断，已回填 {lang_done}/{len(rows)} 行: {exc}"
            print(f"[{lang}] {summary[lang]}", flush=True)
            continue
        summary[lang] = f"回填 {lang_done} 行"

    con.close()
    print(f"回填完成: {done} chunks, 耗时 {time.time()-t0:.1f}s", flush=True)
    for lang, note in summary.items():
        print(f"  [{lang}] {note}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
