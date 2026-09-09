"""Compile SourceDocument facts into LLM Wiki material/chemical pages (W2).

Deterministic merge (no LLM rewrite in this slice): union source_ids and append
``### Source`` evidence blocks from SourceGuide / chunk chem meta / kb_products.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from ...config import get_settings
from ...db.chunk_store import get_chunk_store
from ...db.material_store import norm_key as material_norm_key
from ...db.source_store import get_source_store
from ...db.wiki_store import get_wiki_store
from .schema import (
    chemical_entity_id,
    chemical_path,
    dump_page,
    extract_evidence_blocks,
    has_source_block,
    material_entity_id,
    material_path,
    mechanism_path,
    parse_front_matter,
    pitfall_path,
    safe_key,
    system_path,
)

logger = logging.getLogger(__name__)

_CAS_RE = re.compile(r"^\d{2,7}-\d{2}-\d$")


def compile_source(source_id: str) -> dict[str, Any]:
    """Merge one ingested source into wiki pages. Never raises to callers."""
    try:
        return _compile_source_impl(source_id)
    except Exception as exc:  # noqa: BLE001 — compile must never break ingest
        logger.exception("wiki compile_source failed for %s: %s", source_id, exc)
        return {"ok": False, "reason": "error", "error": str(exc), "pages": []}


def _compile_source_impl(source_id: str) -> dict[str, Any]:
    settings = get_settings()
    if not settings.wiki_enabled:
        return {"ok": False, "reason": "wiki_disabled", "pages": []}

    sid = (source_id or "").strip()
    if not sid:
        return {"ok": False, "reason": "missing_source_id", "pages": []}

    store = get_wiki_store()
    sources = get_source_store()
    doc = sources.get(sid)
    if doc is None:
        return {"ok": False, "reason": "source_not_found", "pages": []}

    guide = sources.get_source_guide(sid)
    chunks = get_chunk_store().get_by_source(sid)

    materials: dict[str, dict[str, Any]] = {}
    chemicals: dict[str, dict[str, Any]] = {}

    def _add_material(name: str, *, grade: str = "", supplier: str = "", role: str = "", note: str = "") -> None:
        title = (name or "").strip()
        if not title:
            return
        key = safe_key(material_norm_key(title))
        slot = materials.setdefault(
            key,
            {
                "title": title,
                "norm_key": key,
                "grade": grade,
                "supplier": supplier,
                "role": role,
                "notes": [],
            },
        )
        if grade and not slot.get("grade"):
            slot["grade"] = grade
        if supplier and not slot.get("supplier"):
            slot["supplier"] = supplier
        if role and not slot.get("role"):
            slot["role"] = role
        if note:
            slot["notes"].append(note)

    def _add_cas(cas: str, *, note: str = "") -> None:
        c = (cas or "").strip()
        if not _CAS_RE.match(c):
            return
        slot = chemicals.setdefault(
            c,
            {"cas": c, "title": c, "notes": []},
        )
        if note:
            slot["notes"].append(note)

    if guide is not None:
        for p in guide.products or []:
            _add_material(
                p.trade_name or p.generic_name or "",
                grade=p.grade or "",
                supplier=p.supplier or "",
                role=p.role or "",
                note="from source_guide.products",
            )
        for ent in guide.key_entities or []:
            if _CAS_RE.match(str(ent).strip()):
                _add_cas(str(ent), note="from source_guide.key_entities")
            elif len(str(ent).strip()) >= 2:
                _add_material(str(ent), note="from source_guide.key_entities")

    for ch in chunks or []:
        meta = getattr(ch, "meta", None) or {}
        for p in meta.get("products") or []:
            _add_material(
                p.get("trade_name") or "",
                grade=p.get("grade") or "",
                supplier=p.get("supplier") or "",
                note="from chunk chem_extract",
            )
        for c in meta.get("chem") or []:
            if (c.get("type") or "").lower() == "cas":
                _add_cas(str(c.get("value") or ""), note="from chunk chem_extract")

    # Also pull kb_products that list this source
    try:
        from ...db.product_store import get_product_store

        for prod in get_product_store().search("", limit=200):
            sids = list(prod.source_ids or [])
            if sid not in sids:
                continue
            _add_material(
                prod.trade_name or "",
                grade=prod.grade or "",
                supplier=prod.supplier or "",
                role=prod.role or "",
                note="from kb_products registry",
            )
            if prod.cas:
                _add_cas(prod.cas, note=f"linked product {prod.trade_name}")
    except Exception as exc:  # noqa: BLE001
        logger.debug("wiki compile: product_store scan skipped: %s", exc)

    updated: list[str] = []
    title_hint = (doc.title or doc.filename or sid)[:120]

    for key, info in materials.items():
        path = material_path(key)
        entity_id = material_entity_id(key)
        existing_md = store.read_markdown(path)
        source_ids = [sid]
        flags: list[str] = []
        evidence: list[str] = []
        summary = f"Commercial / named material **{info['title']}**."
        if info.get("grade"):
            summary += f" Grade: {info['grade']}."
        if info.get("supplier"):
            summary += f" Supplier: {info['supplier']}."
        if info.get("role"):
            summary += f" Role: {info['role']}."

        if existing_md:
            meta, body = parse_front_matter(existing_md)
            source_ids = list(dict.fromkeys([*(meta.get("source_ids") or []), sid]))
            flags = list(meta.get("flags") or [])
            evidence = extract_evidence_blocks(body)
            # Keep prior summary paragraph if present
            if "## Summary" in body:
                prev = body.split("## Summary", 1)[1]
                if "## Evidence" in prev:
                    prev = prev.split("## Evidence", 1)[0]
                prev = prev.strip()
                if prev and prev != "_No summary yet._":
                    summary = prev

        if not has_source_block(evidence, sid):
            lines = [
                f"### Source `{sid}`",
                f"- document: {title_hint}",
                f"- origin: {doc.origin_url or ''}",
            ]
            for n in info.get("notes") or []:
                lines.append(f"- note: {n}")
            evidence.append("\n".join(lines))

        md = dump_page(
            kind="material",
            title=info["title"],
            entity_id=entity_id,
            norm_key=key,
            source_ids=source_ids,
            flags=flags,
            summary=summary,
            evidence_blocks=evidence,
        )
        row = store.upsert_page(
            path=path,
            kind="material",
            title=info["title"],
            norm_key=key,
            entity_id=entity_id,
            markdown=md,
            source_ids=source_ids,
            flags=flags,
        )
        updated.append(row.path)

    for cas, info in chemicals.items():
        path = chemical_path(cas=cas)
        entity_id = chemical_entity_id(cas=cas)
        existing_md = store.read_markdown(path)
        source_ids = [sid]
        flags: list[str] = []
        evidence: list[str] = []
        summary = f"Chemical entity CAS **{cas}**."

        if existing_md:
            meta, body = parse_front_matter(existing_md)
            source_ids = list(dict.fromkeys([*(meta.get("source_ids") or []), sid]))
            flags = list(meta.get("flags") or [])
            evidence = extract_evidence_blocks(body)
            if "## Summary" in body:
                prev = body.split("## Summary", 1)[1]
                if "## Evidence" in prev:
                    prev = prev.split("## Evidence", 1)[0]
                prev = prev.strip()
                if prev and prev != "_No summary yet._":
                    summary = prev

        if not has_source_block(evidence, sid):
            lines = [
                f"### Source `{sid}`",
                f"- document: {title_hint}",
                f"- origin: {doc.origin_url or ''}",
            ]
            for n in info.get("notes") or []:
                lines.append(f"- note: {n}")
            evidence.append("\n".join(lines))

        md = dump_page(
            kind="chemical",
            title=info["title"],
            entity_id=entity_id,
            norm_key=cas,
            source_ids=source_ids,
            flags=flags,
            summary=summary,
            evidence_blocks=evidence,
        )
        row = store.upsert_page(
            path=path,
            kind="chemical",
            title=info["title"],
            norm_key=cas,
            entity_id=entity_id,
            markdown=md,
            source_ids=source_ids,
            flags=flags,
        )
        updated.append(row.path)

    # Systems / mechanisms / pitfalls from SourceGuide (W4)
    if guide is not None:
        for pname, bound in (guide.parameter_space or {}).items():
            key = safe_key(pname)
            path = system_path(pname)
            bounds = [
                {
                    "name": pname,
                    "min": bound.min_value,
                    "max": bound.max_value,
                    "unit": bound.unit or "",
                }
            ]
            existing_md = store.read_markdown(path)
            source_ids = [sid]
            flags: list[str] = []
            evidence: list[str] = []
            summary = f"Process / DOE parameter **{pname}** from literature guides."
            forbidden: list[str] = []
            if existing_md:
                meta, body = parse_front_matter(existing_md)
                source_ids = list(dict.fromkeys([*(meta.get("source_ids") or []), sid]))
                flags = list(meta.get("flags") or [])
                evidence = extract_evidence_blocks(body)
                import json as _json

                raw_b = meta.get("bounds_json")
                if isinstance(raw_b, str):
                    try:
                        prev = _json.loads(raw_b)
                        if isinstance(prev, list):
                            # merge by name
                            by = {str(x.get("name")): x for x in prev if isinstance(x, dict)}
                            by[pname] = bounds[0]
                            bounds = list(by.values())
                    except Exception:
                        pass
            if not has_source_block(evidence, sid):
                evidence.append(
                    "\n".join(
                        [
                            f"### Source `{sid}`",
                            f"- document: {title_hint}",
                            f"- parameter_space: {pname} "
                            f"[{bound.min_value}–{bound.max_value} {bound.unit}]",
                        ]
                    )
                )
            # Tight max as soft forbidden note
            if bound.max_value is not None:
                forbidden.append(f"{pname} > {bound.max_value} {bound.unit}".strip())
            row = store.upsert_page(
                path=path,
                kind="system",
                title=pname,
                norm_key=key,
                entity_id=f"system:{key}"[:64],
                markdown=dump_page(
                    kind="system",
                    title=pname,
                    entity_id=f"system:{key}"[:64],
                    norm_key=key,
                    source_ids=source_ids,
                    flags=flags,
                    summary=summary,
                    evidence_blocks=evidence,
                    bounds=bounds,
                    forbidden=forbidden,
                ),
                source_ids=source_ids,
                flags=flags,
            )
            updated.append(row.path)

        # Mechanism page from summary
        if (guide.summary or "").strip() and guide.status != "degraded":
            mkey = safe_key((guide.key_entities or ["mechanism"])[0])[:80]
            path = mechanism_path(mkey)
            existing_md = store.read_markdown(path)
            source_ids = [sid]
            flags = []
            evidence = []
            summary = guide.summary.strip()
            if existing_md:
                meta, body = parse_front_matter(existing_md)
                source_ids = list(dict.fromkeys([*(meta.get("source_ids") or []), sid]))
                flags = list(meta.get("flags") or [])
                evidence = extract_evidence_blocks(body)
                if "## Summary" in body:
                    prev = body.split("## Summary", 1)[1]
                    if "## Evidence" in prev:
                        prev = prev.split("## Evidence", 1)[0]
                    prev = prev.strip()
                    if prev and prev != "_No summary yet._" and summary not in prev:
                        summary = f"{prev}\n\n---\n\n{summary}"
            if not has_source_block(evidence, sid):
                evidence.append(
                    "\n".join(
                        [
                            f"### Source `{sid}`",
                            f"- document: {title_hint}",
                            "- note: from source_guide.summary",
                        ]
                    )
                )
            row = store.upsert_page(
                path=path,
                kind="mechanism",
                title=f"机理 · {mkey}",
                norm_key=mkey,
                entity_id=f"mechanism:{mkey}"[:64],
                markdown=dump_page(
                    kind="mechanism",
                    title=f"机理 · {mkey}",
                    entity_id=f"mechanism:{mkey}"[:64],
                    norm_key=mkey,
                    source_ids=source_ids,
                    flags=flags,
                    summary=summary,
                    evidence_blocks=evidence,
                ),
                source_ids=source_ids,
                flags=flags,
            )
            updated.append(row.path)

        # Pitfalls from FAQs mentioning 避免/禁忌/不要/禁止/avoid
        _pit_re = re.compile(r"避免|禁忌|不要|禁止|avoid|forbid|never", re.I)
        for faq in guide.faqs or []:
            text = (faq or "").strip()
            if not text or not _pit_re.search(text):
                continue
            pkey = safe_key(text)[:80]
            path = pitfall_path(pkey)
            existing_md = store.read_markdown(path)
            source_ids = [sid]
            flags = []
            evidence = []
            if existing_md:
                meta, body = parse_front_matter(existing_md)
                source_ids = list(dict.fromkeys([*(meta.get("source_ids") or []), sid]))
                flags = list(meta.get("flags") or [])
                evidence = extract_evidence_blocks(body)
            if not has_source_block(evidence, sid):
                evidence.append(
                    "\n".join(
                        [
                            f"### Source `{sid}`",
                            f"- document: {title_hint}",
                            f"- faq: {text}",
                        ]
                    )
                )
            row = store.upsert_page(
                path=path,
                kind="pitfall",
                title=text[:120],
                norm_key=pkey,
                entity_id=f"pitfall:{pkey}"[:64],
                markdown=dump_page(
                    kind="pitfall",
                    title=text[:120],
                    entity_id=f"pitfall:{pkey}"[:64],
                    norm_key=pkey,
                    source_ids=source_ids,
                    flags=flags,
                    summary=text,
                    evidence_blocks=evidence,
                    forbidden=[text],
                ),
                source_ids=source_ids,
                flags=flags,
            )
            updated.append(row.path)

    # Post: lint + optional neo4j
    if settings.wiki_lint_on_compile and updated:
        try:
            from .lint import lint_paths

            lint_paths(updated)
        except Exception as exc:  # noqa: BLE001
            logger.debug("wiki lint after compile skipped: %s", exc)
    if getattr(settings, "wiki_neo4j_project", False) and updated:
        try:
            from .neo4j_project import project_paths

            project_paths(updated)
        except Exception as exc:  # noqa: BLE001
            logger.debug("wiki neo4j project skipped: %s", exc)

    return {
        "ok": True,
        "source_id": sid,
        "pages": updated,
        "materials": len(materials),
        "chemicals": len(chemicals),
    }
