"""Post-LLM grounding checks for recommended formulas (Sprint 2)."""
from __future__ import annotations

import re

from ..domain.knowledge import RAW_MATERIALS
from ..domain.schemas import Evidence, RecommendedFormula, RecommendedFormulaComponent

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text or "") if len(t) > 1}


def _evidence_corpus(evidence: list[Evidence]) -> tuple[set[str], dict[str, str]]:
    """Return (token set, identifier map for substring hits)."""
    tokens: set[str] = set()
    id_map: dict[str, str] = {}
    for ev in evidence:
        ident = (ev.identifier or ev.title or "").strip()
        if ident:
            id_map[ident.lower()] = ident
        blob = f"{ev.title} {ev.snippet} {ev.identifier}"
        tokens |= _tokens(blob)
    return tokens, id_map


def _catalog_tokens() -> set[str]:
    out: set[str] = set()
    for name, spec in RAW_MATERIALS.items():
        out |= _tokens(name)
        if spec.get("cas_no"):
            out.add(str(spec["cas_no"]).lower())
    return out


def _match_evidence_ids(name: str, corpus: set[str], id_map: dict[str, str]) -> list[str]:
    name_toks = _tokens(name)
    if not name_toks:
        return []
    hits: list[str] = []
    for ident_key, ident in id_map.items():
        if any(t in ident_key or t in corpus for t in name_toks if len(t) > 2):
            if ident not in hits:
                hits.append(ident)
    # Direct token overlap with evidence text
    if name_toks & corpus:
        for ident in id_map.values():
            if ident not in hits and any(t in ident.lower() for t in name_toks):
                hits.append(ident)
    return hits[:3]


def _ground_component(
    comp: RecommendedFormulaComponent,
    corpus: set[str],
    id_map: dict[str, str],
    catalog: set[str],
) -> RecommendedFormulaComponent:
    refs = list(comp.evidence_refs or [])
    name_toks = _tokens(comp.name) | _tokens(comp.zh_name or "")
    in_corpus = bool(name_toks & corpus) or bool(name_toks & catalog)
    if comp.cas_no and comp.cas_no.lower() in corpus:
        in_corpus = True
    if not refs:
        refs = _match_evidence_ids(comp.name, corpus, id_map)
    conf = comp.grounding_confidence
    if not in_corpus and not refs:
        conf = "low"
    elif in_corpus or refs:
        conf = "high"
    return comp.model_copy(update={"evidence_refs": refs, "grounding_confidence": conf})


def ground_recommended_formulas(
    formulas: list[RecommendedFormula],
    evidence: list[Evidence],
) -> tuple[list[RecommendedFormula], list[str]]:
    """Verify components appear in evidence or material catalog; tag low-confidence rows."""
    if not formulas:
        return [], []
    corpus, id_map = _evidence_corpus(evidence)
    catalog = _catalog_tokens()
    warnings: list[str] = []
    out: list[RecommendedFormula] = []

    for rec in formulas:
        comps = [
            _ground_component(c, corpus, id_map, catalog) for c in rec.components
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
