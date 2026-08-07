"""Persistent knowledge-base index — chunk, embed and retrieve across restarts.

Every ingested/fetched SourceDocument gets structure-aware chunk rows in
``document_chunks`` (see ``db.chunk_store``).  When sentence-transformers is
installed the chunks carry normalized embeddings, so chat retrieval is true
semantic search over the *whole* accumulated corpus — not just the sources a
client happens to send with one request.  Without embeddings, retrieval
degrades to token-overlap scoring over the same rows.

Gated by ``FORMUMIND_KB_V2_ENABLED`` (default on; pure-local, no network).
"""
from __future__ import annotations

import logging
import re

from ..config import get_settings
from ..domain.schemas import Evidence
from .errors import degrade_return

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]")


def kb_enabled() -> bool:
    return get_settings().kb_v2_enabled


# ── embedding helpers (optional sentence-transformers) ───────────────────────


def _embed_texts(texts: list[str]) -> list[list[float]] | None:
    """Normalized embeddings via the shared rag model, or None when unavailable."""
    if not texts:
        return []
    try:
        from .rag import _load_model, embed_model_name

        model = _load_model(embed_model_name())
        vectors = model.encode(texts, normalize_embeddings=True)
        return [[float(x) for x in v] for v in vectors]
    except Exception as exc:
        return degrade_return(logger, exc, "kb embedding unavailable", None)


def _embed_model_name() -> str:
    from .rag import embed_model_name

    return embed_model_name()


def _dot(a: list[float], b: list[float]) -> float:
    """Dot product of two equal-length, already-normalised vectors.

    Callers must have established comparability first (see
    ``comparable_embedding``) — this deliberately does not tolerate a length
    mismatch, because the tolerant version is what silently produced garbage
    scores.

    Kept in plain Python after measuring: at 5000 chunks, numpy per-row is
    ~68→62 ms at 384 dims and ~276→232 ms at 1536, and a batched matmul is
    *slower* at 384 (128 ms) because materialising the matrix from JSON lists
    dominates. The multiply was never the cost, so the dependency and the
    conversion churn would buy ~16% at the top end and nothing typical.
    """
    return sum(x * y for x, y in zip(a, b))


def comparable_embedding(chunk, query_dim: int, model_name: str) -> bool:
    """Whether *chunk*'s vector can be dot-producted against the query vector.

    Two vectors are only comparable when they came out of the same model. The
    scoring loops used to express this as ``zip(query_vec, c.embedding)``,
    which does not compare — it *truncates to the shorter one* and returns a
    perfectly plausible number. A 384-dim query against 1536-dim stored
    vectors yields scores that sort confidently and mean nothing, with no
    exception and no log line. That is the failure this guard exists for, and
    it becomes reachable the moment the embedding model is changed.

    Dimension is the hard check. The model name is the subtle one: two
    different 384-dim models occupy different semantic spaces, so equal length
    is not sufficient.

    Rows whose ``embedding_model`` is NULL predate the column being populated.
    They are judged on dimension alone rather than excluded — excluding them
    would silently gut retrieval on every existing corpus, which is the same
    class of harm this guard is meant to prevent.
    """
    vec = getattr(chunk, "embedding", None)
    if not vec or len(vec) != query_dim:
        return False
    stored = getattr(chunk, "embedding_model", None)
    return not stored or stored == model_name


def _embedding_probe() -> bool:
    """Importability check only — never loads model weights."""
    from .rag import _embedding_available

    return _embedding_available()


