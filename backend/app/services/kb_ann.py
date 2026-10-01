"""Phase C-1: process-local faiss ANN index over KB chunk embeddings.

Design notes (all verified against the current retrieval path):

- The KB hybrid path (``hybrid_search_scored``) scans at most
  ``kb_search_scan_limit`` (default 5000) chunks and scores the embedding
  half with brute-force cosine. This module removes that ceiling for the
  *vector* half: one ``faiss.IndexFlatIP`` bucket per ``embedding_model``
  (semantic spaces are never mixed — the same invariant as
  ``kb_index.comparable_embedding``), built over the full non-archived
  corpus.
- Stored vectors are L2-normalised (ingest writes normalised vectors;
  rebuild normalises defensively), so inner product == cosine similarity and
  ``IndexFlatIP`` is *exact*: top-k matches brute-force cosine, which the
  A/B correctness check asserts (``recall@k == 1.0`` on synthetic vectors).
- The index persists under ``<backend>/data/kb_ann/`` (gitignored) plus a
  ``manifest.json``. The manifest records a DB fingerprint (per-model
  count/max_created_at over the indexed population); any chunk
  insert/delete/replace/archive changes the fingerprint, so the next
  ``ensure_index`` rebuilds from the DB instead of serving stale vectors.
  A manifest mismatch (version, fingerprint, model set, dim, row count)
  forces a rebuild — never a silent wrong-space or stale search.
- Everything here is fail-open: any faiss problem (missing package, corrupt
  files, dim mismatch) makes the entry points return ``None``/empty and the
  caller falls back to the pre-C-1 brute-force path. faiss is imported
  lazily, mirroring ``rag.py``.

Vector storage (C-1b): embeddings live in ``document_chunks.embedding``
(JSON) historically and ``document_chunks.embedding_blob`` (float32
little-endian BLOB) going forward. :func:`chunk_vector` reads BLOB first
and falls back to JSON, so a partially backfilled corpus still works.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

_INDEX_DIR_NAME = "kb_ann"
_MANIFEST_NAME = "manifest.json"
# v2: manifest carries a DB fingerprint (per-model count/max_created_at over
# the indexed population). v1 manifests are treated as stale and rebuilt.
_MANIFEST_VERSION = 2

_lock = threading.Lock()
_buckets: dict[str, dict[str, Any]] = {}  # model -> {"index", "ids", "dim"}
_manifest: dict[str, Any] | None = None
_last_error: str | None = None


def _index_dir() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    backend_dir = os.path.dirname(here)  # app/services -> app -> backend
    backend_dir = os.path.dirname(backend_dir)
    d = os.path.join(backend_dir, "data", _INDEX_DIR_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def _faiss():  # lazy import, mirrors rag.py
    try:
        import faiss  # type: ignore

        return faiss
    except Exception as exc:  # noqa: BLE001
        logger.debug("kb_ann faiss unavailable: %s", exc)
        return None


def _numpy():
    try:
        import numpy as np  # type: ignore

        return np
    except Exception as exc:  # noqa: BLE001
        logger.debug("kb_ann numpy unavailable: %s", exc)
        return None


def vector_to_blob(vec: list[float]) -> bytes:
    """Encode a float vector as float32 little-endian bytes (C-1b write path)."""
    np = _numpy()
    if np is None:
        raise RuntimeError("numpy unavailable for vector_to_blob")
    return np.asarray(vec, dtype="<f4").tobytes()


def chunk_vector(chunk: Any):
    """Read a chunk's embedding as a normalised float32 array.

    BLOB-first (``embedding_blob``), JSON-fallback (``embedding``).
    Returns ``None`` when the chunk has no usable vector. Never raises.
    """
    np = _numpy()
    if np is None:
        return None
    try:
        if isinstance(chunk, dict):
            raw = chunk.get("embedding_blob")
            js = chunk.get("embedding")
        else:
            raw = getattr(chunk, "embedding_blob", None)
            js = getattr(chunk, "embedding", None)
        vec = None
        if raw:
            vec = np.frombuffer(bytes(raw), dtype="<f4").astype(np.float32)
        if vec is None or vec.size == 0:
            if not js:
                return None
            vec = np.asarray(js, dtype=np.float32)
        if vec.size == 0:
            return None
        norm = float(np.linalg.norm(vec))
        if norm <= 0:
            return None
        return (vec / norm).astype(np.float32)
    except Exception as exc:  # noqa: BLE001
        logger.debug("kb_ann chunk_vector failed: %s", exc)
        return None


def _manifest_path() -> str:
    return os.path.join(_index_dir(), _MANIFEST_NAME)


def _read_manifest() -> dict[str, Any] | None:
    try:
        with open(_manifest_path(), encoding="utf-8") as fh:
            m = json.load(fh)
        return m if isinstance(m, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _write_manifest(m: dict[str, Any]) -> None:
    tmp = _manifest_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(m, fh)
    os.replace(tmp, _manifest_path())


def _bucket_path(model: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in model)
    return os.path.join(_index_dir(), f"bucket_{safe}.faiss")


def _scan_vectors() -> dict[str, list[tuple[str, Any]]]:
    """Full-corpus vector scan (no 5000 cap): model -> [(chunk_id, vector)]."""
    from ..db.chunk_store import get_chunk_store

    out: dict[str, list[tuple[str, Any]]] = {}
    for row in get_chunk_store().embedded_vectors():
        vec = chunk_vector(row)
        if vec is None:
            continue
        model = row.get("embedding_model") or ""
        out.setdefault(model, []).append((row["id"], vec))
    return out


def _build_from_scan(faiss, np, scan: dict[str, list[tuple[str, Any]]]) -> dict[str, dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for model, items in scan.items():
        if not items:
            continue
        dim = int(items[0][1].shape[0])
        if any(int(v.shape[0]) != dim for _, v in items):
            logger.warning("kb_ann bucket %r has mixed dims, skipping", model)
            continue
        mat = np.stack([v for _, v in items]).astype(np.float32)
        index = faiss.IndexFlatIP(dim)
        index.add(mat)
        faiss.write_index(index, _bucket_path(model))
        buckets[model] = {
            "index": index,
            "ids": [cid for cid, _ in items],
            "dim": dim,
        }
    return buckets


def _manifest_for(
    buckets: dict[str, dict[str, Any]], fingerprint: dict[str, Any]
) -> dict[str, Any]:
    return {
        "version": _MANIFEST_VERSION,
        "built_at": time.time(),
        "fingerprint": fingerprint,
        "buckets": {
            model: {"dim": b["dim"], "count": len(b["ids"])}
            for model, b in buckets.items()
        },
    }


def _current_fingerprint() -> dict[str, Any] | None:
    """DB fingerprint of the indexed population.

    Returns ``None`` when it cannot be computed (fail-open: the caller then
    rebuilds instead of trusting a possibly stale index). Never raises.
    """
    try:
        from ..db.chunk_store import get_chunk_store

        return get_chunk_store().embedded_fingerprint()
    except Exception as exc:  # noqa: BLE001
        logger.debug("kb_ann fingerprint failed: %s", exc)
        return None


def ensure_index() -> dict[str, Any]:
    """Build or load the faiss index. Fail-open: never raises.

    Staleness rule: the index is trusted only while the DB fingerprint
    (per-model count/max_created_at over the indexed population, recorded
    in the manifest at build time) still matches the live database. Any
    chunk insert/delete/replace/archive changes the fingerprint, so the
    next ``ensure_index`` rebuilds from the DB instead of serving stale
    vectors. An uncomputable fingerprint also forces a rebuild attempt.

    Returns ``{"ready": bool, "buckets": {...}, "error": str|None}``.
    """
    global _buckets, _manifest, _last_error
    faiss, np = _faiss(), _numpy()
    if faiss is None or np is None:
        return {"ready": False, "buckets": {}, "error": "faiss/numpy unavailable"}
    with _lock:
        fp = _current_fingerprint()
        fp_ok = (
            fp is not None
            and _manifest is not None
            and _manifest.get("fingerprint") == fp
        )
        if _buckets and fp_ok:
            return {"ready": True, "buckets": _manifest or {}, "error": None}
        if _buckets:
            logger.info(
                "kb_ann fingerprint changed, dropping in-memory index for rebuild"
            )
            _buckets, _manifest = {}, None
        # Try loading persisted buckets first (only when the DB fingerprint
        # matches what the manifest recorded).
        manifest = _read_manifest()
        loaded: dict[str, dict[str, Any]] = {}
        if (
            fp is not None
            and manifest
            and manifest.get("version") == _MANIFEST_VERSION
            and manifest.get("fingerprint") == fp
        ):
            ok = True
            for model, meta in (manifest.get("buckets") or {}).items():
                path = _bucket_path(model)
                sidecar = path + ".ids.json"
                if not (os.path.exists(path) and os.path.exists(sidecar)):
                    ok = False
                    break
                try:
                    index = faiss.read_index(path)
                    with open(sidecar, encoding="utf-8") as fh:
                        ids = json.load(fh)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("kb_ann load failed for %r: %s", model, exc)
                    ok = False
                    break
                if (
                    not isinstance(ids, list)
                    or int(index.ntotal) != len(ids)
                    or int(meta.get("dim", -1)) != int(index.d)
                ):
                    logger.warning("kb_ann sidecar mismatch for %r, rebuilding", model)
                    ok = False
                    break
                loaded[model] = {
                    "index": index,
                    "ids": [str(i) for i in ids],
                    "dim": int(index.d),
                }
            if ok and loaded:
                _buckets = loaded
                _manifest = manifest
                return {"ready": True, "buckets": manifest, "error": None}
            # Fall through to rebuild on any load problem.
        elif manifest and manifest.get("version") != _MANIFEST_VERSION:
            logger.info(
                "kb_ann manifest version %r != %r, rebuilding",
                manifest.get("version"),
                _MANIFEST_VERSION,
            )
        # (Re)build from the database. ids live only in the manifest sidecar.
        try:
            scan = _scan_vectors()
        except Exception as exc:  # noqa: BLE001
            _last_error = f"vector scan failed: {exc}"
            logger.warning("kb_ann %s", _last_error)
            return {"ready": False, "buckets": {}, "error": _last_error}
        buckets = _build_from_scan(faiss, np, scan)
        if not buckets:
            _last_error = "no embeddable vectors in corpus"
            return {"ready": False, "buckets": {}, "error": _last_error}
        # Race guard: compare the pre-scan fingerprint with the post-scan
        # one. Any write landing inside the scan window changes count or
        # max_created_at, so fp != fp_new proves the scanned vectors no
        # longer match the DB. Discard the build (no manifest, no files)
        # and let the next ensure_index rebuild — never trust a raced index.
        fp_new = _current_fingerprint()
        if fp is None or fp_new is None or fp_new != fp:
            _last_error = "build raced with concurrent DB write, discarded"
            logger.info("kb_ann %s", _last_error)
            return {"ready": False, "buckets": {}, "error": _last_error}
        manifest = _manifest_for(buckets, fp_new)
        # Persist chunk-id sidecars alongside each bucket (faiss files don't
        # carry ids; the sidecar is what makes load possible).
        for model, b in buckets.items():
            sidecar = _bucket_path(model) + ".ids.json"
            tmp = sidecar + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(b["ids"], fh)
            os.replace(tmp, sidecar)
        _write_manifest(manifest)
        _buckets, _manifest, _last_error = buckets, manifest, None
        return {"ready": True, "buckets": manifest, "error": None}


def search(model: str, query_vec: list[float], k: int) -> list[tuple[str, float]] | None:
    """Top-k ANN search in one model bucket.

    Returns ``[(chunk_id, cosine_score)]`` or ``None`` when the index is not
    usable (caller falls back to brute force). Never raises.
    """
    np = _numpy()
    if np is None:
        return None
    with _lock:
        bucket = _buckets.get(model or "")
    if not bucket:
        return None
    try:
        qv = np.asarray(query_vec, dtype=np.float32)
        if int(qv.shape[0]) != int(bucket["dim"]):
            return None
        norm = float(np.linalg.norm(qv))
        if norm <= 0:
            return None
        qv = (qv / norm).astype(np.float32).reshape(1, -1)
        index = bucket["index"]
        kk = max(1, min(int(k), int(index.ntotal)))
        scores, idx = index.search(qv, kk)
        ids = bucket["ids"]
        out: list[tuple[str, float]] = []
        for s, i in zip(scores[0].tolist(), idx[0].tolist()):
            if 0 <= int(i) < len(ids):
                out.append((ids[int(i)], float(s)))
        return out
    except Exception as exc:  # noqa: BLE001
        logger.debug("kb_ann search failed: %s", exc)
        return None


def invalidate() -> None:
    """Drop the in-memory index (forces rebuild on next ensure_index)."""
    global _buckets, _manifest, _last_error
    with _lock:
        _buckets, _manifest, _last_error = {}, None, None


def index_stats() -> dict[str, Any]:
    """Observability snapshot for /api/ops/kb-health (fail-open)."""
    with _lock:
        buckets = {
            model: {"dim": b["dim"], "count": len(b["ids"])}
            for model, b in _buckets.items()
        }
        err = _last_error
    files = []
    try:
        d = _index_dir()
        files = sorted(f for f in os.listdir(d) if f.endswith(".faiss"))
    except Exception:  # noqa: BLE001
        pass
    return {
        "loaded": bool(buckets),
        "buckets": buckets,
        "persisted_files": files,
        "last_error": err,
    }
