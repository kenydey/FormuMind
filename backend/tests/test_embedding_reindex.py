"""``reindex_for_model`` must re-embed every chunk, not just the first batch.

The loop used to run its UPDATEs on the cursor that was still paging through the SELECT, which discarded the rest of the
result set: with more rows than one batch, the switch re-embedded 32 chunks and then failed its own ``migrated != total``
check, leaving the corpus half on the new model.
"""
from __future__ import annotations

import sqlite3
import sys
import types

import numpy as np
import pytest

from app.services import embedding_reindex as er

MODEL = "sentence-transformers/all-MiniLM-L6-v2"  # 384 dims in MODEL_DIMS


class _FakeModel:
    def __init__(self, dim: int):
        self.dim = dim

    def get_sentence_embedding_dimension(self) -> int:
        return self.dim

    def encode(self, texts, **_kw):
        return np.ones((len(texts), self.dim), dtype="float32")


def _install_fake(monkeypatch, dim: int, calls: list):
    def factory(name, **kw):
        calls.append(kw)
        return _FakeModel(dim)

    mod = types.ModuleType("sentence_transformers")
    mod.SentenceTransformer = factory
    monkeypatch.setitem(sys.modules, "sentence_transformers", mod)


def _db(tmp_path, n: int):
    path = tmp_path / "formumind.db"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE document_chunks (id TEXT PRIMARY KEY, text TEXT, embedding TEXT,"
        " embedding_blob BLOB, embedding_model TEXT)"
    )
    con.executemany(
        "INSERT INTO document_chunks (id, text, embedding_model) VALUES (?, ?, 'old')",
        [(f"c{i:04d}", f"chunk {i}") for i in range(n)],
    )
    con.commit()
    con.close()
    return path


def test_every_chunk_is_migrated_across_batches(tmp_path, monkeypatch):
    calls: list = []
    _install_fake(monkeypatch, 384, calls)
    path = _db(tmp_path, 70)  # 3 batches of 32
    out = er.reindex_for_model(MODEL, db_path=path, batch=32)
    assert out["total"] == out["migrated"] == 70
    con = sqlite3.connect(path)
    assert con.execute("SELECT COUNT(*) FROM document_chunks WHERE embedding_model=?", (MODEL,)).fetchone()[0] == 70
    assert con.execute("SELECT COUNT(*) FROM document_chunks WHERE embedding_blob IS NULL").fetchone()[0] == 0
    con.close()


def test_the_model_is_loaded_from_the_local_cache_first_without_touching_the_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    calls: list = []
    _install_fake(monkeypatch, 384, calls)
    er.reindex_for_model(MODEL, db_path=_db(tmp_path, 3), batch=32)
    assert calls == [{"local_files_only": True}]
    import os

    assert "HF_HUB_OFFLINE" not in os.environ


def test_a_model_of_the_wrong_dimension_is_rejected_before_any_row_is_written(tmp_path, monkeypatch):
    _install_fake(monkeypatch, 768, [])
    path = _db(tmp_path, 5)
    with pytest.raises(RuntimeError, match="维度"):
        er.reindex_for_model(MODEL, db_path=path, batch=32)
    con = sqlite3.connect(path)
    assert con.execute("SELECT COUNT(*) FROM document_chunks WHERE embedding_model='old'").fetchone()[0] == 5
    con.close()


def test_the_database_is_backed_up_with_sqlite_so_rows_are_not_lost(tmp_path, monkeypatch):
    _install_fake(monkeypatch, 384, [])
    path = _db(tmp_path, 5)
    out = er.reindex_for_model(MODEL, db_path=path, batch=32)
    bak = sqlite3.connect(out["backup"])
    # the backup is a consistent copy of the database as it was before the run
    assert bak.execute("SELECT COUNT(*) FROM document_chunks WHERE embedding_model='old'").fetchone()[0] == 5
    bak.close()


def test_the_default_database_is_the_one_the_app_uses(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUMIND_DB_URL", f"sqlite:///{tmp_path / 'app.db'}")
    assert er._configured_db_path() == (tmp_path / "app.db").resolve()
    monkeypatch.setenv("FORMUMIND_DB_URL", "postgresql://u@h/db")
    with pytest.raises(RuntimeError, match="SQLite"):
        er._configured_db_path()