def _vector_health(total: int, embedded: int, stale: int = 0) -> dict:
    """Whether retrieval is *actually* semantic, plus what to do if it is not.

    ``embedding_available`` is import-only by design (loading weights inside a
    stats call would be absurd), but that makes it a promise the corpus may not
    keep: the library imports, the model download fails, ``_embed_texts``
    swallows it and returns None, and every chunk lands with a NULL vector while
    the flag still says yes. Retrieval silently becomes token-set overlap.

    So the honest signal is not "is the library installed" but "are there
    vectors". ``vector_mode`` answers that:

    ``semantic``  — vectors exist, retrieval is embedding-based
    ``stale``     — vectors exist but were produced by a different embedding
                    model, so they are not comparable to anything this model
                    encodes. Those rows fall back to keyword scoring, and
                    counting them as ``semantic`` was the last way this probe
                    could still report green over a corpus it cannot search.
    ``degraded``  — the library is installed but nothing got embedded; this is
                    the case worth shouting about, because it looks fine
    ``keyword``   — no embedding library; expected, not a fault
    ``empty``     — nothing indexed yet, so there is nothing to say
    """
    from .rag import active_rag_backend

    available = _embedding_probe()
    if total <= 0:
        mode, hint = "empty", ""
    elif stale > 0 and stale >= embedded:
        # Every vector belongs to another model — retrieval is keyword-only in
        # practice, whatever the embedded count says.
        mode = "stale"
        hint = (
            f"{stale} 个切块由其他 embedding 模型生成，无法与当前模型比较，"
            "这些行已退回关键词匹配。点「重建索引」用当前模型重新向量化。"
        )
    elif stale > 0:
        mode = "stale"
        hint = (
            f"{embedded - stale}/{embedded} 个切块可用于语义检索，"
            f"另有 {stale} 个由其他 embedding 模型生成、已退回关键词匹配。"
            "点「重建索引」可恢复全部语义排序。"
        )
    elif embedded > 0:
        mode, hint = "semantic", ""
    elif available:
        mode = "degraded"
        hint = (
            "已安装向量库但没有任何切块被向量化——通常是模型权重未能下载。"
            "检索当前退化为关键词匹配。请检查服务端网络后点「重建索引」。"
        )
    else:
        mode = "keyword"
        hint = "未安装 sentence-transformers，检索为关键词匹配。可在下方一键安装 Embedding 后点「重建索引」。"
    return {"vector_mode": mode, "vector_hint": hint, "rag_backend": active_rag_backend()}


# ── indexing ─────────────────────────────────────────────────────────────────


def ingest_full_document(source_id: str, text: str, metadata: dict | None = None) -> int:
    """Full ingest: chunk + index + store offset info into document_chunks.

    Idempotent: if ``document_chunks`` already contains rows for *source_id*,
    skip and return 0.  Otherwise delegates to ``index_source`` for the full
    chunking + embedding + store pipeline and returns the chunk count.

    Never raises — ingest must not break callers.
    """
    if not kb_enabled() or not (text or "").strip():
        return 0
    try:
        from ..db.chunk_store import get_chunk_store

        # Idempotency guard: already ingested → skip
        existing = get_chunk_store().get_by_source(source_id)
        if existing:
            logger.info(
                "ingest_full_document: source %s already has %d chunks, skipping",
                source_id, len(existing),
            )
            return 0

        return index_source(source_id, text)
    except Exception as exc:
        return degrade_return(logger, exc, "ingest_full_document failed", 0)


def index_source(source_id: str, full_text: str, *, embed: bool = True) -> int:
    """(Re)chunk one source document into persistent KB rows.

    Returns the number of chunks written; 0 when disabled or text is empty.
    Never raises — KB indexing must not break ingestion.
    """
    if not kb_enabled() or not (full_text or "").strip():
        return 0
    try:
        from ..db.chunk_store import get_chunk_store
        from .chunking import chunk_markdown

        from . import ingest_timing as timing

        settings = get_settings()
        with timing.span("chunk"):
            chunks = chunk_markdown(
                full_text,
                max_chars=settings.ingest_chunk_max_chars,
                overlap=settings.ingest_chunk_overlap,
            )
            chunks = [c for c in chunks if len(c.text.strip()) > 30][: settings.kb_max_chunks_per_source]
        rows: list[dict] = [
            {
                "text": c.text,
                "heading_path": c.heading_path,
                "page_no": c.page_no,
                "paragraph_idx": c.paragraph_idx,
                "offset_start": c.offset_start,
                "offset_end": c.offset_end,
            }
            for c in chunks
        ]
        with timing.span("entities"):
            _attach_entities(source_id, rows)
        if embed and rows:
            with timing.span("embed"):
                vectors = _embed_texts([r["text"] for r in rows])
            if vectors:
                if len(vectors) != len(rows):
                    # A short or long response would bind vectors to the wrong
                    # text from the mismatch onward — every later chunk carries
                    # its neighbour's meaning, and nothing about the result
                    # looks wrong. Drop the batch instead; the rows stay
                    # keyword-searchable and a rebuild can retry.
                    logger.error(
                        "kb embedding count mismatch: %d vectors for %d chunks — "
                        "skipping embeddings for this source",
                        len(vectors), len(rows),
                    )
                    vectors = None
            if vectors:
                model_name = _embed_model_name()
                for row, vec in zip(rows, vectors):
                    row["embedding"] = vec
                    row["embedding_model"] = model_name
        n = get_chunk_store().replace_for_source(source_id, rows)
        if n and settings.kg_enabled and settings.kg_link_on_ingest:
            try:
                from .kg.entity_linker import link_source

                link_source(source_id, settings=settings)
            except Exception as link_exc:
                degrade_return(logger, link_exc, "kg link_on_ingest failed", None)
        return n
    except Exception as exc:
        return degrade_return(logger, exc, "kb index_source failed", 0)


