"""Builtin read-only scientific connectors (Literature / Chemistry).

Phase 4a — Python adapters over existing FormuMind search helpers.
Not MCP processes; exposed via Settings + optional chat enrichment.
"""
from __future__ import annotations

import logging
from typing import Any

from ..domain.schemas import Evidence

logger = logging.getLogger(__name__)

CONNECTOR_CATALOG: list[dict[str, Any]] = [
    {
        "id": "literature",
        "display_name": "Literature Graph",
        "kind": "builtin",
        "description": "OpenAlex / arXiv / Crossref-oriented literature search.",
        "use_when": (
            "Use when exploring scholarly literature — works by topic, citations, "
            "authors, venues, preprints, or bibliographic metadata."
        ),
        "sources": ["OpenAlex", "arXiv", "Crossref"],
        "readonly": True,
    },
    {
        "id": "chemistry",
        "display_name": "Chemistry",
        "kind": "builtin",
        "description": "PubChem + ChEBI small-molecule / ontology lookup.",
        "use_when": (
            "Use for small-molecule identity — PubChem properties, SMILES, "
            "formula, CID; and ChEBI roles/ontology labels for formulation ingredients."
        ),
        "sources": ["PubChem", "ChEBI"],
        "readonly": True,
    },
]


def list_connectors(*, settings: Any = None) -> list[dict[str, Any]]:
    from .skills_store import load_prefs

    # Reuse skills prefs file section or dedicated key
    prefs = load_prefs()
    disabled = set(prefs.get("disabled_connector_ids") or [])
    enabled_flag = True
    if settings is not None:
        enabled_flag = bool(getattr(settings, "connectors_builtin_enabled", True))
    rows = []
    for c in CONNECTOR_CATALOG:
        row = dict(c)
        row["enabled"] = enabled_flag and c["id"] not in disabled
        rows.append(row)
    return rows


def set_connector_enabled(connector_id: str, enabled: bool) -> list[dict[str, Any]]:
    from .skills_store import load_prefs, save_prefs

    prefs = load_prefs()
    disabled = set(prefs.get("disabled_connector_ids") or [])
    if enabled:
        disabled.discard(connector_id)
    else:
        disabled.add(connector_id)
    save_prefs({"disabled_connector_ids": sorted(disabled)})
    # save_prefs only knows certain keys — extend store
    return list_connectors()


def search_literature(query: str, *, limit: int = 8) -> list[Evidence]:
    q = (query or "").strip()
    if not q:
        return []
    try:
        from .literature import search_openalex

        rows = search_openalex(q, limit=limit) or []
        out: list[Evidence] = []
        for r in rows[:limit]:
            if isinstance(r, Evidence):
                out.append(r)
            elif isinstance(r, dict):
                try:
                    out.append(Evidence.model_validate(r))
                except Exception:
                    continue
        return out
    except Exception as exc:  # noqa: BLE001
        logger.debug("literature connector: %s", exc)
        return []


def lookup_chemistry(query: str, *, limit: int = 5) -> list[Evidence]:
    q = (query or "").strip()
    if not q:
        return []
    out: list[Evidence] = []
    try:
        from .compounds import lookup_compound

        hit = lookup_compound(q)
        if hit and (hit.get("formula") or hit.get("cas") or hit.get("cid") or hit.get("smiles")):
            title = hit.get("iupac_name") or hit.get("zh_name") or hit.get("name") or q
            snip = (
                f"CID={hit.get('cid')}; CAS={hit.get('cas')}; formula={hit.get('formula')}; "
                f"MW={hit.get('mw') or hit.get('molecular_weight')}; "
                f"SMILES={hit.get('smiles') or hit.get('isomeric_smiles')}"
            )
            out.append(
                Evidence(
                    source="pubchem",
                    identifier=str(hit.get("cid") or hit.get("cas") or title),
                    title=str(title),
                    snippet=snip,
                    relevance=0.9,
                )
            )
    except Exception:
        pass
    if not out:
        # Fallback: lightweight PubChemPy if available
        try:
            import pubchempy as pcp

            comps = pcp.get_compounds(q, "name")[:limit]
            for c in comps:
                out.append(
                    Evidence(
                        source="pubchem",
                        identifier=str(c.cid),
                        title=c.iupac_name or q,
                        snippet=(
                            f"formula={c.molecular_formula}; MW={c.molecular_weight}; "
                            f"SMILES={c.isomeric_smiles}"
                        ),
                        relevance=0.85,
                    )
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("chemistry pubchem connector: %s", exc)

    # Wave C: ChEBI ontology enrichment (fail-open; append alongside PubChem)
    try:
        from .chemistry_chebi import lookup_chebi

        for row in lookup_chebi(q, limit=min(3, limit)):
            cid = row.get("chebi_id") or row.get("name") or q
            desc = row.get("description") or ""
            out.append(
                Evidence(
                    source="chebi",
                    identifier=str(cid),
                    title=str(row.get("name") or q),
                    snippet=f"ChEBI {cid}; {desc}".strip("; "),
                    relevance=0.8,
                )
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("chemistry chebi connector: %s", exc)
    return out[: max(limit, 5)]


def gather_connector_evidence(
    question: str,
    connector_ids: list[str],
    *,
    settings: Any = None,
) -> list[Evidence]:
    if not connector_ids:
        return []
    if settings is not None and not bool(getattr(settings, "connectors_builtin_enabled", True)):
        return []
    enabled = {c["id"] for c in list_connectors(settings=settings) if c.get("enabled")}
    out: list[Evidence] = []
    for cid in connector_ids:
        if cid not in enabled:
            continue
        if cid == "literature":
            out.extend(search_literature(question))
        elif cid == "chemistry":
            out.extend(lookup_chemistry(question))
    return out
