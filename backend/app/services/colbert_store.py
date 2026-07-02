"""ColBERT persistent knowledge index with TF-IDF/embedding fallback.

When ``ragatouille`` is installed, uses ``colbert-ir/colbertv2.0`` late-interaction
retrieval. Otherwise persists an Evidence manifest on disk and re-ranks via
``rag.build_store()`` — same API surface for CRAG and recommend pipelines.
"""
from __future__ import annotations

import logging
from .errors import degrade_return, log_handled_exception, optional_import, reraise_if_fatal
import json
import re
import threading
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Literal

from loguru import logger
from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from ..domain.schemas import Evidence
from . import rag

logger = logging.getLogger(__name__)

SourceType = Literal["patents", "literature", "internet", "local", "notebooklm"]

_LOCK = threading.Lock()
_MODEL_CACHE: dict[str, object] = {}


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
            registry[doc.doc_id] = ev

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
            "Indexed {} docs into collection={} backend={} total={}",
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
    k = k or settings.colbert_top_k

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
                score = float(item.get("score", 1.0 - rank * 0.05))
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

    store = rag.build_store()
    store.ingest(filtered)
    ranked = store.query(query, k=min(k, len(filtered))) or filtered[:k]
    for i, ev in enumerate(ranked):
        doc_id = _doc_id_for_evidence(ev)
        score = max(0.1, 1.0 - i * 0.08)
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
    if settings.rag_backend == "colbert" and colbert_available():
        return "colbert"
    if settings.rag_backend in ("auto", "colbert") and colbert_available():
        return "colbert"
    if settings.rag_backend == "embedding" or (
        settings.rag_backend == "auto" and rag.active_rag_backend() == "embedding"
    ):
        return "embedding"
    return "fallback"


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