def _attach_entities(source_id: str, rows: list[dict]) -> None:
    """Chunk-level chemistry/product entity extraction → row meta + registry."""
    settings = get_settings()
    if not settings.chem_extract_enabled or not rows:
        return
    try:
        from .chem_extract import extract_entities

        all_products: list[dict] = []
        for row in rows:
            meta = extract_entities(row["text"])
            if meta:
                row["meta"] = meta
                all_products.extend(meta.get("products") or [])
        if all_products and settings.product_extract_enabled:
            from ..db.product_store import get_product_store

            # `link_structures=False`: resolving 牌号 → CAS/SMILES is a network
            # round trip per product, and this runs inside the serial index
            # phase, so it dominated the whole build. Measured on one coating
            # patent: 3.57s with linking, 0.19s without — and a non-chemistry
            # patent 2.6x longer was unaffected either way, because the cost
            # tracks chemistry density rather than size.
            #
            # The registry itself is still written here; only the lookup moves.
            # `ProductStore.backfill_structures()` picks it up afterwards, where
            # `kb_products` being unique by norm_key means one lookup per
            # product instead of up to five per document.
            get_product_store().upsert_mentions(source_id, all_products, link_structures=False)
    except Exception as exc:
        degrade_return(logger, exc, "kb entity extraction failed", None)


def reindex_all(*, embed: bool = True) -> dict:
    """Rebuild chunk rows for every stored source (backfill / after upgrades)."""
    from ..db.chunk_store import get_chunk_store
    from ..db.models import SourceDocument
    from ..db.source_store import get_source_store

    store = get_source_store()
    sources = 0
    chunks = 0
    with store._session_factory() as session:
        rows = session.query(SourceDocument.id, SourceDocument.full_text).all()
    for source_id, full_text in rows:
        if not (full_text or "").strip():
            continue
        n = index_source(source_id, full_text, embed=embed)
        if n:
            sources += 1
            chunks += n
    total, embedded = get_chunk_store().counts()
    return {
        "reindexed_sources": sources,
        "reindexed_chunks": chunks,
        "total_chunks": total,
        "embedded_chunks": embedded,
    }


# ── retrieval ────────────────────────────────────────────────────────────────


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def _keyword_score(query_tokens: set[str], text: str) -> float:
    if not query_tokens:
        return 0.0
    hits = query_tokens & _tokens(text)
    return len(hits) / len(query_tokens)


def _chunk_to_evidence(chunk, source_meta: dict, score: float) -> Evidence:
    meta = source_meta.get(chunk.source_id) or {}
    title = meta.get("title") or "知识库文档"
    if chunk.heading_path:
        title = f"{title} · {chunk.heading_path}"
    if getattr(chunk, "page_no", None):
        title = f"{title} · P{chunk.page_no}"
    return Evidence(
        source=meta.get("source_kind") or "kb",
        identifier=f"kb:{chunk.source_id}#c{chunk.ord}",
        title=title[:200],
        snippet=chunk_snippet(chunk.text),
        relevance=max(0.05, min(1.0, round(score, 4))),
    )


