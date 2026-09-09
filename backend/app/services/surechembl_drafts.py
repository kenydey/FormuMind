"""SureChEMBL embodiment Formulation drafts (P3) — human review required.

Extract returns a draft only (no DB writes).
Confirm writes KG + force_pending materials; never production formula pool.
"""
from __future__ import annotations

from typing import Any

from loguru import logger

from ..db.entity_store import get_entity_store
from ..db.session_utils import commit_session
from . import surechembl_client as client
from . import surechembl_kg as sch_kg
from .material_promote import propose_material

_NOISE_NAME = {
    "methylbenzene",
    "propan-2-ol",
    "water",
    "ethanol",
    "methanol",
    "sulfuric acid",
    "hydrogen",
    "oxygen",
}


def _pick_ingredients(chems: list[dict[str, Any]], *, limit: int = 8) -> list[dict[str, Any]]:
    ranked = sorted(
        chems,
        key=lambda r: (-(r.get("global_frequency") or 0), r.get("name") or ""),
    )
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in ranked:
        name = str(row.get("name") or "").strip()
        if not name or name.casefold() in _NOISE_NAME:
            continue
        smiles = str(row.get("smiles") or "").strip()
        # Skip multi-fragment solvent mixtures.
        if smiles.count(".") >= 2:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
        if len(out) >= limit:
            break
    return out


def extract_example_draft(
    *,
    doc_id: str,
    title: str | None = None,
    assignee: str | None = None,
    pub_date: str | None = None,
    url: str | None = None,
    domain: str = "anticorrosion_coating",
    ingredient_limit: int = 8,
) -> dict[str, Any]:
    """Build a review-only Formulation draft from document chemistry.

    Does **not** write KG, materials, or leaderboard.
    """
    did = (doc_id or "").strip()
    if not did:
        return {"ok": False, "reason": "empty_doc_id", "draft": None}

    try:
        chems = client.document_chemistry(did, limit=max(20, ingredient_limit * 3))
    except Exception as exc:
        logger.debug("surechembl draft chemistry failed ({})", exc)
        chems = []

    picked = _pick_ingredients(chems, limit=ingredient_limit)
    if not picked:
        return {
            "ok": False,
            "reason": "no_usable_chemistry",
            "doc_id": did,
            "draft": None,
            "chemistry_count": len(chems),
        }

    n = len(picked)
    # Equal placeholder split — amounts are unknown from chemistry export.
    share = round(100.0 / n, 4)
    ingredients = []
    for row in picked:
        ingredients.append(
            {
                "name": str(row.get("name") or row.get("chemical_id")),
                "role": "additive",
                "weight_pct": share,
                "smiles": row.get("smiles"),
                "cas_no": None,
                "chemical_id": row.get("chemical_id"),
                "formula": row.get("formula"),
                "global_frequency": row.get("global_frequency"),
            }
        )

    display_title = (title or did).strip()
    draft = {
        "status": "draft",
        "needs_review": True,
        "origin": "surechembl",
        "doc_id": did,
        "title": display_title,
        "assignee": assignee,
        "pub_date": pub_date,
        "url": url or client.google_patent_url(did),
        "url_alt": client.surechembl_document_url(did),
        "formulation": {
            "name": f"SureChEMBL 草稿 · {did}",
            "domain": domain,
            "ingredients": [
                {
                    "name": ing["name"],
                    "role": ing["role"],
                    "weight_pct": ing["weight_pct"],
                    "smiles": ing.get("smiles"),
                    "cas_no": ing.get("cas_no"),
                }
                for ing in ingredients
            ],
            "predicted": {},
            "warnings": [
                "人审草稿：重量分为占位均分，非专利实施例真实配比",
                "确认后仅进入原料 pending + KG，不会写入生产配方池",
            ],
            "source": "surechembl",
        },
        "ingredients_detail": ingredients,
        "chemistry_count": len(chems),
    }
    return {"ok": True, "draft": draft}


