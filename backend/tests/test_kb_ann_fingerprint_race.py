"""Fingerprint race guard for kb_ann.ensure_index (C-b fix).

Root cause: ensure_index recorded only the post-scan fingerprint (fp_new)
in the manifest and never compared it with the pre-scan fingerprint (fp).
A write landing inside the scan window was therefore paired with the new
fingerprint, and the next ensure_index trusted the stale index.

The fix discards the build when fp != fp_new (or either is uncomputable):
no manifest file, no in-memory buckets; the next ensure_index rebuilds.
"""
from __future__ import annotations

import json
import os

import pytest

faiss = pytest.importorskip("faiss")
np = pytest.importorskip("numpy")

from app.db import chunk_store as cs_mod
from app.db.database import Base, make_engine, make_session_factory
from app.services import kb_ann


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    def _tmpdir():
        d = str(tmp_path / "kb_ann")
        os.makedirs(d, exist_ok=True)
        return d

    monkeypatch.setattr(kb_ann, "_index_dir", _tmpdir)
    kb_ann.invalidate()
    monkeypatch.setattr(cs_mod, "_store", None)
    yield
    kb_ann.invalidate()
    monkeypatch.setattr(cs_mod, "_store", None)


@pytest.fixture()
def tmpdb(tmp_path, monkeypatch):
    import app.db.database as db_mod

    engine = make_engine(f"sqlite:///{tmp_path}/ann_race.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    monkeypatch.setattr(db_mod, "default_session_factory", lambda: factory)
    yield factory


def _normed(dim: int, seed: int) -> list[float]:
    rng = np.random.default_rng(seed)
    v = rng.normal(size=dim).astype(np.float32)
    v /= np.linalg.norm(v)
    return [float(x) for x in v]


def _seed(n: int = 20, dim: int = 32) -> None:
    store = cs_mod.get_chunk_store()
    chunks = [
        {
            "text": f"race chunk {i}",
            "embedding": _normed(dim, 5000 + i),
            "embedding_model": "race-model",
        }
        for i in range(n)
    ]
    store.replace_for_source("race-src", chunks)


def _manifest_path() -> str:
    return os.path.join(kb_ann._index_dir(), "manifest.json")


def test_build_discarded_when_fingerprint_changes_mid_scan(tmpdb, monkeypatch):
    """Simulated write racing the scan: build must be discarded, not trusted."""
    _seed()
    calls: list[str] = []
    real_fp = kb_ann._current_fingerprint

    def _racing_fp():
        calls.append("fp")
        if len(calls) == 1:
            return real_fp()  # pre-scan
        # post-scan: a concurrent write changed count/max_created_at
        fp = real_fp()
        fp = {m: dict(v) for m, v in fp.items()}
        for m in fp:
            fp[m] = dict(fp[m], count=int(fp[m]["count"]) + 1)
        return fp

    monkeypatch.setattr(kb_ann, "_current_fingerprint", _racing_fp)
    res = kb_ann.ensure_index()
    assert res["ready"] is False
    assert "raced" in (res["error"] or "")
    # Nothing persisted: no manifest, no bucket files, no in-memory state.
    assert not os.path.exists(_manifest_path())
    assert kb_ann.index_stats()["loaded"] is False
    # Next ensure_index (no race) rebuilds cleanly.
    monkeypatch.setattr(kb_ann, "_current_fingerprint", real_fp)
    res2 = kb_ann.ensure_index()
    assert res2["ready"] is True
    assert os.path.exists(_manifest_path())


def test_build_succeeds_when_fingerprint_stable(tmpdb):
    _seed()
    res = kb_ann.ensure_index()
    assert res["ready"] is True
    assert os.path.exists(_manifest_path())
    with open(_manifest_path(), encoding="utf-8") as fh:
        manifest = json.load(fh)
    assert manifest["fingerprint"] == kb_ann._current_fingerprint()


def test_build_discarded_when_fingerprint_uncomputable(tmpdb, monkeypatch):
    """Fail-open: an unverifiable post-scan fingerprint must not be trusted."""
    _seed()
    calls: list[str] = []
    real_fp = kb_ann._current_fingerprint

    def _flaky_fp():
        calls.append("fp")
        return real_fp() if len(calls) == 1 else None

    monkeypatch.setattr(kb_ann, "_current_fingerprint", _flaky_fp)
    res = kb_ann.ensure_index()
    assert res["ready"] is False
    assert not os.path.exists(_manifest_path())
