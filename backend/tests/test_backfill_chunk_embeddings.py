"""P0-1: chunk embedding 回填脚本测试。

覆盖 no_proxy 清洗（沙箱 httpx 缺陷 workaround）与回填主流程
（fake 编码器 + 临时库，不下载真实模型）。
"""
from __future__ import annotations

import os
import sqlite3
import sys
import types
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from backfill_chunk_embeddings import _sanitize_no_proxy, main  # noqa: E402


def test_sanitize_no_proxy_drops_bracketed_ipv6(monkeypatch):
    monkeypatch.setenv(
        "no_proxy",
        "localhost,127.0.0.1,::1,[::1],fd8b:4f84:7d32:99::1,[fd8b:4f84:7d32:99::1]",
    )
    monkeypatch.setenv("NO_PROXY", "localhost,[::1]")
    _sanitize_no_proxy()
    assert os.environ["no_proxy"] == "localhost,127.0.0.1,::1,fd8b:4f84:7d32:99::1"
    assert os.environ["NO_PROXY"] == "localhost"
    # 真实回归点: 清洗后 httpx.Client() 可构造 (之前崩 Invalid port: ':1]')
    import httpx

    httpx.Client()


def _fake_st_module(monkeypatch):
    class FakeST:
        def __init__(self, name: str):
            self.dim = 384 if "MiniLM" in name else 512
            self.name = name

        def encode(self, texts, **kwargs):
            return np.array([[0.5] * self.dim for _ in texts], dtype=np.float32)

    mod = types.ModuleType("sentence_transformers")
    mod.SentenceTransformer = FakeST
    monkeypatch.setitem(sys.modules, "sentence_transformers", mod)


def _make_db(path: Path) -> None:
    con = sqlite3.connect(str(path))
    con.execute(
        "CREATE TABLE document_chunks (id INTEGER PRIMARY KEY, text TEXT, "
        "lang TEXT, embedding_blob BLOB, embedding_model TEXT)"
    )
    con.executemany(
        "INSERT INTO document_chunks (text, lang) VALUES (?, ?)",
        [("salt spray 720h", "en"), ("neutral salt spray", "en"), ("中性盐雾试验", "zh")],
    )
    con.commit()
    con.close()


def test_backfill_writes_blob_and_model(tmp_path, monkeypatch):
    _fake_st_module(monkeypatch)
    db = tmp_path / "chunks.db"
    _make_db(db)
    assert main(db_path=db) == 0

    con = sqlite3.connect(str(db))
    rows = con.execute(
        "SELECT lang, embedding_blob, embedding_model FROM document_chunks"
    ).fetchall()
    con.close()
    assert len(rows) == 3
    for lang, blob, model in rows:
        assert blob is not None
        vec = np.frombuffer(bytes(blob), dtype="<f4")
        if lang == "en":
            assert vec.shape == (384,)
            assert model == "sentence-transformers/all-MiniLM-L6-v2"
        else:
            assert vec.shape == (512,)
            assert model == "BAAI/bge-small-zh-v1.5"


def test_backfill_is_rerunnable(tmp_path, monkeypatch):
    """第二次跑: 已回填的行跳过, 返回 0。"""
    _fake_st_module(monkeypatch)
    db = tmp_path / "chunks.db"
    _make_db(db)
    assert main(db_path=db) == 0
    assert main(db_path=db) == 0
    con = sqlite3.connect(str(db))
    n = con.execute(
        "SELECT COUNT(*) FROM document_chunks WHERE embedding_blob IS NOT NULL"
    ).fetchone()[0]
    con.close()
    assert n == 3


def test_backfill_empty_db(tmp_path, monkeypatch):
    _fake_st_module(monkeypatch)
    db = tmp_path / "empty.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE document_chunks (id INTEGER PRIMARY KEY, text TEXT, "
        "lang TEXT, embedding_blob BLOB, embedding_model TEXT)"
    )
    con.commit()
    con.close()
    assert main(db_path=db) == 0
