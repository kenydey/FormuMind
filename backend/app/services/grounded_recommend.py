"""Post-LLM grounding checks for recommended formulas (Sprint 2 + Phase B R-4a)."""
from __future__ import annotations

import re

from ..domain.knowledge import RAW_MATERIALS
from ..domain.schemas import Evidence, RecommendedFormula, RecommendedFormulaComponent

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)
_CAS_RE = re.compile(r"\b(\d{2,7}-\d{2}-\d)\b")


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text or "") if len(t) > 1}


def _extract_cas(text: str) -> set[str]:
    try:
        from .chem_extract import extract_cas

        return {c.lower() for c in extract_cas(text or "")}
    except Exception:
        return {m.group(1).lower() for m in _CAS_RE.finditer(text or "")}


def _evidence_corpus(evidence: list[Evidence]) -> tuple[set[str], set[str], dict[str, str]]:
    """Return (token set, CAS set, identifier map)."""
    tokens: set[str] = set()
    cas_set: set[str] = set()
    id_map: dict[str, str] = {}
    for ev in evidence:
        ident = (ev.identifier or ev.title or "").strip()
        if ident:
            id_map[ident.lower()] = ident
        blob = f"{ev.title} {ev.snippet} {ev.identifier}"
        tokens |= _tokens(blob)
        cas_set |= _extract_cas(blob)
    return tokens, cas_set, id_map


def _component_in_catalog(
    comp: RecommendedFormulaComponent,
    catalog: set[str],
    catalog_cas: set[str],
) -> bool:
    name_toks = {t for t in _tokens(comp.name) | _tokens(comp.zh_name or "") if len(t) >= 5}
    comp_cas = (comp.cas_no or "").strip().lower()
    if comp_cas and comp_cas in catalog_cas:
        return True
    overlap = name_toks & catalog
    return len(overlap) >= 2 or any(len(t) >= 8 for t in overlap)


def _catalog_tokens() -> set[str]:
    out: set[str] = set()
    for name, spec in RAW_MATERIALS.items():
        out |= {t for t in _tokens(name) if len(t) >= 5}
        if spec.get("cas_no"):
            out.add(str(spec["cas_no"]).lower())
        if spec.get("zh_name"):
            out |= {t for t in _tokens(str(spec["zh_name"])) if len(t) >= 5}
    return out


def _catalog_cas() -> set[str]:
    return {
        str(spec["cas_no"]).lower()
        for spec in RAW_MATERIALS.values()
        if spec.get("cas_no")
    }


def _catalog_availability_bonus(name: str, cas_no: str | None) -> float:
    """Soft ranking bonus for in-stock catalog hits (prefer_materials_catalog)."""
    needle = (name or "").strip().lower()
    cas = (cas_no or "").strip().lower()
    for mat_name, spec in RAW_MATERIALS.items():
        hit = needle and needle in mat_name.lower()
        if cas and str(spec.get("cas_no") or "").lower() == cas:
            hit = True
        if not hit:
            zh = str(spec.get("zh_name") or "").lower()
            if needle and needle in zh:
                hit = True
        if not hit:
            continue
        avail = str(spec.get("availability") or "in_stock")
        if avail == "in_stock":
            return 1.0
        if avail == "restricted":
            return 0.4
        return 0.1
    return 0.0


def _match_evidence_ids(name: str, corpus: set[str], id_map: dict[str, str]) -> list[str]:
    name_toks = _tokens(name)
    if not name_toks:
        return []
    hits: list[str] = []
    for ident_key, ident in id_map.items():
        if any(t in ident_key or t in corpus for t in name_toks if len(t) > 2):
            if ident not in hits:
                hits.append(ident)
    if name_toks & corpus:
        for ident in id_map.values():
            if ident not in hits and any(t in ident.lower() for t in name_toks):
                hits.append(ident)
    return hits[:3]


