"""Ingest-time near-duplicate dedup for KB chunks (2026-10-01).

Two layers, applied to chunk rows AFTER embedding and BEFORE
``ChunkStore.replace_for_source[_in]``:

- L1 exact: (normalized text, heading_path, page_no) already present under
  another source → skip. Provenance is part of the key: same-document
  chunks may share identical text across sections and must be kept.
  Zero false-positive risk. Default ON (``kb_dedup_exact_enabled``).
- L2 near: same-language embedding cosine >= ``kb_near_dedup_threshold``
  against live DB vectors → skip, or audit-log only when
  ``kb_near_dedup_audit`` is on and ``kb_near_dedup_enabled`` is off.
  Default OFF (opt-in), mirroring the conservative convention used for
  rerank / children retrieval.

Wire-in points (all three chunk writers):

- ``services/kb_index.index_source`` — after embed, before ``replace_for_source``
- ``services/ingest_tx.ingest_document_tx`` — before ``replace_for_source_in``
- ``services/wiki/embed.py`` — before ``replace_for_source_in``

Fail-open everywhere: any DB / numpy failure returns the rows unchanged and
only logs. Skips are reported via ``record_gate_drop("ingest",
"dedup_exact" / "dedup_near")`` so ``/api/kb/stats`` can see them.

Deliberately NOT inside ``ChunkStore``: the store stays dumb, no vector math
in the DB layer. Deliberately NOT inside ``gate_ingest_rows``: the gate runs
pre-embedding, while L2 needs the row vectors.
"""

from __future__ import annotations

import contextvars
import hashlib
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_WS_RE = re.compile(r"\s+")

# Side channel for callers that must tell "every chunk already exists under
# another source" apart from a genuine zero-chunk failure (both make
# ``index_source`` return 0). A context variable keeps ``index_source``'s
# signature intact; the consumer resets it right before the call and reads it
# right after, in the same thread, so a stale value cannot leak across calls.
_ALL_CHUNKS_DUPLICATE: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "kb_dedup_all_chunks_duplicate", default=False
)


def reset_all_duplicate_flag() -> None:
    _ALL_CHUNKS_DUPLICATE.set(False)


def all_chunks_were_duplicates() -> bool:
    """True when the last L1 pass in this context dropped every chunk."""
    return _ALL_CHUNKS_DUPLICATE.get()


def normalize_text(text: str | None) -> str:
    """Canonical form for the L1 exact-dup key."""
    return _WS_RE.sub(" ", (text or "").strip().lower())


def chunk_dedup_key(text: str | None, heading_path: str | None, page_no: Any) -> str:
    """L1 dedup key: normalized text + provenance.

    Same-document chunks may share identical text across sections/pages
    (different ``heading_path`` / ``page_no``) — those are legitimate and
    must be kept. Only text+provenance-identical rows under a *different*
    source count as duplicates.
    """
    canon = "\x1f".join(
        [normalize_text(text), normalize_text(heading_path), str(page_no or "")]
    )
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _l1_exact(
    rows: list[dict],
    source_id: str,
    session,
    settings,
) -> list[dict]:
    """Drop rows whose (text, provenance) already exists under another source.

    P2: 走 ``dedup_key`` 索引 —— 先算出来入库行的 key，再
    ``WHERE dedup_key IN (...)`` 查（0042 迁移加列+索引+回填），
    不再全表扫描。
    """
    if not rows:
        return rows
    try:
        from ..db.models import DocumentChunk

        wanted = {
            chunk_dedup_key(
                row.get("text"), row.get("heading_path"), row.get("page_no")
            )
            for row in rows
        }
        wanted.discard(None)
        existing_keys: set[str] = set()
        if wanted:
            # IN 切片：SQLite 变量上限 999，入库行数通常远小于此，仍做保护。
            keys = sorted(wanted)
            for i in range(0, len(keys), 500):
                batch = keys[i : i + 500]
                for (k,) in (
                    session.query(DocumentChunk.dedup_key)
                    .filter(
                        DocumentChunk.dedup_key.in_(batch),
                        DocumentChunk.source_id != source_id,
                    )
                    .all()
                ):
                    if k:
                        existing_keys.add(k)
            # 兜底：dedup_key 为 NULL 的历史行（0042 回填失败/直接插库），
            # 按旧方式在 Python 侧比对 —— 正常库中这类行接近于零。
            for text, heading_path, page_no in (
                session.query(
                    DocumentChunk.text,
                    DocumentChunk.heading_path,
                    DocumentChunk.page_no,
                )
                .filter(
                    DocumentChunk.source_id != source_id,
                    DocumentChunk.dedup_key.is_(None),
                )
                .yield_per(1000)
            ):
                existing_keys.add(chunk_dedup_key(text, heading_path, page_no))

        out: list[dict] = []
        dropped = 0
        for row in rows:
            if chunk_dedup_key(row.get("text"), row.get("heading_path"), row.get("page_no")) in existing_keys:
                dropped += 1
                continue
            out.append(row)
        if dropped and not out:
            _ALL_CHUNKS_DUPLICATE.set(True)
        if dropped:
            from .kb_retrieval_gate import record_gate_drop

            record_gate_drop("ingest", "dedup_exact", dropped)
            logger.info(
                "kb dedup L1: dropped %d/%d exact-dup chunks for source %s",
                dropped,
                len(rows),
                source_id,
            )
        return out
    except Exception as exc:  # noqa: BLE001 — fail-open, never block ingest
        logger.warning("kb dedup L1 failed for source %s: %s", source_id, exc)
        return rows