def chunk_snippet(text: str) -> str:
    """A retrieved chunk as the LLM will see it.

    Chunks are stored at ``ingest_chunk_max_chars`` (1600). This used to clip
    them to a hardcoded 600 on the way out, which threw away two thirds of text
    that had already been fetched, parsed, chunked and persisted — the expensive
    part was done and the cheap part discarded it. 0 means no clip.
    """
    limit = int(get_settings().kb_snippet_max_chars or 0)
    return text[:limit] if limit > 0 else text


def _source_meta() -> dict:
    from ..db.models import SourceDocument
    from ..db.source_store import get_source_store

    with get_source_store()._session_factory() as session:
        rows = session.query(
            SourceDocument.id, SourceDocument.title, SourceDocument.source_kind
        ).all()
    return {rid: {"title": title, "source_kind": kind} for rid, title, kind in rows}


def _query_chem_context(query: str) -> dict:
    """Chemical entities + product expansion terms extracted from the query.

    * CAS / formulas / SMILES in the question → exact-entity boost targets;
    * trade names known to the product registry → the linked generic name /
      CAS / supplier become extra query terms (牌号 ↔ 通用名 双向打通);
    * generic material names that map to registry products → trade names
      become extra terms (reverse direction).
    """
    ctx: dict = {"cas": set(), "formulas": set(), "smiles": [], "products": set(), "terms": []}
    if not (query or "").strip():
        return ctx
    try:
        from .chem_extract import extract_cas, extract_formulas, extract_products, extract_smiles

        ctx["cas"] = set(extract_cas(query))
        ctx["formulas"] = set(extract_formulas(query))
        ctx["smiles"] = [s["canonical"] for s in extract_smiles(query)]

        from ..db.product_store import get_product_store, norm_key

        store = get_product_store()
        for p in extract_products(query):
            ctx["products"].add(norm_key(p["trade_name"], p.get("grade", "")))
            for row in store.search(p["trade_name"], limit=3):
                for term in (row.generic_name, row.cas, row.supplier):
                    if term:
                        ctx["terms"].append(term)
                if row.cas:
                    ctx["cas"].add(row.cas)
    except Exception as exc:
        degrade_return(logger, exc, "query chem context failed", None)
    return ctx


def _entity_boost(chunk, qctx: dict) -> float:
    """Additive score boost when chunk metadata shares entities with the query."""
    meta = getattr(chunk, "meta", None) or {}
    chem = meta.get("chem") or []
    if not chem and not meta.get("products"):
        return 0.0
    values = {e.get("value") for e in chem}
    boost = 0.0
    if qctx["cas"] & values:
        boost += 0.3
    if qctx["formulas"] & values:
        boost += 0.2
    if qctx["products"]:
        try:
            from ..db.product_store import norm_key

            chunk_products = {
                norm_key(p.get("trade_name", ""), p.get("grade", ""))
                for p in meta.get("products") or []
            }
            if qctx["products"] & chunk_products:
                boost += 0.25
        except Exception:
            pass
    if qctx["smiles"]:
        chunk_smiles = [e.get("value") for e in chem if e.get("type") == "smiles"]
        if chunk_smiles:
            try:
                from . import chemtools

                best = max(
                    (
                        chemtools.mol_similarity(q, c) or 0.0
                        for q in qctx["smiles"][:2]
                        for c in chunk_smiles[:4]
                    ),
                    default=0.0,
                )
                boost += 0.25 * best
            except Exception:
                pass
    return min(boost, 0.6)


