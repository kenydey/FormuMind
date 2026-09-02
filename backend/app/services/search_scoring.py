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
    if any(k in s for k in ("uspto", "epo", "patent", "cnipa")):
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