def _row_lang(row: dict) -> str:
    lang = (row.get("lang") or "").strip().lower()
    if lang:
        return lang
    try:
        from ..db.chunk_store import _detect_chunk_lang

        return _detect_chunk_lang(row.get("text") or "")
    except Exception:  # noqa: BLE001
        return "en"


def _l2_near(
    rows: list[dict],
    source_id: str,
    session,
    settings,
    mark_needs_l2: bool = True,
) -> list[dict]:
    """Drop/audit rows whose embedding is >= threshold cosine to a live chunk.

    v16 P2-6: 无向量行打标 needs_l2_review —— backfill 补向量后应重跑 L2，
    否则近重复行永久漏检。
    """
    enforce = bool(getattr(settings, "kb_near_dedup_enabled", False))
    audit = bool(getattr(settings, "kb_near_dedup_audit", True))
    if not (enforce or audit):
        return rows
    try:
        threshold = float(getattr(settings, "kb_near_dedup_threshold", 0.98))
    except (TypeError, ValueError):
        threshold = 0.98

    # Rows carrying an embedding vector, grouped by language.
    indexed: list[tuple[int, str, Any]] = []
    for i, row in enumerate(rows):
        vec = row.get("embedding")
        if not vec:
            # v16 P2-6: 无向量行打标，backfill 后重跑 L2。
            # v17 CI-2: embed=False 显式禁用时不打标 —— 行本就不该有向量，
            # 打标会破坏 FORMUMIND_CHEM_EXTRACT_ENABLED=false 时 meta is None 的契约。
            if mark_needs_l2:
                meta = row.get("meta") or {}
                if isinstance(meta, dict):
                    meta["needs_l2_review"] = True
                    row["meta"] = meta
            continue
        indexed.append((i, _row_lang(row), vec))
    if not indexed:
        return rows

    try:
        import numpy as _np
    except ImportError:
        logger.debug("kb dedup L2 skipped: numpy unavailable")
        return rows

    try:
        from ..db.models import DocumentChunk

        # Live DB vectors per language (BLOB-first, JSON fallback), excluding
        # this source's own (about-to-be-replaced) rows.
        db_vecs: dict[str, list[tuple[str, _np.ndarray]]] = {}
        q = (
            session.query(
                DocumentChunk.id,
                DocumentChunk.source_id,
                DocumentChunk.lang,
                DocumentChunk.embedding_blob,
                DocumentChunk.embedding,
            )
            .filter(DocumentChunk.source_id != source_id)
            .yield_per(1000)
        )
        for cid, _sid, lang, blob, js in q:
            raw = None
            if blob:
                try:
                    raw = _np.frombuffer(bytes(blob), dtype="<f4").astype(_np.float32)
                except Exception:  # noqa: BLE001
                    raw = None
            if raw is None or raw.size == 0:
                if not js:
                    continue
                try:
                    raw = _np.asarray(js, dtype=_np.float32)
                except Exception:  # noqa: BLE001
                    continue
            if raw.size == 0:
                continue
            n = float(_np.linalg.norm(raw))
            if n <= 0:
                continue
            key = (lang or "en").strip().lower() or "en"
            db_vecs.setdefault(key, []).append((cid, (raw / n).astype(_np.float32)))

        drop_idx: set[int] = set()
        for i, lang, vec in indexed:
            pop = db_vecs.get(lang)
            if not pop:
                continue
            try:
                qv = _np.asarray(vec, dtype=_np.float32)
            except Exception:  # noqa: BLE001
                continue
            n = float(_np.linalg.norm(qv))
            if n <= 0:
                continue
            qv = qv / n
            mat = _np.stack([v for _, v in pop])
            if mat.shape[1] != qv.shape[0]:
                continue  # dim mismatch — different model bucket, skip safely
            sims = mat @ qv
            best = int(_np.argmax(sims))
            score = float(sims[best])
            if score >= threshold:
                match_id = pop[best][0]
                if enforce:
                    drop_idx.add(i)
                if enforce or audit:
                    logger.info(
                        "kb dedup L2 %s: source=%s lang=%s cosine=%.4f "
                        "matches chunk %s (threshold %.3f)",
                        "drop" if enforce else "audit",
                        source_id,
                        lang,
                        score,
                        match_id,
                        threshold,
                    )
        if drop_idx and enforce:
            from .kb_retrieval_gate import record_gate_drop

            record_gate_drop("ingest", "dedup_near", len(drop_idx))
            logger.info(
                "kb dedup L2: dropped %d/%d near-dup chunks for source %s",
                len(drop_idx),
                len(rows),
                source_id,
            )
        return [r for i, r in enumerate(rows) if i not in drop_idx]
    except Exception as exc:  # noqa: BLE001 — fail-open, never block ingest
        logger.warning("kb dedup L2 failed for source %s: %s", source_id, exc)
        return rows