def _ground_component(
    comp: RecommendedFormulaComponent,
    corpus: set[str],
    corpus_cas: set[str],
    id_map: dict[str, str],
    catalog: set[str],
    catalog_cas: set[str],
    *,
    prefer_catalog: bool = False,
) -> RecommendedFormulaComponent:
    refs = list(comp.evidence_refs or [])
    raw_toks = _tokens(comp.name) | _tokens(comp.zh_name or "")
    name_toks = {t for t in raw_toks if len(t) >= 5}
    comp_cas = (comp.cas_no or "").strip().lower()

    if comp_cas and (comp_cas in corpus_cas or comp_cas in catalog_cas):
        if not refs:
            refs = _match_evidence_ids(comp.name, corpus, id_map)
        return comp.model_copy(
            update={"evidence_refs": refs, "grounding_confidence": "high"}
        )

    if not refs:
        refs = _match_evidence_ids(comp.name, corpus, id_map)

    catalog_overlap = name_toks & catalog
    # One shortish role-like token (e.g. "additive") is not enough to ground.
    catalog_hit = len(catalog_overlap) >= 2 or any(len(t) >= 8 for t in catalog_overlap)
    token_hit = bool(raw_toks & corpus)
    strong_token_hit = token_hit and len(raw_toks & corpus) >= 2

    if catalog_hit or (refs and strong_token_hit):
        conf = "high"
    elif prefer_catalog and catalog_hit:
        conf = "high"
    elif refs or token_hit:
        conf = "low"
    else:
        conf = "low"

    # Soft prefer: catalog hits escalate low→high when prefer is on and evidence is weak.
    if prefer_catalog and catalog_hit and conf != "high":
        conf = "high"

    return comp.model_copy(update={"evidence_refs": refs, "grounding_confidence": conf})


def _formula_catalog_score(rec: RecommendedFormula, catalog: set[str], catalog_cas: set[str]) -> float:
    comps = list(rec.components or [])
    if not comps:
        return 0.0
    hits = 0.0
    for c in comps:
        if _component_in_catalog(c, catalog, catalog_cas):
            hits += 1.0 + 0.25 * _catalog_availability_bonus(c.name, c.cas_no)
    return hits / len(comps)


def ground_recommended_formulas(
    formulas: list[RecommendedFormula],
    evidence: list[Evidence],
    *,
    prefer_materials_catalog: bool = False,
) -> tuple[list[RecommendedFormula], list[str]]:
    """Verify components appear in evidence or material catalog; tag low-confidence rows.

    ``prefer_materials_catalog`` is a *soft* bias: catalog hits get stronger
    grounding / stable sort priority. It never drops formulas whose components
    are outside the catalog.
    """
    if not formulas:
        return [], []
    corpus, corpus_cas, id_map = _evidence_corpus(evidence)
    catalog = _catalog_tokens()
    catalog_cas = _catalog_cas()
    warnings: list[str] = []
    out: list[RecommendedFormula] = []

    for rec in formulas:
        comps = [
            _ground_component(
                c,
                corpus,
                corpus_cas,
                id_map,
                catalog,
                catalog_cas,
                prefer_catalog=prefer_materials_catalog,
            )
            for c in rec.components
        ]
        low = [c.name for c in comps if c.grounding_confidence == "low"]
        form_warnings = list(rec.warnings)
        if low:
            form_warnings.append(
                f"低可信度成分（证据未覆盖）: {', '.join(low[:5])}"
            )
            warnings.append(f"{rec.name}: {len(low)} 个成分缺少文献/专利依据")
        if prefer_materials_catalog:
            in_cat = sum(
                1 for c in comps if _component_in_catalog(c, catalog, catalog_cas)
            )
            form_warnings.append(
                f"优先材料库：{in_cat}/{len(comps)} 个成分命中全局材料库（未命中者仍保留）"
            )
        out.append(rec.model_copy(update={"components": comps, "warnings": form_warnings}))

    if prefer_materials_catalog and len(out) > 1:
        # Stable soft reorder: higher catalog coverage first; never filter.
        indexed = list(enumerate(out))
        indexed.sort(
            key=lambda iv: (
                -_formula_catalog_score(iv[1], catalog, catalog_cas),
                iv[0],
            )
        )
        out = [rec for _, rec in indexed]
        warnings.append("已按材料库命中率对推荐结果软排序（未排除库外材料）")

    return out, warnings
