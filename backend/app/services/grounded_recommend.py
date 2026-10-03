"""Post-LLM grounding checks for recommended formulas (Sprint 2 + Phase B R-4a)."""
from __future__ import annotations

import re

from ..domain.chemistry import normalize_role
from ..domain.knowledge import RAW_MATERIALS
from ..domain.schemas import Evidence, RecommendedFormula, RecommendedFormulaComponent

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)
_CAS_RE = re.compile(r"\b(\d{2,7}-\d{2}-\d)\b")


# Commodity ingredient classes. They are well known (a missing citation says little
# about whether they exist) and structurally essential — they set solids / VOC and
# make the weights add up — so strict grounding keeps them, tagged "medium", instead
# of dropping them. Dropping them used to turn a plausible epoxy primer into a 72 %
# recipe with no solvent, filler or additive whose predicted VOC was 0 g/L (which the
# "minimize VOC" objective then rewarded). Specialty roles (inhibitor, resin, hardener,
# active, accelerator, …) are where hallucinated chemicals matter and stay strict.
_COMMODITY_ROLES = frozenset({"solvent", "filler", "pigment"})
# "additive" is only commodity when the *name* says it is a generic additive class.
_GENERIC_ADDITIVE_HINTS = (
    "defoam", "antifoam", "anti-foam", "wetting", "dispers", "levelling", "leveling",
    "rheolog", "thicken", "biocide", "preservative", "uv absorber", "flow agent",
    "消泡", "润湿", "分散", "流平", "增稠", "防腐", "紫外",
)


def _is_commodity(comp: RecommendedFormulaComponent) -> bool:
    role = normalize_role(comp.component_type or "")
    if role in _COMMODITY_ROLES:
        return True
    if role == "additive":
        name = f"{comp.name} {comp.zh_name or ''}".lower()
        return any(hint in name for hint in _GENERIC_ADDITIVE_HINTS)
    return False


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text or "") if len(t) > 1}


def _extract_cas(text: str) -> set[str]:
    try:
        from .chem_extract import extract_cas

        return {c.lower() for c in extract_cas(text or "")}
    except Exception:
        return {m.group(1).lower() for m in _CAS_RE.finditer(text or "")}


_PAREN_RE = re.compile(r"[(（]([^()（）]{3,})[)）]")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _name_variants(comp: RecommendedFormulaComponent) -> list[str]:
    """Spellings of a component worth searching for verbatim in evidence text.

    The full name, the name without a parenthetical, the parenthetical itself
    (``"… epoxy resin (DGEBA)"`` → ``dgeba``) and the Chinese name. Anything that
    is too short to be specific (``Zn``, ``PU``) is skipped.
    """
    out: list[str] = []
    for raw in (comp.name, comp.zh_name):
        text = (raw or "").strip().lower()
        if not text:
            continue
        candidates = [text, _PAREN_RE.sub("", text).strip()]
        candidates += [m.group(1).strip() for m in _PAREN_RE.finditer(text)]
        for c in candidates:
            cjk = bool(_CJK_RE.search(c))
            min_len = 2 if cjk else 4
            if len(c) >= min_len and c not in out:
                out.append(c)
            # "Silanes" in the recipe, "silane" in the evidence.
            if not cjk and len(c) >= 6 and c.endswith("s") and c[:-1] not in out:
                out.append(c[:-1])
    return out


def _verbatim_refs(
    comp: RecommendedFormulaComponent, texts: list[tuple[str, str]]
) -> list[str]:
    """Identifiers of evidence whose text states this component's name verbatim.

    Token overlap cannot do this: a one-word name ("Benzotriazole", "Silane") can
    never reach the two-token bar, and Chinese text is not tokenised at all (a
    whole run of 汉字 is one token), so an evidence snippet that literally named the
    ingredient still left it "low" and — under strict grounding — dropped.
    Latin variants match on word boundaries (plural ``s``/``es`` allowed) so
    ``acid`` does not hit ``acidic``; CJK variants match as substrings.
    """
    variants = _name_variants(comp)
    if not variants:
        return []
    patterns = []
    for v in variants:
        if _CJK_RE.search(v):
            patterns.append(re.compile(re.escape(v)))
        else:
            patterns.append(re.compile(rf"(?<![a-z0-9]){re.escape(v)}(?:e?s)?(?![a-z0-9])"))
    hits: list[str] = []
    for ident, blob in texts:
        if any(p.search(blob) for p in patterns) and ident not in hits:
            hits.append(ident)
    return hits[:3]


def _evidence_texts(evidence: list[Evidence]) -> list[tuple[str, str]]:
    """``(identifier, lower-cased title + snippet)`` per evidence item."""
    out: list[tuple[str, str]] = []
    for ev in evidence:
        ident = (ev.identifier or ev.title or "").strip()
        blob = f"{ev.title or ''} {ev.snippet or ''}".lower()
        if ident and blob.strip():
            out.append((ident, blob))
    return out


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
    texts: list[tuple[str, str]] | None = None,
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

    # The evidence states the component by name: that is support, however short
    # the name is or whichever script it is written in.
    verbatim = _verbatim_refs(comp, texts or [])
    if verbatim:
        return comp.model_copy(
            update={"evidence_refs": refs or verbatim, "grounding_confidence": "high"}
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

    # Unverified but commodity: keep it (flagged), do not drop it.
    if conf == "low" and _is_commodity(comp):
        conf = "medium"

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
    strict: bool = True,
) -> tuple[list[RecommendedFormula], list[str]]:
    """Verify components appear in evidence or material catalog.

    ``prefer_materials_catalog`` is a *soft* bias: catalog hits get stronger
    grounding / stable sort priority. It never drops formulas whose components
    are outside the catalog.

    ``strict`` (default True): drop low-confidence components instead of only
    tagging them. A formula whose every component is low-confidence is dropped
    entirely. ``strict=False`` restores the legacy tag-only behaviour.
    """
    if not formulas:
        return [], []
    corpus, corpus_cas, id_map = _evidence_corpus(evidence)
    texts = _evidence_texts(evidence)
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
                texts=texts,
            )
            for c in rec.components
        ]
        low = [c.name for c in comps if c.grounding_confidence == "low"]
        medium = [c.name for c in comps if c.grounding_confidence == "medium"]
        form_warnings = list(rec.warnings)
        if medium:
            form_warnings.append(
                f"常规成分未在证据/材料库中核实（已保留，请人工确认）: {', '.join(medium[:5])}"
            )
        if strict and low:
            # A-5: drop instead of only tagging. Never renormalize weight_pct:
            # the remaining recipe is honest about what was removed.
            dropped_pct = sum(
                c.weight_pct or 0.0 for c in comps if c.grounding_confidence == "low"
            )
            kept = [c for c in comps if c.grounding_confidence != "low"]
            form_warnings.append(
                f"已剔除低可信度成分（证据未覆盖）: {', '.join(low[:5])}"
                f"（共剔除 {dropped_pct:.1f}%）"
            )
            warnings.append(
                f"{rec.name}: 剔除 {len(low)} 个低可信度成分（{', '.join(low[:5])}）"
            )
            if not kept:
                warnings.append(f"{rec.name}: 全部分成分均低可信度，整个配方已剔除")
                continue
            comps = kept
        elif low:
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
