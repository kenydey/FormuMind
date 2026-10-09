"""ColBERT persistent knowledge index with TF-IDF/embedding fallback.

When ``ragatouille`` is installed, uses ``colbert-ir/colbertv2.0`` late-interaction
retrieval. Otherwise persists an Evidence manifest on disk and re-ranks via
``rag.build_store()`` — same API surface for CRAG and recommend pipelines.
"""
from __future__ import annotations

import logging
from .errors import degrade_return, log_handled_exception
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from ..domain.schemas import Evidence
from . import rag

logger = logging.getLogger(__name__)

SourceType = Literal["patents", "literature", "internet", "local", "notebooklm"]

_LOCK = threading.Lock()
_MODEL_CACHE: dict[str, object] = {}

# v29 P0-1: 常驻检索器缓存 —— search() 每次重建 BM25 索引是秒级浪费。
# key: (collection, registry mtime_ns, doc_count, source_types_key)。
# index_evidence 写 registry 后 mtime 变化，缓存自动失效。
_STORE_CACHE: dict[tuple, object] = {}
_STORE_CACHE_LOCK = threading.Lock()


class ColbertDocMetadata(BaseModel):
    source_type: SourceType = "literature"
    identifier: str = ""
    title: str = ""
    indexed_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ColbertDocument(BaseModel):
    doc_id: str
    text: str
    metadata: ColbertDocMetadata = Field(default_factory=ColbertDocMetadata)


class ColbertSearchHit(BaseModel):
    doc_id: str
    score: float
    passage: str
    evidence: Evidence


class IndexManifest(BaseModel):
    collection: str
    doc_count: int
    backend: str
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def colbert_available() -> bool:
    try:
        import ragatouille  # noqa: F401

        return True
    except Exception as exc:
        log_handled_exception(logger, exc, "optional feature check")
        return False


def colbert_available_gpu(settings: Settings | None = None) -> bool:
    """Check if PyLate + CUDA torch are available for GPU ColBERT retrieval."""
    from ..config import get_settings
    settings = settings or get_settings()
    if not settings.gpu_enabled:
        return False
    try:
        import torch
        if not torch.cuda.is_available():
            return False
        import pylate  # noqa: F401
        return True
    except ImportError:
        return False
    except Exception as exc:
        log_handled_exception(logger, exc, "GPU ColBERT availability check")
        return False


def _infer_source_type(ev: Evidence) -> SourceType:
    src = (ev.source or "").lower()
    ident = (ev.identifier or "").lower()
    if any(x in src for x in ("uspto", "epo", "patent", "wipo")) or ident.startswith(("us", "ep", "wo")):
        return "patents"
    if "notebooklm" in src:
        return "notebooklm"
    if src == "local" or "upload" in src or "ingest" in src:
        return "local"
    if any(x in src for x in ("web", "duck", "internet", "chemcrow-web", "serp")):
        return "internet"
    if any(x in src for x in ("literature", "arxiv", "semantic", "paper", "doi", "seed")):
        return "literature"
    return "literature"


def _doc_id_for_evidence(ev: Evidence) -> str:
    key = (ev.identifier or ev.title or "doc").strip()
    key = re.sub(r"[^\w\-.:]+", "_", key)[:120]
    return key or "doc"


def _evidence_to_document(ev: Evidence) -> ColbertDocument:
    doc_id = _doc_id_for_evidence(ev)
    text = f"{ev.title}\n{ev.snippet}".strip()
    return ColbertDocument(
        doc_id=doc_id,
        text=text,
        metadata=ColbertDocMetadata(
            source_type=_infer_source_type(ev),
            identifier=ev.identifier,
            title=ev.title,
        ),
    )


def _collection_dir(settings: Settings, collection: str) -> Path:
    root = Path(settings.colbert_index_dir)
    return root / collection


def _manifest_path(settings: Settings, collection: str) -> Path:
    return _collection_dir(settings, collection) / "manifest.json"


def _evidence_registry_path(settings: Settings, collection: str) -> Path:
    return _collection_dir(settings, collection) / "evidence_registry.json"


def _load_registry(settings: Settings, collection: str) -> dict[str, Evidence]:
    path = _evidence_registry_path(settings, collection)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {k: Evidence.model_validate(v) for k, v in raw.items()}
    except Exception as exc:
        return degrade_return(logger, exc, "Failed to load evidence registry", {})


def _save_registry(settings: Settings, collection: str, registry: dict[str, Evidence]) -> None:
    path = _evidence_registry_path(settings, collection)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {k: v.model_dump() for k, v in registry.items()}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    # v29 B-1: 写时失效 —— _STORE_CACHE 的指纹只含 doc_id 不含内容，
    # 不清缓存会导致文档更新后 search() 返回过期旧内容。
    with _STORE_CACHE_LOCK:
        _STORE_CACHE.clear()