def search_chunks(query: str, k: int = 6, *, project_id: str | None = None) -> list[Evidence]:
    """Retrieve the top-k KB chunks for a query (chemistry-aware hybrid).

    Base score: cosine over stored embeddings when both sides can embed,
    token-overlap otherwise.  On top of that, chunks sharing chemical
    entities with the question (CAS / formula / 牌号 / structure similarity
    via Tanimoto) get an additive boost, and trade names in the question are
    expanded with their registry-linked generic names.  Empty list when
    disabled, empty KB, or on any failure.
    """
    if not kb_enabled() or not (query or "").strip() or k <= 0:
        return []
    try:
        from ..db.chunk_store import get_chunk_store

        chunks = get_chunk_store().all_chunks(
            limit=get_settings().kb_search_scan_limit, project_id=project_id
        )
        if not chunks:
            return []

        qctx = _query_chem_context(query)
        expanded_query = query
        if qctx["terms"]:
            expanded_query = f"{query} {' '.join(qctx['terms'][:8])}"

        scored: list[tuple[float, object]] = []
        embedded = [c for c in chunks if c.embedding]
        query_vec = None
        if embedded:
            vecs = _embed_texts([expanded_query])
            query_vec = vecs[0] if vecs else None

        if query_vec is not None and embedded:
            model_name = _embed_model_name()
            dim = len(query_vec)
            usable = [c for c in embedded if comparable_embedding(c, dim, model_name)]
            skipped = len(embedded) - len(usable)
            if skipped:
                # Loud on purpose: this is the state where results still look
                # ranked and are not. It clears after a rebuild.
                logger.warning(
                    "kb search: %d/%d chunks embedded by a different model — "
                    "scored by keyword instead. Rebuild the index to restore "
                    "semantic ranking.",
                    skipped, len(embedded),
                )
            usable_ids = {id(c) for c in usable}
            for c in usable:
                score = _dot(query_vec, c.embedding)
                scored.append((score, c))
            # Text-only rows — and rows this model cannot compare against —
            # still compete via keywords, rescaled below cosine range.
            qt = _tokens(expanded_query)
            for c in chunks:
                if id(c) not in usable_ids:
                    scored.append((_keyword_score(qt, c.text) * 0.5, c))
        else:
            qt = _tokens(expanded_query)
            for c in chunks:
                scored.append((_keyword_score(qt, f"{c.heading_path} {c.text}"), c))

        has_chem_query = bool(
            qctx["cas"] or qctx["formulas"] or qctx["smiles"] or qctx["products"]
        )
        if has_chem_query:
            scored = [(s + _entity_boost(c, qctx), c) for s, c in scored]

        scored = [(s, c) for s, c in scored if s > 0.05]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        meta = _source_meta()
        return [_chunk_to_evidence(c, meta, s) for s, c in scored[:k]]
    except Exception as exc:
        return degrade_return(logger, exc, "kb search failed", [])


def aggregate_parameter_space() -> dict[str, dict]:
    """Fuse the LLM-extracted parameter spaces of all stored source guides.

    Returns {parameter_name: {min, max, unit, sources}} where min/max span the
    union of documented ranges (literature-supported envelope) and ``sources``
    counts how many documents mention the parameter.  Empty dict when the KB
    is disabled/empty or no guide carries a parameter space.
    """
    if not kb_enabled():
        return {}
    try:
        from ..db.models import SourceDocument
        from ..db.source_store import get_source_store

        with get_source_store()._session_factory() as session:
            rows = session.query(SourceDocument.source_guide).filter(
                SourceDocument.source_guide.isnot(None)
            ).all()

        fused: dict[str, dict] = {}
        for (guide,) in rows:
            space = (guide or {}).get("parameter_space") or {}
            if not isinstance(space, dict):
                continue
            for name, bound in space.items():
                if not isinstance(bound, dict):
                    continue
                lo, hi = bound.get("min_value"), bound.get("max_value")
                unit = (bound.get("unit") or "").strip()
                key = str(name).strip()
                if not key:
                    continue
                entry = fused.setdefault(
                    key, {"min": None, "max": None, "unit": unit, "sources": 0}
                )
                entry["sources"] += 1
                if lo is not None:
                    entry["min"] = lo if entry["min"] is None else min(entry["min"], lo)
                if hi is not None:
                    entry["max"] = hi if entry["max"] is None else max(entry["max"], hi)
                if not entry["unit"] and unit:
                    entry["unit"] = unit
        return {k: v for k, v in fused.items() if v["min"] is not None or v["max"] is not None}
    except Exception as exc:
        return degrade_return(logger, exc, "kb parameter-space aggregation failed", {})