def confirm_example_draft(draft: dict[str, Any]) -> dict[str, Any]:
    """Human confirm: KG ingest + pending materials + draft formulation entity.

    Red lines:
    - no leaderboard / production formula pool write
    - materials only via force_pending propose
    """
    if not isinstance(draft, dict):
        return {"ok": False, "reason": "invalid_draft"}
    if draft.get("origin") != "surechembl" or not draft.get("needs_review"):
        return {"ok": False, "reason": "draft_not_reviewable"}

    did = str(draft.get("doc_id") or "").strip()
    if not did:
        return {"ok": False, "reason": "empty_doc_id"}

    details = list(draft.get("ingredients_detail") or [])
    if not details:
        form = draft.get("formulation") or {}
        details = list(form.get("ingredients") or [])

    # 1) Patent + chem graph
    kg = sch_kg.ingest_document_graph(
        doc_id=did,
        title=draft.get("title"),
        assignee=draft.get("assignee"),
        pub_date=draft.get("pub_date"),
        url=draft.get("url"),
        chemicals=[
            {
                "chemical_id": r.get("chemical_id"),
                "name": r.get("name"),
                "smiles": r.get("smiles"),
                "formula": r.get("formula"),
                "global_frequency": r.get("global_frequency"),
            }
            for r in details
            if r.get("chemical_id") or r.get("name")
        ],
        fetch_chemistry=False,
    )

    # 2) Pending materials only
    pending: list[dict[str, Any]] = []
    for row in details:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        try:
            result = propose_material(
                name,
                {
                    "smiles": row.get("smiles") or None,
                    "cas_no": row.get("cas_no") or None,
                    "role": row.get("role") or "additive",
                    "formula": row.get("formula") or None,
                },
                source="surechembl",
                source_ref=f"SCHEMBL draft {did}",
                force_pending=True,
            )
            pending.append(result)
        except Exception as exc:
            pending.append({"action": "error", "name": name, "reason": str(exc)})

    # 3) Draft formulation entity (not production pool)
    form_eid = sch_kg.formulation_draft_entity_id(did, "embodiment")
    store = get_entity_store()
    links = 0
    with commit_session(store._session_factory) as session:
        store.upsert_entity(
            session,
            id=form_eid,
            kind="formulation",
            canonical_name=str((draft.get("formulation") or {}).get("name") or did)[:512],
            composition_status="unknown",
            aliases=[
                "origin:surechembl",
                "status:pending_review",
                f"doc:{did}",
            ],
            supplier=str(draft.get("assignee") or "")[:120],
            grade=str(draft.get("pub_date") or "")[:60],
        )
        patent_id = sch_kg.patent_entity_id(did)
        # Link draft formulation to patent via appears_in (form → patent)
        if store.merge_structural_link(
            session,
            src_entity_id=form_eid,
            dst_entity_id=patent_id,
            link_type="appears_in",
            confidence=0.8,
            evidence_ref={
                "source_id": f"surechembl:{did}",
                "chunk_id": None,
                "sentence": "human-confirmed SureChEMBL embodiment draft",
                "confidence": 0.8,
                "extraction_method": "surechembl_human",
            },
            metadata={"origin": "surechembl", "status": "pending_review"},
            extraction_method="surechembl_human",
        ):
            links += 1
        for row in details:
            cid = str(row.get("chemical_id") or "").strip()
            if not cid:
                continue
            chem_id = sch_kg.chem_entity_id(cid)
            if store.merge_structural_link(
                session,
                src_entity_id=form_eid,
                dst_entity_id=chem_id,
                link_type="has_ingredient",
                confidence=0.7,
                evidence_ref={
                    "source_id": f"surechembl:{did}",
                    "chunk_id": None,
                    "sentence": str(row.get("name") or cid),
                    "confidence": 0.7,
                    "extraction_method": "surechembl_human",
                },
                metadata={
                    "weight_pct": row.get("weight_pct"),
                    "placeholder_amount": True,
                    "origin": "surechembl",
                },
                extraction_method="surechembl_human",
            ):
                links += 1

    return {
        "ok": True,
        "doc_id": did,
        "kg": kg,
        "pending_materials": pending,
        "formulation_entity_id": form_eid,
        "links_added": links,
        "promoted_to_pool": False,
        "note": "草稿已确认：原料进入 pending；配方实体标记 pending_review；未写入生产配方池",
    }
