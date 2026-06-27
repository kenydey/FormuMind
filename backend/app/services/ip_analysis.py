"""IP Compliance Analysis Agent (v0.5).

Searches patents relevant to a formulation's ingredients, then uses an LLM
(when available) to assess novelty and flag potential claim overlaps. Falls
back to keyword-matching when no LLM is configured.
"""
from __future__ import annotations

import re

from ..domain.schemas import (
    Formulation,
    IPAnalysisRequest,
    IPReport,
    PatentRisk,
    ProductDomain,
    Requirement,
    Substrate,
)


def _extract_chem_terms(form: Formulation) -> list[str]:
    """Extract chemical names and CAS-like terms from ingredient names."""
    terms = []
    for ing in form.ingredients:
        if ing.weight_pct > 0.5:  # skip negligible amounts
            terms.append(ing.name)
    return terms


def _search_relevant_patents(
    terms: list[str],
    domain: ProductDomain,
    limit: int = 10,
) -> list:
    """Search patents relevant to the formulation using expanded multi-source retrieval."""
    from . import literature
    from .deep_research.query_expander import prepare_search_queries

    req = Requirement(domain=domain, substrate=Substrate.carbon_steel)
    query = " ".join(terms)
    sq = prepare_search_queries(query)
    patents = literature.search_patents(
        req,
        limit=limit,
        query=sq.patent_q or query,
        ipc_codes=sq.ipc_codes,
        chinese_query=sq.chinese_q,
    )

    # Score patents by term overlap (TF-IDF-like keyword relevance)
    def _score(ev) -> float:
        text = (ev.title + " " + ev.snippet).lower()
        hits = sum(1 for t in terms if t.lower().split("(")[0].strip() in text)
        return ev.relevance * 0.6 + (hits / max(1, len(terms))) * 0.4

    return sorted(patents, key=_score, reverse=True)


def _keyword_risk(term: str, patent_text: str) -> str:
    """Assign risk level based on keyword overlap density."""
    text = patent_text.lower()
    name = term.lower().split("(")[0].strip()
    # Remove common stop words
    words = [w for w in re.split(r"\W+", name) if len(w) > 3]
    hits = sum(1 for w in words if w in text)
    if hits >= len(words) and words:
        return "high"
    if hits >= max(1, len(words) // 2):
        return "medium"
    return "low"


def _build_ip_prompt(form: Formulation, patents: list) -> str:
    ingredients_list = "\n".join(
        f"  - {ing.name} ({ing.weight_pct:.1f} wt%)" for ing in form.ingredients
    )
    patents_text = "\n\n".join(
        f"Patent {p.identifier}: {p.title}\n{p.snippet}" for p in patents[:6]
    )
    return f"""You are a patent analyst specializing in coating and surface treatment chemistry.

Formulation: {form.name}
Ingredients:
{ingredients_list}

Relevant patents (abbreviated):
{patents_text}

Task: Analyze whether this formulation may overlap with any of the above patents.
Return a JSON object with these exact keys:
{{
  "novelty_score": <float 0.0-1.0, where 0=likely infringes, 1=highly novel>,
  "risks": [
    {{
      "patent_id": "<patent identifier>",
      "title": "<patent title>",
      "risk": "<high|medium|low>",
      "claim_overlap": "<brief description of overlap>",
      "recommendation": "<suggested design-around or action>"
    }}
  ],
  "whitespace_hints": ["<area of technical novelty not covered by above patents>"]
}}

Respond with only valid JSON.
"""


def _offline_keyword_analysis(form: Formulation, patents: list) -> IPReport:
    """Keyword-based offline fallback when LLM is unavailable."""
    risks: list[PatentRisk] = []
    for patent in patents[:8]:
        patent_text = patent.title + " " + patent.snippet
        # Check each ingredient against the patent text
        max_risk = "low"
        overlap_terms = []
        for ing in form.ingredients:
            if ing.weight_pct < 0.5:
                continue
            risk = _keyword_risk(ing.name, patent_text)
            if risk == "high":
                max_risk = "high"
                overlap_terms.append(ing.name)
            elif risk == "medium" and max_risk != "high":
                max_risk = "medium"
                overlap_terms.append(ing.name)

        if max_risk in ("high", "medium"):
            risks.append(PatentRisk(
                patent_id=patent.identifier,
                title=patent.title,
                risk=max_risk,
                claim_overlap=f"Ingredient overlap: {', '.join(overlap_terms) or 'see text'}",
                recommendation="Review patent claims; consider varying concentrations or substitutes.",
            ))

    novelty = max(0.1, 1.0 - len([r for r in risks if r.risk == "high"]) * 0.3
                  - len([r for r in risks if r.risk == "medium"]) * 0.1)

    whitespace: list[str] = []
    domain_hints = {
        ProductDomain.anticorrosion_coating: [
            "Low-VOC waterborne formulations with bio-based inhibitors",
            "Nano-pigment dispersion for improved barrier performance",
        ],
        ProductDomain.degreaser: [
            "Enzyme-based degreaser systems for mild steel",
            "Concentrated tablet-form alkaline cleaner",
        ],
        ProductDomain.surface_treatment: [
            "Rare-earth-free conversion coatings for aluminum",
            "One-step phosphating + passivation baths",
        ],
    }
    whitespace = domain_hints.get(form.domain, ["Evaluate adjacent technologies for novelty."])

    return IPReport(
        formulation_name=form.name,
        novelty_score=round(novelty, 2),
        risks=risks,
        whitespace_hints=whitespace,
        raw_patents_searched=len(patents),
        engine="offline-keyword",
    )


def _llm_analysis(form: Formulation, patents: list) -> IPReport | None:
    """LLM-based analysis; returns None if LLM is unavailable."""
    try:
        from . import llm as llm_service

        data = llm_service.complete_json(_build_ip_prompt(form, patents))
        if not data:
            return None

        risks = [
            PatentRisk(
                patent_id=r.get("patent_id", "unknown"),
                title=r.get("title", ""),
                risk=r.get("risk", "unknown"),
                claim_overlap=r.get("claim_overlap", ""),
                recommendation=r.get("recommendation", ""),
            )
            for r in data.get("risks", [])
        ]
        return IPReport(
            formulation_name=form.name,
            novelty_score=float(data.get("novelty_score", 0.5)),
            risks=risks,
            whitespace_hints=data.get("whitespace_hints", []),
            raw_patents_searched=len(patents),
            engine="llm",
        )
    except Exception:
        return None


def analyze_ip_risk(req: IPAnalysisRequest) -> IPReport:
    """Main entry point: run IP landscape analysis for a formulation.

    Uses LLM when available; falls back to offline keyword matching.
    """
    form = req.formulation
    terms = _extract_chem_terms(form)
    patents = _search_relevant_patents(terms, form.domain, limit=req.limit_patents)

    result = _llm_analysis(form, patents)
    if result is not None:
        return result
    return _offline_keyword_analysis(form, patents)