def product_hints(materials: list) -> list[str]:
    """Corpus-derived commercial product lines for recommend prompts.

    For each requirement material, look up registry products whose generic
    name (or CAS) matches — the LLM then recommends against real purchasable
    grades instead of abstract chemistries.  Advisory; [] offline/empty.
    """
    if not kb_enabled() or not get_settings().product_extract_enabled:
        return []
    try:
        from ..db.product_store import get_product_store

        store = get_product_store()
        lines: list[str] = []
        seen: set[str] = set()
        for m in materials or []:
            name = (getattr(m, "name", "") or "").strip()
            if not name:
                continue
            for row in store.find_for_material(name):
                key = row.norm_key
                if key in seen or len(lines) >= 8:
                    continue
                seen.add(key)
                label = f"{row.trade_name} {row.grade}".strip()
                detail = "，".join(
                    x for x in (row.supplier, row.generic_name, row.role) if x
                )
                line = f"- {label}（{detail}）" if detail else f"- {label}"
                lines.append(f"{line} — 语料提及 {row.mention_count} 次")
        return lines
    except Exception as exc:
        return degrade_return(logger, exc, "kb product hints failed", [])


def doe_parameter_hints(factor_names: list[str]) -> list[str]:
    """Literature-envelope notes for DOE factors whose names match KB parameters.

    Advisory only — factor bounds are never mutated; the researcher decides."""
    space = aggregate_parameter_space()
    if not space:
        return []
    notes: list[str] = []
    lowered = {name.lower(): name for name in factor_names}
    for param, entry in space.items():
        match = next(
            (
                orig
                for low, orig in lowered.items()
                if low == param.lower() or param.lower() in low or low in param.lower()
            ),
            None,
        )
        if match is None:
            continue
        lo = entry["min"] if entry["min"] is not None else "?"
        hi = entry["max"] if entry["max"] is not None else "?"
        unit = f" {entry['unit']}" if entry["unit"] else ""
        notes.append(
            f"知识库文献范围：{match} ≈ {lo}–{hi}{unit}（{entry['sources']} 个来源）"
        )
    return notes


def kb_stats() -> dict:
    """Corpus counters for the UI / API."""
    from ..db.chunk_store import get_chunk_store
    from ..db.models import SourceDocument
    from ..db.source_store import get_source_store

    try:
        total, embedded = get_chunk_store().counts()
        try:
            stale = get_chunk_store().count_foreign_model(_embed_model_name())
        except Exception as exc:
            stale = degrade_return(logger, exc, "stale-vector count failed", 0)
        with get_source_store()._session_factory() as session:
            from sqlalchemy import func

            sources = session.query(func.count(SourceDocument.id)).scalar() or 0
            by_kind = dict(
                session.query(SourceDocument.source_kind, func.count(SourceDocument.id))
                .group_by(SourceDocument.source_kind)
                .all()
            )
        try:
            from ..db.product_store import get_product_store

            products = get_product_store().count()
        except Exception:
            products = 0
        try:
            from ..db.product_store import get_product_store

            pending_products = get_product_store().count_needing_structure()
        except Exception as exc:
            pending_products = degrade_return(
                logger, exc, "pending-structure count failed", 0
            )
        return {
            "enabled": kb_enabled(),
            "sources": int(sources),
            "sources_by_kind": {k: int(v) for k, v in by_kind.items()},
            "chunks": total,
            "embedded_chunks": embedded,
            "embedding_available": _embedding_probe(),
            "products": products,
            # Visible on purpose: the trade-name synonym expansion these feed is
            # unavailable until they resolve, so a number that never reaches 0
            # is something to notice rather than something to discover later.
            "products_pending_structure": pending_products,
            "stale_chunks": stale,
            **_vector_health(total, embedded, stale),
        }
    except Exception as exc:
        return degrade_return(
            logger, exc, "kb stats failed",
            {"enabled": kb_enabled(), "sources": 0, "sources_by_kind": {},
             "chunks": 0, "embedded_chunks": 0, "embedding_available": False,
             "products": 0},
        )
