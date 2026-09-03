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


def _catalog_tokens() -> set[str]:
    out: set[str] = set()
    for name, spec in RAW_MATERIALS.items():
        out |= _tokens(name)
        if spec.get("cas_no"):
            out.add(str(spec["cas_no"]).lower())
        if spec.get("zh_name"):
            out |= _tokens(str(spec["zh_name"]))
    return out


def _catalog_cas() -> set[str]:
    return {
        str(spec["cas_no"]).lower()
        for spec in RAW_MATERIALS.values()
        if spec.get("cas_no")
    }


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
) -> RecommendedFormulaComponent:
    refs = list(comp.evidence_refs or [])
    name_toks = _tokens(comp.name) | _tokens(comp.zh_name or "")
    comp_cas = (comp.cas_no or "").strip().lower()

    if comp_cas and (comp_cas in corpus_cas or comp_cas in catalog_cas):
        if not refs:
            refs = _match_evidence_ids(comp.name, corpus, id_map)
        return comp.model_copy(
            update={"evidence_refs": refs, "grounding_confidence": "high"}
        )

    if not refs:
        refs = _match_evidence_ids(comp.name, corpus, id_map)

    catalog_hit = bool(name_toks & catalog)
    token_hit = bool(name_toks & corpus)
    strong_token_hit = token_hit and len(name_toks & corpus) >= 2

    if catalog_hit or (refs and strong_token_hit):
        conf = "high"
    elif refs or token_hit:
        conf = "low"
    else:
        conf = "low"

    return comp.model_copy(update={"evidence_refs": refs, "grounding_confidence": conf})


def ground_recommended_formulas(
    formulas: list[RecommendedFormula],
    evidence: list[Evidence],
) -> tuple[list[RecommendedFormula], list[str]]:
    """Verify components appear in evidence or material catalog; tag low-confidence rows."""
    if not formulas:
        return [], []
    corpus, corpus_cas, id_map = _evidence_corpus(evidence)
    catalog = _catalog_tokens()
    catalog_cas = _catalog_cas()
    warnings: list[str] = []
    out: list[RecommendedFormula] = []

    for rec in formulas:
        comps = [
            _ground_component(c, corpus, corpus_cas, id_map, catalog, catalog_cas)
            for c in rec.components
        ]
        low = [c.name for c in comps if c.grounding_confidence == "low"]
        form_warnings = list(rec.warnings)
        if low:
            form_warnings.append(
                f"低可信度成分（证据未覆盖）: {', '.join(low[:5])}"
            )
            warnings.append(f"{rec.name}: {len(low)} 个成分缺少文献/专利依据")
        out.append(rec.model_copy(update={"components": comps, "warnings": form_warnings}))

    return out, warnings
