"""Chemical-entity relevance boosts for live search Evidence rows."""
from __future__ import annotations

from ..domain.schemas import Evidence


def query_chem_context(query: str) -> dict:
    """Extract CAS / formula / SMILES signals from a search query."""
    ctx: dict = {"cas": set(), "formulas": set(), "smiles": []}
    if not (query or "").strip():
        return ctx
    try:
        from .chem_extract import extract_cas, extract_formulas, extract_smiles

        ctx["cas"] = set(extract_cas(query))
        ctx["formulas"] = set(extract_formulas(query))
        ctx["smiles"] = [s["canonical"] for s in extract_smiles(query)]
    except Exception:
        pass
    return ctx


def evidence_authority_bonus(ev: Evidence) -> float:
    """C: 引文权威度加成 — 专利/权威文献 > 种子语料 > 网络聚合。

    source 值域（literature.py 实测）：USPTO（官方专利）、seed（离线
    示例语料）、Tavily/SerpAPI/duckduckgo（网络聚合，junk-prone）。
    权威度用于证据排序加权——LLM 合成时高权威源优先被引用。
    """
    s = (ev.source or "").lower()
    if any(k in s for k in ("uspto", "epo", "patent", "cnipa", "surechembl")):
        return 0.12  # 官方专利库
    if any(k in s for k in ("arxiv", "scholar", "semantic", "literature", "paper")):
        return 0.08  # 学术文献
    if "seed" in s:
        return 0.04  # 离线精选种子语料
    return 0.0  # web/tavily/serpapi/duck — 不加成（反被 junk 过滤）


def evidence_entity_boost(ev: Evidence, qctx: dict) -> float:
    """Additive score bump when evidence text shares query chemical entities."""
    if not any(qctx.get(k) for k in ("cas", "formulas", "smiles")):
        return 0.0
    blob = f"{ev.title} {ev.snippet} {ev.identifier}".lower()
    boost = 0.0
    for cas in qctx.get("cas") or []:
        if str(cas).lower() in blob:
            boost += 0.3
            break
    for formula in qctx.get("formulas") or []:
        if str(formula).lower() in blob:
            boost += 0.15
            break
    return min(boost, 0.45)


def domain_match_bonus(ev: Evidence) -> float:
    """P0: DomainSearchProfile match weight (strong↑ / none↓)."""
    try:
        from .domain_tagging import domain_match_bonus as _bonus
        return _bonus(ev)
    except Exception:
        return 0.0


def age_normalized_citation_score(ev: Evidence) -> float:
    """W4-6 · P0-19: citedBy 按论文年龄归一化 —— 新论文不被老论文淹没。

    用引用速率（次/年）而非引用总量参与排序融合：
    ``rate = cited_by / max(1, age_years)``，再经 ``log1p`` 压到小加成。
    无 cited_by/pub_year 时返回 0.0（不惩罚未知）。
    """
    import math
    from datetime import date

    cited = getattr(ev, "cited_by", None)
    if cited is None:
        return 0.0
    try:
        cited = int(cited)
    except (TypeError, ValueError):
        return 0.0
    if cited <= 0:
        return 0.0
    current_year = date.today().year
    age_years = 1
    year = getattr(ev, "pub_year", None)
    if year is not None:
        try:
            age_years = max(1, current_year - int(year) + 1)
        except (TypeError, ValueError):
            pass
    rate = cited / age_years
    # rate=10 → ~0.048；rate=100 → ~0.092；上限 0.12（与 authority bonus 同量级）
    return min(0.12, 0.02 * math.log1p(rate))


def search_deny_penalty(ev: Evidence, domain=None, *, negative_terms=None) -> float:
    """Top-5 #2: retrieve-time deny term penalty."""
    try:
        from .domain_tagging import search_deny_penalty as _pen
        return _pen(ev, domain, negative_terms=negative_terms)
    except Exception:
        return 0.0