def dedupe_chunk_rows(
    rows: list[dict],
    source_id: str,
    session=None,
    *,
    skip_l1: bool = False,
    mark_needs_l2: bool = True,
) -> list[dict]:
    """Apply L1 (+L2) ingest-time dedup. Returns the kept rows.

    ``session`` may be a caller-owned SQLAlchemy session (ingest_tx / wiki
    paths); when omitted a short-lived read session is opened. The session
    is never committed here — callers own their transactions.

    v16 P3-17: skip_l1=True 时仅做 L2（L1 已在 embedding 前做过）。
    """
    if not rows:
        return rows
    from ..config import get_settings

    settings = get_settings()
    own_session = session is None
    try:
        if own_session:
            # Use the chunk store's own factory so dedup reads the same
            # database the store writes (matters for tests that swap the
            # store for an isolated temp DB).
            from ..db.chunk_store import get_chunk_store

            session = get_chunk_store().session_factory()
        if not skip_l1 and bool(getattr(settings, "kb_dedup_exact_enabled", True)):
            rows = _l1_exact(rows, source_id, session, settings)
            if not rows:
                return rows
        rows = _l2_near(rows, source_id, session, settings, mark_needs_l2=mark_needs_l2)
        return rows
    finally:
        if own_session and session is not None:
            try:
                session.close()
            except Exception:  # noqa: BLE001
                pass


def l1_dedupe_chunk_rows(
    rows: list[dict],
    source_id: str,
    session=None,
) -> list[dict]:
    """v16 P3-17: 仅做 L1 exact-dedupe（embedding 之前调用，省向量计算）。

    L2 near-dedupe 仍需向量，保留在 embedding 之后。
    """
    if not rows:
        return rows
    from ..config import get_settings

    settings = get_settings()
    own_session = session is None
    try:
        if own_session:
            from ..db.chunk_store import get_chunk_store

            session = get_chunk_store().session_factory()
        if bool(getattr(settings, "kb_dedup_exact_enabled", True)):
            rows = _l1_exact(rows, source_id, session, settings)
        return rows
    finally:
        if own_session and session is not None:
            try:
                session.close()
            except Exception:  # noqa: BLE001
                pass