def _get_ragatouille_model(settings: Settings):
    if settings.colbert_model in _MODEL_CACHE:
        return _MODEL_CACHE[settings.colbert_model]
    from ragatouille import RAGPretrainedModel

    logger.info("Loading ColBERT model %s", settings.colbert_model)
    model = RAGPretrainedModel.from_pretrained(settings.colbert_model)
    _MODEL_CACHE[settings.colbert_model] = model
    return model


def index_documents(
    docs: list[ColbertDocument],
    *,
    collection: str | None = None,
    settings: Settings | None = None,
) -> IndexManifest:
    """Persist documents into the ColBERT (or fallback) index."""
    settings = settings or get_settings()
    collection = collection or settings.colbert_collection
    if not docs:
        return IndexManifest(collection=collection, doc_count=0, backend=active_backend())

    with _LOCK:
        registry = _load_registry(settings, collection)
        for doc in docs:
            ev = Evidence(
                source=doc.metadata.source_type,
                identifier=doc.metadata.identifier or doc.doc_id,
                title=doc.metadata.title or doc.doc_id,
                snippet=doc.text[:500],
                relevance=0.5,
            )
            # v29 B-3: doc_id 唯一性 —— 直接覆盖会导致同名文档静默丢失。
            # 已存在且内容不同时追加后缀保证唯一。
            doc_id = doc.doc_id
            if doc_id in registry:
                existing = registry[doc_id]
                if existing.snippet != ev.snippet or existing.title != ev.title:
                    n = 2
                    while f"{doc_id}#{n}" in registry:
                        n += 1
                    doc_id = f"{doc_id}#{n}"
                    logger.warning(
                        "doc_id 碰撞 '%s' → 重命名为 '%s'",
                        doc.doc_id,
                        doc_id,
                    )
            registry[doc_id] = ev

        backend = active_backend(settings)
        if backend == "colbert":
            try:
                model = _get_ragatouille_model(settings)
                index_path = str(_collection_dir(settings, collection))
                texts = [d.text for d in docs]
                doc_ids = [d.doc_id for d in docs]
                if Path(index_path).exists() and (_collection_dir(settings, collection) / ".ragatouille").exists():
                    model.add_to_index(index_name=collection, new_collection=docs)
                else:
                    _collection_dir(settings, collection).mkdir(parents=True, exist_ok=True)
                    model.index(
                        collection=texts,
                        index_name=collection,
                        max_document_length=256,
                        split_documents=False,
                        document_ids=doc_ids,
                    )
            except Exception as exc:
                logger.exception("ColBERT index failed, using fallback registry only: %s", exc)
                backend = "fallback"

        _save_registry(settings, collection, registry)
        manifest = IndexManifest(
            collection=collection,
            doc_count=len(registry),
            backend=backend,
        )
        _manifest_path(settings, collection).write_text(
            manifest.model_dump_json(indent=2), encoding="utf-8"
        )
        logger.info(
            "Indexed %s docs into collection=%s backend=%s total=%s",
            len(docs),
            collection,
            backend,
            len(registry),
        )
        return manifest


def index_evidence(
    evidence: list[Evidence],
    *,
    collection: str | None = None,
    settings: Settings | None = None,
) -> int:
    if not evidence:
        return 0
    docs = [_evidence_to_document(ev) for ev in evidence]
    manifest = index_documents(docs, collection=collection, settings=settings)
    return manifest.doc_count


def _registry_fingerprint(settings: Settings, collection: str, registry: dict) -> tuple:
    """v29 P0-1: registry 指纹，用于常驻检索器缓存失效。"""
    try:
        path = _evidence_registry_path(settings, collection)
        mtime = path.stat().st_mtime_ns if path.exists() else 0
    except Exception:
        mtime = 0
    return (collection, mtime, len(registry))


def _get_cached_store(
    settings: Settings,
    collection: str,
    filtered: list,
    source_types_key: str,
) -> object:
    """v29 P0-1: 获取或构建常驻检索器。registry 未变时复用，避免每次重建。"""
    # 指纹需要 registry，这里用 filtered 的长度 + collection 作为近似；
    # 精确指纹在调用方计算后传入。简化：用 filtered 的 id 列表哈希。
    import hashlib

    doc_ids = sorted(_doc_id_for_evidence(ev) for ev in filtered)
    fp = hashlib.md5("|".join(doc_ids).encode()).hexdigest()[:16]
    key = (collection, fp, source_types_key, len(filtered))
    with _STORE_CACHE_LOCK:
        store = _STORE_CACHE.get(key)
    if store is not None:
        return store
    store = rag.build_store()
    store.ingest(filtered)
    with _STORE_CACHE_LOCK:
        # 防止缓存无限增长：只保留最近 8 个
        if len(_STORE_CACHE) >= 8:
            _STORE_CACHE.pop(next(iter(_STORE_CACHE)))
        _STORE_CACHE[key] = store
    return store


