"""SureChEMBL → KG sync (P3).

Entities
  - ``patent:scpn:{doc_id}``
  - ``chem:surechembl:{chemical_id}``

Edges (chem → patent)
  - ``appears_in`` (default)
  - ``claimed_in`` when section looks like claims

Never writes materials/formulations production pools.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from loguru import logger

from ..db.entity_store import get_entity_store
from ..db.session_utils import commit_session
from . import surechembl_client as client

_CLAIMS_RE = re.compile(r"\bclaim(s|ed)?\b|权利要求", re.I)


def patent_entity_id(doc_id: str) -> str:
    return f"patent:scpn:{(doc_id or '').strip()}"[:64]


def chem_entity_id(chemical_id: str) -> str:
    return f"chem:surechembl:{(chemical_id or '').strip()}"[:64]


def _section_link_type(section: str | None) -> str:
    raw = (section or "").strip()
    if raw and _CLAIMS_RE.search(raw):
        return "claimed_in"
    return "appears_in"


def _evidence_ref(doc_id: str, *, sentence: str = "") -> dict[str, Any]:
    return {
        "source_id": f"surechembl:{doc_id}",
        "chunk_id": None,
        "sentence": sentence or f"SureChEMBL document {doc_id}",
        "confidence": 0.7,
        "extraction_method": "surechembl",
    }


def ingest_document_graph(
    *,
    doc_id: str,
    title: str | None = None,
    assignee: str | None = None,
    pub_date: str | None = None,
    url: str | None = None,
    chemicals: list[dict[str, Any]] | None = None,
    fetch_chemistry: bool = True,
    chemistry_limit: int = 25,
    section: str | None = None,
) -> dict[str, Any]:
    """Upsert patent + chemistry entities and appears_in/claimed_in links."""
    did = (doc_id or "").strip()
    if not did:
        return {"ok": False, "reason": "empty_doc_id", "entities": 0, "links": 0}

    chems = list(chemicals or [])
    if fetch_chemistry and not chems:
        try:
            chems = client.document_chemistry(did, limit=chemistry_limit)
        except Exception as exc:
            logger.debug("surechembl kg chemistry fetch failed ({})", exc)
            chems = []

    store = get_entity_store()
    patent_id = patent_entity_id(did)
    link_type = _section_link_type(section)
    entities = 0
    links = 0

    with commit_session(store._session_factory) as session:
        store.upsert_entity(
            session,
            id=patent_id,
            kind="patent",
            canonical_name=(title or did)[:512],
            supplier=(assignee or "")[:120],
            grade=(pub_date or "")[:60],
            composition_status="resolved",
            aliases=[did] + ([url] if url else []),
        )
        entities += 1

        for row in chems:
            cid = str(row.get("chemical_id") or "").strip()
            if not cid:
                continue
            eid = chem_entity_id(cid)
            name = str(row.get("name") or cid)[:512]
            store.upsert_entity(
                session,
                id=eid,
                kind="chemical",
                canonical_name=name,
                smiles=(str(row.get("smiles")).strip() if row.get("smiles") else None),
                formula=(str(row.get("formula")).strip() if row.get("formula") else None),
                composition_status="resolved",
                aliases=[f"SCHEMBL:{cid}"],
            )
            entities += 1
            meta = {
                "frequency": row.get("global_frequency"),
                "similarity": row.get("similarity"),
                "chemical_id": cid,
                "doc_id": did,
                "assignee": assignee,
                "pub_date": pub_date,
                "section": section,
            }
            if store.merge_structural_link(
                session,
                src_entity_id=eid,
                dst_entity_id=patent_id,
                link_type=link_type,
                confidence=0.75,
                evidence_ref=_evidence_ref(did, sentence=name),
                metadata={k: v for k, v in meta.items() if v is not None},
                extraction_method="surechembl",
            ):
                links += 1

    return {
        "ok": True,
        "doc_id": did,
        "patent_entity_id": patent_id,
        "entities": entities,
        "links": links,
        "link_type": link_type,
        "chemicals": len(chems),
    }


def formulation_draft_entity_id(doc_id: str, slug: str = "embodiment") -> str:
    digest = hashlib.sha1(f"{doc_id}:{slug}".encode()).hexdigest()[:12]
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", (slug or "embodiment"))[:16] or "embodiment"
    # form:surechembl:{12hex}:{slug} fits String(64)
    return f"form:surechembl:{digest}:{safe}"[:64]