def search(
    query: str,
    k: int | None = None,
    *,
    collection: str | None = None,
    source_types: list[str] | None = None,
    settings: Settings | None = None,
) -> list[ColbertSearchHit]:
    """Search the knowledge index; returns ranked hits with scores."""
    settings = settings or get_settings()
    collection = collection or settings.colbert_collection
    # v29 B-11: k 钳制 —— API 层有 le=1000 保护，但直接调用 search() 时无上限，
    # k=10**9 会全量返回。统一钳制到 1000。
    k = min(k or settings.colbert_top_k, 1000)

    registry = _load_registry(settings, collection)
    if not registry:
        logger.debug("Empty registry for collection %s", collection)
        return []

    filtered: list[Evidence] = list(registry.values())
    if source_types:
        allowed = set(source_types)
        filtered = [ev for ev in filtered if _infer_source_type(ev) in allowed]

    backend = active_backend(settings)
    hits: list[ColbertSearchHit] = []

    if backend == "colbert" and filtered:
        try:
            model = _get_ragatouille_model(settings)
            results = model.search(query, k=min(k, len(filtered)), index_name=collection)
            for rank, item in enumerate(results or []):
                doc_id = str(item.get("document_id") or item.get("doc_id") or rank)
                content = str(item.get("content") or item.get("text") or "")
                score = float(item.get("score", max(0.0, 1.0 - rank * 0.05)))
                ev = registry.get(doc_id)
                if ev is None:
                    ev = Evidence(
                        source="colbert",
                        identifier=doc_id,
                        title=content[:80],
                        snippet=content[:500],
                        relevance=min(1.0, max(0.0, score)),
                    )
                else:
                    ev = ev.model_copy(update={"relevance": min(1.0, max(0.0, score))})
                hits.append(
                    ColbertSearchHit(
                        doc_id=doc_id,
                        score=score,
                        passage=content[:500] or ev.snippet,
                        evidence=ev,
                    )
                )
            if hits:
                return hits[:k]
        except Exception as exc:
            logger.warning("ColBERT search failed, falling back to rag store: %s", exc)

    store = _get_cached_store(
        settings, collection, filtered,
        source_types_key=",".join(sorted(source_types)) if source_types else "",
    )
    # v29 B-2: 用真实 hybrid 分数而非合成排名分。
    # 此前 score = max(0.1, 1.0 - i*0.08) 与内容无关，导致下游
    # research_graph 的 colbert_min_score=0.35 阈值实际变成固定砍 top-9。
    if hasattr(store, "query_scored"):
        scored = store.query_scored(query, k=min(k, len(filtered)))
        ranked = [(s, ev) for s, ev in scored]
    else:
        ranked = [(max(0.1, 1.0 - i * 0.08), ev)
                  for i, ev in enumerate(store.query(query, k=min(k, len(filtered))))]
    if not ranked:
        ranked = [(0.5, ev) for ev in filtered[:k]]
    for score, ev in ranked:
        doc_id = _doc_id_for_evidence(ev)
        score = min(1.0, max(0.0, float(score)))
        hits.append(
            ColbertSearchHit(
                doc_id=doc_id,
                score=score,
                passage=ev.snippet,
                evidence=ev.model_copy(update={"relevance": score}),
            )
        )
    return hits[:k]


def active_backend(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    # Explicit override always wins
    if settings.rag_backend not in ("auto", ""):
        return settings.rag_backend
    # GPU: PyLate ColBERT (only when gpu_enabled AND CUDA available)
    if settings.gpu_enabled and colbert_available_gpu(settings):
        return "pylate"
    # Legacy ColBERT only when gpu_enabled AND colbert_available
    if settings.gpu_enabled and colbert_available():
        return "colbert"
    # CPU default: BM25+FAISS (reliable, zero AVX2 requirement)
    return "bm25_faiss"


def bootstrap_seed_corpus(settings: Settings | None = None) -> int:
    """Index offline domain knowledge paragraphs on first run."""
    settings = settings or get_settings()
    registry = _load_registry(settings, settings.colbert_collection)
    if registry:
        return len(registry)

    from ..domain import knowledge

    evidence: list[Evidence] = []
    for name, props in list(knowledge.RAW_MATERIALS.items())[:40]:
        role = props.get("role", "material")
        snippet = f"{name}: role={role}"
        if props.get("cas_no"):
            snippet += f", CAS={props['cas_no']}"
        evidence.append(
            Evidence(
                source="seed",
                identifier=f"seed:{name}",
                title=name,
                snippet=snippet,
                relevance=0.4,
            )
        )
    count = index_evidence(evidence, settings=settings)
    logger.info("Bootstrapped seed corpus: %s documents", count)
    return count
